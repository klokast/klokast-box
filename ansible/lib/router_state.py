"""Fixed router state copy primitive, for a stopped pair in an isolated guest.

The caller mounts a consistent source read-only and the destination read-write.
It fences both routers until this function returns and its private receipt is
recorded. No source program, service hook, or account database is executed.
This module does not grant authority, mount disks, or declare compatibility.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

KEY_TYPES = ('rsa', 'ecdsa', 'ed25519')
REQUIRED = {
    'var/lib/dhcpcd/duid': (4096, False),
    'var/lib/dhcpcd/secret': (4096, False),
    'var/lib/misc/dnsmasq.leases': (4 * 1024 * 1024, True),
}
WAN_CACHE = {f'var/lib/dhcpcd/eth0{suffix}': (128 * 1024, False)
            for suffix in ('.lease', '.lease6')}
SSH = {f'{directory}/ssh_host_{kind}_key': (16384, False)
       for directory in ('etc/ssh', 'var/lib/tailscale/ssh') for kind in KEY_TYPES}
IDENTITY = {'var/lib/tailscale/tailscaled.state': (16 * 1024 * 1024, False), **SSH}
ALLOWLIST = dict(REQUIRED)
# WAN caches are only validated and removed from the stopped destination.
PATHS = {**ALLOWLIST, **WAN_CACHE}
READ_PATHS = {**PATHS, **IDENTITY}


class StateError(RuntimeError):
    pass


def parent(root, relative):
    """Open every directory without following links, including the root itself."""
    if relative not in READ_PATHS:
        raise StateError('state path is outside router-state.v2')
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in Path(relative).parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read(root, relative, *, missing=False):
    maximum, empty = READ_PATHS[relative]
    try:
        directory = parent(root, relative)
    except FileNotFoundError:
        if missing:
            return None
        raise StateError('required router state directory is missing') from None
    try:
        try:
            descriptor = os.open(Path(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
        except FileNotFoundError:
            if missing:
                return None
            raise StateError('required router state file is missing: ' + relative) from None
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    not (0 if empty else 1) <= info.st_size <= maximum or
                    stat.S_IMODE(info.st_mode) & 0o7022):
                raise StateError('router state has unsafe type, links, size, or mode: ' + relative)
            if relative in {*IDENTITY, 'var/lib/dhcpcd/secret'} and info.st_mode & 0o077:
                raise StateError('router identity state must be private: ' + relative)
            data = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            if (len(data) != info.st_size or
                    (info.st_size, info.st_mtime_ns, info.st_ctime_ns) !=
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise StateError('router source state changed during an offline copy')
            return data, info
    finally:
        os.close(directory)


def snapshot(root, *, dnsmasq_uid=0, dnsmasq_gid=0, tailscale_gid=0):
    result = {}
    for relative in ALLOWLIST:
        item = read(root, relative, missing=relative not in REQUIRED)
        if item is None:
            continue
        data, info = item
        owners = {(0, 0)}
        if relative == 'var/lib/misc/dnsmasq.leases':
            owners.add((dnsmasq_uid, dnsmasq_gid))
        if (info.st_uid, info.st_gid) not in owners:
            raise StateError('router state ownership is outside the approved service profile')
        result[relative] = item
    return result


def evidence(files):
    return {path: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                   'mtime_ns': info.st_mtime_ns, 'mode': stat.S_IMODE(info.st_mode),
                   'uid': info.st_uid, 'gid': info.st_gid}
            for path, (data, info) in files.items()}


def enrolled_snapshot(root, *, dnsmasq_uid=0, dnsmasq_gid=0, tailscale_gid=0):
    """Observe one generation during cleanup; these bytes are never copied."""
    result = snapshot(root, dnsmasq_uid=dnsmasq_uid, dnsmasq_gid=dnsmasq_gid)
    for relative in {**IDENTITY, **WAN_CACHE}:
        item = read(root, relative, missing=relative != 'var/lib/tailscale/tailscaled.state')
        if item is None:
            continue
        _, info = item
        owners = {(0, 0)}
        if relative.startswith('var/lib/tailscale/'):
            owners.add((0, tailscale_gid))
        if (info.st_uid, info.st_gid) not in owners:
            raise StateError('enrolled router state ownership is outside its service profile')
        result[relative] = item
    for kind in KEY_TYPES:
        if not any(f'{directory}/ssh_host_{kind}_key' in result
                   for directory in ('etc/ssh', 'var/lib/tailscale/ssh')):
            raise StateError('effective Tailscale SSH key is missing: ' + kind)
    return result


def copy_state(source, destination, *, source_id, destination_id, dnsmasq_uid=0, dnsmasq_gid=0,
               destination_dnsmasq_uid=0, destination_dnsmasq_gid=0, tailscale_gid=0,
               destination_tailscale_gid=0, checkpoint=lambda stage: None):
    """Stage all bytes before replacement; restart with the same stopped source.

    Partial output is never bootable evidence. The caller retains its pending
    copy record until this returns. Repeating after interruption overwrites the
    complete fixed set. A changed source identity requires a new transaction.
    Identity paths are outside this operation. Each generation retains its own
    Tailscale and SSH bytes. The private receipt describes DHCP state only.
    """
    if not source_id or not destination_id or source_id == destination_id or os.path.samefile(source, destination):
        raise StateError('state copy requires two distinct recorded disk identities')
    source_files = snapshot(source, dnsmasq_uid=dnsmasq_uid, dnsmasq_gid=dnsmasq_gid, tailscale_gid=tailscale_gid)
    staged, parents = {}, {}
    try:
        # Validate the complete destination set, including disposable WAN
        # caches, before modifying any file. Never open identity directories.
        for relative in PATHS:
            current = read(destination, relative, missing=True)
            if relative in source_files or current is not None:
                parents[relative] = parent(destination, relative)
        checkpoint('validated')
        for relative, (data, info) in source_files.items():
            directory = parents[relative]
            # The source and destination record fixes each temporary name.
            # An interrupted process leaves only these exact resumable files.
            temporary = '.router-state-' + hashlib.sha256(
                json.dumps([source_id, destination_id, relative]).encode()).hexdigest()
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=directory)
            staged_info = os.fstat(descriptor)
            if not stat.S_ISREG(staged_info.st_mode) or staged_info.st_nlink != 1:
                os.close(descriptor)
                raise StateError('recorded router staging path is unsafe')
            staged[relative] = temporary
            with os.fdopen(descriptor, 'wb') as stream:
                os.ftruncate(stream.fileno(), 0)
                stream.write(data)
                stream.flush()
                uid, gid = info.st_uid, info.st_gid
                if relative == 'var/lib/misc/dnsmasq.leases' and (uid, gid) != (0, 0):
                    uid, gid = destination_dnsmasq_uid, destination_dnsmasq_gid
                os.fchown(stream.fileno(), uid, gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
                os.utime(stream.fileno(), ns=(info.st_atime_ns, info.st_mtime_ns))
                os.fsync(stream.fileno())
            os.fsync(directory)
        checkpoint('staged')
        for relative in source_files:
            os.rename(staged[relative], Path(relative).name,
                      src_dir_fd=parents[relative], dst_dir_fd=parents[relative])
            staged.pop(relative)
            os.fsync(parents[relative])
            checkpoint('installed:' + relative)
        copied = snapshot(destination, dnsmasq_uid=destination_dnsmasq_uid, dnsmasq_gid=destination_dnsmasq_gid,
                          tailscale_gid=destination_tailscale_gid)
        expected = evidence(source_files)
        for path, item in expected.items():
            if path == 'var/lib/misc/dnsmasq.leases' and (item['uid'], item['gid']) != (0, 0):
                item.update(uid=destination_dnsmasq_uid, gid=destination_dnsmasq_gid)
        if evidence(copied) != expected:
            raise StateError('router state copy failed final byte and metadata verification')
        for relative in WAN_CACHE:
            if relative in parents:
                os.unlink(Path(relative).name, dir_fd=parents[relative])
                os.fsync(parents[relative])
                checkpoint('removed:' + relative)
            if read(destination, relative, missing=True) is not None:
                raise StateError('destination retains a WAN lease cache')
        checkpoint('verified')
        record = {'kind': 'klokast.router-state-copy.v2', 'source': source_id,
                  'destination': destination_id, 'files': expected, 'complete': True,
                  'wan_cache_absent': list(WAN_CACHE)}
        record['receipt_sha256'] = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return record
    finally:
        for relative, name in staged.items():
            os.unlink(name, dir_fd=parents[relative])
        for descriptor in parents.values():
            os.close(descriptor)
