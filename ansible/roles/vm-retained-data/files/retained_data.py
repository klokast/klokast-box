"""Offline retained-data copy primitive for a disposable, networkless Xen VM.

The caller supplies approved physical mappings and proves backup, fencing,
writer shutdown, disk ownership, and source completeness. This module cannot
authorize adoption, select a release, attach disks, or start services.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import time

SOURCE = Path('/source')
TARGET = Path('/retained')
MAX_ENTRIES = 100000
RESERVE = 128 * 1024 * 1024


class CopyError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise CopyError('retained-data operation exceeded its deadline')
    return value


def run(argv, deadline):
    # Child output can contain private filenames. Discard it rather than send
    # it to the console. Neither a filename nor a secret becomes shell code.
    timeout = remaining(deadline)
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True,
                          env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}) as child:
        try:
            child.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            raise
        if child.returncode:
            raise CopyError(f'retained-data command {argv[0]} failed (exit {child.returncode}); partial destination is not reusable')


def validate(request):
    if (not isinstance(request, dict) or set(request) !=
            {'kind', 'operation_id', 'source_uuid', 'destination_uuid', 'runtime', 'entries'} or
            request['kind'] != 'klokast.vm-retained-copy.v1'):
        raise CopyError('invalid retained-data request contract')
    for key, pattern in (('operation_id', r'[0-9a-f]{24}'),
                         ('source_uuid', r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'),
                         ('destination_uuid', r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')):
        if not isinstance(request[key], str) or not re.fullmatch(pattern, request[key]):
            raise CopyError('invalid operation or filesystem identity')
    if request['source_uuid'] == request['destination_uuid']:
        raise CopyError('source and destination filesystem identities overlap')
    runtime = request['runtime']
    if not isinstance(runtime, dict) or set(runtime) != {'uid', 'gid', 'subuid', 'subgid'}:
        raise CopyError('runtime ownership is incomplete')
    for key in ('uid', 'gid'):
        if type(runtime[key]) is not int or not 0 < runtime[key] < 2**32 - 1:
            raise CopyError('runtime UID and GID must be positive numeric identities')
    for key in ('subuid', 'subgid'):
        ranges = runtime[key]
        if not isinstance(ranges, list) or not 1 <= len(ranges) <= 16:
            raise CopyError('subordinate identity ranges are missing')
        end = 0
        for item in ranges:
            if (not isinstance(item, list) or len(item) != 2 or
                    any(type(v) is not int for v in item) or item[0] < end or
                    item[0] < 1 or item[1] < 1 or item[0] + item[1] >= 2**32):
                raise CopyError('subordinate identity ranges overlap or are invalid')
            end = sum(item)
    entries = request['entries']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
        raise CopyError('retained-data mappings are missing or too numerous')
    keys, paths = set(), []
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'key', 'source'} or
                not isinstance(entry['key'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', entry['key']) or
                not isinstance(entry['source'], str)):
            raise CopyError('invalid retained-data mapping')
        path = entry['source']
        parts = path.split('/')
        if (len(parts) < 3 or any(p in ('', '.', '..') for p in parts) or
                any(ord(c) < 32 or ord(c) == 127 for c in path) or len(path) > 4096 or
                parts[0] not in {'home', 'srv', 'var', 'etc'} or
                path.startswith(('etc/rc', 'etc/init.d', 'var/run/', 'var/cache/'))):
            raise CopyError('mapping must name a narrow retained directory, not OS configuration or a whole home')
        if entry['key'] in keys or any(path == p or path.startswith(p + '/') or p.startswith(path + '/') for p in paths):
            raise CopyError('retained-data mappings overlap or repeat')
        keys.add(entry['key'])
        paths.append(path)


def environment():
    if (os.geteuid() != 0 or Path('/sys/hypervisor/type').read_text().strip() != 'xen' or
            Path('/sys/hypervisor/uuid').read_text().strip() == '00000000-0000-0000-0000-000000000000' or
            {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'}):
        raise CopyError('retained-data copying requires a networkless Xen guest, never dom0')


def mount_records():
    records = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        split = fields.index('-')
        # Refuse escaped mount paths rather than interpret ambiguous names.
        records.append({'device': fields[2], 'root': fields[3], 'path': fields[4],
                        'options': fields[5].split(','), 'type': fields[split + 1],
                        'source': fields[split + 2]})
    return records


def filesystem_uuid(device):
    # The approved Alpine base supplies BusyBox blkid, which has no util-linux
    # -s/-o options. Require one exact device record and unique native fields.
    result = subprocess.run(['/bin/busybox', 'blkid', device], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=10)
    if result.returncode or len(result.stdout) > 8192 or len(result.stdout.splitlines()) != 1:
        raise CopyError('filesystem identity probe failed or returned ambiguous output')
    try:
        tokens = shlex.split(result.stdout)
    except ValueError as error:
        raise CopyError('filesystem identity probe returned malformed fields') from error
    if not tokens or tokens.pop(0) != device + ':':
        raise CopyError('filesystem identity probe returned the wrong device')
    fields = {}
    for token in tokens:
        key, separator, value = token.partition('=')
        if not separator or key in fields or not re.fullmatch('[A-Z_]+', key):
            raise CopyError('filesystem identity probe returned duplicate or invalid fields')
        fields[key] = value
    if fields.get('TYPE') != 'ext4' or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', fields.get('UUID', '')):
        raise CopyError('filesystem identity probe has no valid ext4 UUID')
    return fields['UUID']


def check_mounts(request):
    records = mount_records()
    selected = []
    for root, device, access, key in ((SOURCE, '/dev/xvdc', 'ro', 'source_uuid'),
                                      (TARGET, '/dev/xvdd', 'rw', 'destination_uuid')):
        if root.is_symlink() or not root.is_dir():
            raise CopyError('retained-data mountpoint is unsafe or missing')
        matches = [r for r in records if r['path'] == str(root)]
        if len(matches) != 1:
            raise CopyError('retained-data mount is missing or ambiguous')
        record = matches[0]
        if (record['source'] != device or record['type'] != 'ext4' or record['root'] != '/' or
                access not in record['options'] or
                any(r['path'].startswith(str(root) + '/') for r in records) or
                sum(r['device'] == record['device'] for r in records) != 1):
            raise CopyError('retained-data filesystem has wrong access, alias mounts, or nested mounts')
        info = Path(device).lstat()
        if not stat.S_ISBLK(info.st_mode) or record['device'] != f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}':
            raise CopyError('retained-data device identity differs from the mount')
        if filesystem_uuid(device) != request[key]:
            raise CopyError('retained-data filesystem UUID differs from the request')
        selected.append(record['device'])
    if len(set(selected)) != 2 or SOURCE.stat().st_dev == TARGET.stat().st_dev:
        raise CopyError('retained-data disks overlap')


def below(root, relative):
    path = root
    for part in relative.split('/'):
        path = path / part
        if path.is_symlink():
            raise CopyError('retained-data source path contains a symlink')
    return path


def runtime_identity(root):
    def lines(name):
        path = below(root, 'etc/' + name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise CopyError('runtime identity file is missing, oversized, or unsafe')
        return [line.split(':') for line in path.read_text().splitlines() if line]
    users = [v for v in lines('passwd') if v[0] == 'neo']
    if len(users) != 1 or len(users[0]) != 7:
        raise CopyError('source has no unique neo runtime account')
    identity = {'uid': int(users[0][2]), 'gid': int(users[0][3])}
    for key in ('subuid', 'subgid'):
        values = lines(key)
        if any(len(v) != 3 for v in values):
            raise CopyError('invalid subordinate identity file')
        identity[key] = sorted([int(v[1]), int(v[2])] for v in values
                               if v[0] in {'neo', str(identity['uid'])})
        if not identity[key]:
            raise CopyError('source runtime subordinate identities are missing')
    return identity


def tree(root, deadline):
    """Measure bytes, metadata, xattrs (including ACLs), and hardlink groups.

    Never follow symlinks. Atime is deliberately absent: reading changes it.
    Reject links outside this one mapping instead of silently breaking them.
    """
    result, links, space = [], {}, 0
    device = root.lstat().st_dev
    pending = [root]
    while pending:
        remaining(deadline)
        path = pending.pop()
        info = path.lstat()
        if info.st_dev != device:
            raise CopyError('retained-data tree crosses a filesystem boundary')
        relative = str(path.relative_to(root))
        mode = info.st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
            raise CopyError('retained-data tree contains a device, socket, or FIFO')
        record = {'path': relative, 'mode': mode, 'uid': info.st_uid, 'gid': info.st_gid,
                  'mtime_ns': info.st_mtime_ns,
                  'xattrs': {k: hashlib.sha256(os.getxattr(path, k, follow_symlinks=False)).hexdigest()
                             for k in sorted(os.listxattr(path, follow_symlinks=False))}}
        if stat.S_ISDIR(mode):
            pending.extend(sorted(path.iterdir(), reverse=True))
            space += 4096
        elif stat.S_ISLNK(mode):
            record['link'] = os.readlink(path)
            space += 4096
        else:
            identity = (info.st_dev, info.st_ino)
            group = links.setdefault(identity, {'first': relative, 'count': 0, 'expected': info.st_nlink})
            group['count'] += 1
            record['hardlink'] = group['first']
            record['bytes'] = info.st_size
            value = hashlib.sha256()
            with path.open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    remaining(deadline)
                    value.update(chunk)
            record['sha256'] = value.hexdigest()
            if group['count'] == 1:
                space += ((info.st_size + 4095) // 4096) * 4096
        result.append(record)
        if len(result) + len(pending) > MAX_ENTRIES:
            raise CopyError('retained-data tree exceeds its entry limit')
    if any(group['count'] != group['expected'] for group in links.values()):
        raise CopyError('retained-data hardlink extends outside its mapping')
    return {'sha256': digest(result), 'entries': len(result), 'required_bytes': space}


def copy(request, deadline):
    validate(request)
    if remaining(deadline) > 1800:
        raise CopyError('retained-data copy budget must not exceed 30 minutes')
    environment()
    check_mounts(request)
    if runtime_identity(SOURCE) != request['runtime']:
        raise CopyError('runtime UID, GID, or subordinate identities changed')
    existing = list(TARGET.iterdir())
    if any(p.name != 'lost+found' or p.is_symlink() or not p.is_dir() or any(p.iterdir()) for p in existing):
        raise CopyError('destination contains unknown or partial data; use a new empty filesystem')
    measured = {}
    for entry in request['entries']:
        path = below(SOURCE, entry['source'])
        if not path.is_dir():
            raise CopyError('retained-data mapping does not name an existing directory')
        measured[entry['key']] = tree(path, deadline)
    capacity = os.statvfs(TARGET)
    if (capacity.f_bavail * capacity.f_frsize < RESERVE + sum(v['required_bytes'] for v in measured.values()) or
            capacity.f_favail < 32 + sum(v['entries'] for v in measured.values())):
        raise CopyError('retained-data destination has insufficient bytes or inodes')
    # Claim the destination before the first write. Failed operations keep this
    # marker and all partial data for inspection, never deletion or reuse.
    claim = TARGET / '.klokast-copy-pending'
    with claim.open('xb') as stream:
        stream.write(canonical({'operation_id': request['operation_id'], 'request_sha256': digest(request)}))
        stream.flush()
        os.fsync(stream.fileno())
    for entry in request['entries']:
        remaining(deadline)
        source, destination = below(SOURCE, entry['source']), TARGET / entry['key']
        destination.mkdir(mode=0o700)
        run(['rsync', '-aHAXS', '--numeric-ids', '--one-file-system', '--modify-window=-1',
             '--', str(source) + '/', str(destination) + '/'], deadline)
        if tree(destination, deadline) != measured[entry['key']] or tree(source, deadline) != measured[entry['key']]:
            raise CopyError('retained-data content, ownership, links, or metadata verification failed')
    check_mounts(request)
    if runtime_identity(SOURCE) != request['runtime']:
        raise CopyError('runtime identities changed during retained-data copy')
    # syncfs waits for this destination only; the controller must enforce the
    # outer VM deadline if storage stops responding to a kernel syscall.
    run(['sync', '-f', str(TARGET)], deadline)
    receipt = {'kind': 'klokast.vm-retained-copy-result.v1', 'operation_id': request['operation_id'],
               'request_sha256': digest(request), 'source_uuid': request['source_uuid'],
               'destination_uuid': request['destination_uuid'], 'runtime': request['runtime'],
               'entries': measured, 'copy_verified': True, 'adoption_accepted': False}
    with (TARGET / '.klokast-copy-result.json').open('xb') as stream:
        stream.write(canonical(receipt) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    claim.unlink()
    run(['sync', '-f', str(TARGET)], deadline)
    return receipt
