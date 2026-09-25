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
    'var/lib/tailscale/tailscaled.state': (16 * 1024 * 1024, False),
    'var/lib/dhcpcd/duid': (4096, False),
    'var/lib/dhcpcd/secret': (4096, False),
    'var/lib/misc/dnsmasq.leases': (4 * 1024 * 1024, True),
}
OPTIONAL = {f'var/lib/dhcpcd/eth0{suffix}': (128 * 1024, False)
            for suffix in ('.lease', '.lease6')}
SSH = {f'{directory}/ssh_host_{kind}_key': (16384, False)
       for directory in ('etc/ssh', 'var/lib/tailscale/ssh') for kind in KEY_TYPES}
ALLOWLIST = {**REQUIRED, **OPTIONAL, **SSH}


class StateError(RuntimeError):
    pass


def parent(root, relative):
    """Open every directory without following links, including the root itself."""
    if relative not in ALLOWLIST:
        raise StateError('state path is outside router-state.v1')
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
    maximum, empty = ALLOWLIST[relative]
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
            if relative not in OPTIONAL and relative != 'var/lib/misc/dnsmasq.leases' and info.st_mode & 0o077:
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


def snapshot(root, *, dnsmasq_uid=0, dnsmasq_gid=0):
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
    # Tailscale selects a system key first, per type. Copy the effective key
    # only; a conflicting destination key must block copying until recorded
    # synthetic bootstrap state is removed by its owner.
    for kind in KEY_TYPES:
        system = f'etc/ssh/ssh_host_{kind}_key'
        fallback = f'var/lib/tailscale/ssh/ssh_host_{kind}_key'
        if system not in result and fallback not in result:
            raise StateError('effective Tailscale SSH key is missing: ' + kind)
        if system in result:
            result.pop(fallback, None)
    return result


def evidence(files):
    return {path: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                   'mtime_ns': info.st_mtime_ns, 'mode': stat.S_IMODE(info.st_mode),
                   'uid': info.st_uid, 'gid': info.st_gid}
            for path, (data, info) in files.items()}


def copy_state(source, destination, *, source_id, destination_id, dnsmasq_uid=0, dnsmasq_gid=0,
               destination_dnsmasq_uid=0, destination_dnsmasq_gid=0, checkpoint=lambda stage: None):
    """Stage all bytes before replacement; restart with the same stopped source.

    Partial output is never bootable evidence. The caller retains its pending
    copy record until this returns. Repeating after interruption overwrites the
    complete fixed set. A changed source identity requires a new transaction.
    The returned receipt must stay on box storage because it describes keys.
    """
    if not source_id or not destination_id or source_id == destination_id or os.path.samefile(source, destination):
        raise StateError('state copy requires two distinct recorded disk identities')
    source_files = snapshot(source, dnsmasq_uid=dnsmasq_uid, dnsmasq_gid=dnsmasq_gid)
    staged, parents = {}, {}
    try:
        # Validate all destination parents and all existing fixed paths before
        # any copy. Refuse unknown bootstrap keys rather than deleting them.
        for relative in ALLOWLIST:
            current = read(destination, relative, missing=True)
            if relative not in source_files and current is not None:
                raise StateError('destination has conflicting state; remove only recorded synthetic files first')
            if relative in source_files:
                parents[relative] = parent(destination, relative)
        checkpoint('validated')
        for relative, (data, info) in source_files.items():
            directory = parents[relative]
            temporary = '.router-state-' + os.urandom(12).hex()
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            staged[relative] = temporary
            with os.fdopen(descriptor, 'wb') as stream:
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
        copied = snapshot(destination, dnsmasq_uid=destination_dnsmasq_uid, dnsmasq_gid=destination_dnsmasq_gid)
        expected = evidence(source_files)
        for path, item in expected.items():
            if path == 'var/lib/misc/dnsmasq.leases' and (item['uid'], item['gid']) != (0, 0):
                item.update(uid=destination_dnsmasq_uid, gid=destination_dnsmasq_gid)
        if evidence(copied) != expected:
            raise StateError('router state copy failed final byte and metadata verification')
        checkpoint('verified')
        record = {'kind': 'klokast.router-state-copy.v1', 'source': source_id,
                  'destination': destination_id, 'files': expected, 'complete': True}
        record['receipt_sha256'] = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return record
    finally:
        for relative, name in staged.items():
            os.unlink(name, dir_fd=parents[relative])
        for descriptor in parents.values():
            os.close(descriptor)


def generic_absence(root):
    """Native filesystem check on a disposable template inside the test guest."""
    root = Path(root)
    forbidden = list(ALLOWLIST) + ['etc/hostname', 'etc/machine-id', 'var/lib/dbus/machine-id',
                                  'root/.ssh/authorized_keys', 'etc/klokast/app-resources',
                                  'etc/klokast/platform-resources', 'var/lib/tailscale/tpm-sealed',
                                  'var/lib/tailscale/tka']
    for relative in forbidden:
        path = root / relative
        # Check ancestors too; a dangling symlink is identity or unknown state.
        if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
            raise StateError('generic router template has an unsafe state path')
        if path.exists():
            raise StateError('generic router template contains machine state: ' + relative)
    for directory in ('etc/ssh', 'var/lib/tailscale', 'var/lib/dhcpcd'):
        path = root / directory
        if path.is_dir():
            for child in path.iterdir():
                if directory == 'var/lib/tailscale' and child.name == 'ssh' and child.is_dir() and not any(child.iterdir()):
                    continue
                if directory == 'etc/ssh' and not child.name.startswith('ssh_host_'):
                    continue
                raise StateError('generic router template contains service identity or lease state')
    return True
