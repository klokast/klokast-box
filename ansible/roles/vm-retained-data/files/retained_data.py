#!/usr/bin/python3
"""Offline retained-data copy primitive for a disposable, networkless Xen VM.

The caller supplies approved physical mappings and proves backup, fencing,
writer shutdown, disk ownership, and source completeness. This module cannot
authorize adoption, select a release, attach disks, or start services.
"""
import hashlib
import fcntl
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
BLOCK_SYS = Path('/sys/class/block')
MAX_ENTRIES = 100000
RESERVE = 128 * 1024 * 1024
# Public physical convention, not permission to copy a production identity.
IDENTITY_FILES = {'platform-tailscale-state': 'var/lib/tailscale/tailscaled.state'}
MAX_IDENTITY_BYTES = 8 * 1024 * 1024
BACKUP_MOUNT = Path('/backup-restore')
BACKUP_PENDING = Path('/run/klokast-backup-restore-pending')


class CopyError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CopyError('retained-data record contains duplicate fields')
        value[key] = item
    return value


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise CopyError('retained-data operation exceeded its deadline')
    return value


def run(argv, deadline, accepted=(0,)):
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
        if child.returncode not in accepted:
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


def validate_backup(request):
    fields = {'kind', 'operation_id', 'engine_commit', 'backup_receipt_sha256',
              'disk_bytes', 'disk_sha256', 'root_partition', 'root_uuid', 'runtime'}
    if (not isinstance(request, dict) or set(request) != fields or
            request['kind'] != 'klokast.vm-backup-restore.v1' or
            type(request['disk_bytes']) is not int or not 16 * 1024**2 <= request['disk_bytes'] <= 128 * 1024**3 or
            request['disk_bytes'] % 512 or type(request['root_partition']) is not int or request['root_partition'] not in (0, 3)):
        raise CopyError('backup restore request has an unsupported disk contract')
    for key, pattern in (('operation_id', '[0-9a-f]{24}'), ('engine_commit', '[0-9a-f]{40}'),
                         ('backup_receipt_sha256', '[0-9a-f]{64}'), ('disk_sha256', '[0-9a-f]{64}'),
                         ('root_uuid', '[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')):
        if not isinstance(request[key], str) or not re.fullmatch(pattern, request[key]):
            raise CopyError('backup restore request has invalid provenance')
    # Reuse the closed numeric ownership contract; these dummy distinct UUIDs
    # are validation inputs only, never selected filesystem identities.
    validate({'kind': 'klokast.vm-retained-copy.v1', 'operation_id': request['operation_id'],
              'source_uuid': '11111111-1111-4111-8111-111111111111',
              'destination_uuid': '22222222-2222-4222-8222-222222222222',
              'runtime': request['runtime'], 'entries': [{'key': 'state', 'source': 'var/lib/state'}]})


def backup_devices(request):
    devices = []
    for name, readonly in (('xvdc', '1'), ('xvdd', '0')):
        path = Path('/dev/' + name)
        info = path.lstat()
        if (not stat.S_ISBLK(info.st_mode) or
                (BLOCK_SYS / name / 'dev').read_text().strip() != f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' or
                int((BLOCK_SYS / name / 'size').read_text()) * 512 != request['disk_bytes'] or
                (BLOCK_SYS / name / 'ro').read_text().strip() != readonly):
            raise CopyError('backup restore requires an exact read-only backup and disposable writable restore disk')
        devices.append(info.st_rdev)
        aliases = {(p / 'dev').read_text().strip() for p in BLOCK_SYS.glob(name + '*') if (p / 'dev').is_file()}
        if any(v['device'] in aliases or v['source'].startswith(str(path)) for v in mount_records()):
            raise CopyError('backup or restore disk has a mounted filesystem or alias')
    if len(set(devices)) != 2:
        raise CopyError('backup and restore disks overlap')


def backup_root(name, partition):
    device = BLOCK_SYS / (name + ('3' if partition else ''))
    if partition and (device.joinpath('partition').read_text().strip() != '3' or
                      device.resolve().parent != (BLOCK_SYS / name).resolve()):
        raise CopyError('backup root partition does not belong to its assigned disk')
    if device.joinpath('ro').read_text().strip() != ('1' if name == 'xvdc' else '0'):
        raise CopyError('backup root partition has unexpected block access')
    path = '/dev/' + name + ('3' if partition else '')
    info = Path(path).lstat()
    if (not stat.S_ISBLK(info.st_mode) or
            device.joinpath('dev').read_text().strip() != f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'):
        raise CopyError('backup root device node differs from its recorded kernel device')
    return path


def disk_digest(path, size, deadline):
    value = hashlib.sha256()
    remaining_bytes = size
    with open(path, 'rb', buffering=0) as stream:
        while remaining_bytes:
            remaining(deadline)
            chunk = stream.read(min(1024 * 1024, remaining_bytes))
            if not chunk:
                raise CopyError('backup disk is truncated')
            value.update(chunk); remaining_bytes -= len(chunk)
    return value.hexdigest()


def restore_backup(request, deadline):
    """Test an independently stored full-disk backup in a maintenance Xen VM.

    The controller must prove origin/snapshot freshness, independent allocation,
    capacity, application consistency, and authority before attaching disks.
    This primitive overwrites only the assigned disposable /dev/xvdd. It never
    mounts or boots the original backup and never accepts a production release.
    """
    validate_backup(request)
    if remaining(deadline) > 1800:
        raise CopyError('backup restore verification must be bounded to 30 minutes')
    environment(); backup_devices(request)
    marker = read_record(Path('/etc/klokast-template.json'), private=False)
    if not isinstance(marker, dict) or marker.get('engine_commit') != request['engine_commit'] or marker.get('profile') != 'shared-alpine-v1':
        raise CopyError('backup maintenance image differs from the recorded engine and profile')
    if BACKUP_PENDING.exists() or BACKUP_PENDING.is_symlink():
        raise CopyError('backup restore staging was already used; allocate a new maintenance guest')
    if disk_digest('/dev/xvdc', request['disk_bytes'], deadline) != request['disk_sha256']:
        raise CopyError('backup bytes differ from the protected backup receipt')
    if filesystem_uuid(backup_root('xvdc', request['root_partition'])) != request['root_uuid']:
        raise CopyError('backup root filesystem identity differs')
    if BACKUP_MOUNT.is_symlink() or (BACKUP_MOUNT.exists() and any(BACKUP_MOUNT.iterdir())):
        raise CopyError('backup restore mountpoint is unsafe or not empty')
    BACKUP_MOUNT.mkdir(mode=0o700, exist_ok=True)
    create_record(BACKUP_PENDING, {'request_sha256': digest(request)})
    with open('/dev/xvdc', 'rb', buffering=0) as source, open('/dev/xvdd', 'wb', buffering=0) as target:
        count = request['disk_bytes']
        while count:
            remaining(deadline)
            chunk = source.read(min(1024 * 1024, count))
            if not chunk or target.write(chunk) != len(chunk):
                raise CopyError('backup restore copy has a short device read or write')
            count -= len(chunk)
        os.fsync(target.fileno())
    if disk_digest('/dev/xvdd', request['disk_bytes'], deadline) != request['disk_sha256']:
        raise CopyError('restored disk differs from the complete backup')
    with open('/dev/xvdd', 'rb', buffering=0) as target:
        fcntl.ioctl(target.fileno(), 0x125f)  # Linux BLKRRPART on the disposable clone.
    for _ in range(100):
        remaining(deadline)
        if Path('/dev/xvdd' + ('3' if request['root_partition'] else '')).exists():
            break
        time.sleep(0.1)
    device = backup_root('xvdd', request['root_partition'])
    if filesystem_uuid(device) != request['root_uuid']:
        raise CopyError('restored root filesystem identity differs')
    # e2fsck journal_only replays committed transactions without broader repair.
    # A second, forced read-only check must pass; no repair can hide data loss.
    run(['e2fsck', '-p', '-E', 'journal_only', device], deadline, accepted=(0, 1))
    run(['e2fsck', '-f', '-n', device], deadline)
    run(['mount', '-t', 'ext4', '-o', 'ro,noload,nodev,nosuid,noexec', device, str(BACKUP_MOUNT)], deadline)
    try:
        mounts = mount_records()
        selected = [v for v in mounts if v['path'] == str(BACKUP_MOUNT)]
        if (len(selected) != 1 or selected[0]['source'] != device or selected[0]['root'] != '/' or
                selected[0]['type'] != 'ext4' or not {'ro', 'nodev', 'nosuid', 'noexec'} <= set(selected[0]['options']) or
                any(v['path'].startswith(str(BACKUP_MOUNT) + '/') for v in mounts)):
            raise CopyError('restored filesystem mount differs from the isolated read-only contract')
        if runtime_identity(BACKUP_MOUNT) != request['runtime']:
            raise CopyError('restored numeric runtime identity differs')
        identity = below(BACKUP_MOUNT, 'var/lib/tailscale/tailscaled.state')
        info = identity.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or
                info.st_nlink != 1 or not 0 < info.st_size <= MAX_IDENTITY_BYTES):
            raise CopyError('restored management identity is missing or has unsafe metadata')
        measured = tree(identity, deadline)
    finally:
        run(['umount', str(BACKUP_MOUNT)], deadline)
    backup_devices(request)
    if disk_digest('/dev/xvdc', request['disk_bytes'], deadline) != request['disk_sha256']:
        raise CopyError('original backup changed during restore verification')
    result = {'kind': 'klokast.vm-backup-restore-result.v1', 'request_sha256': digest(request),
              'backup_receipt_sha256': request['backup_receipt_sha256'],
              'disk_sha256': request['disk_sha256'], 'disk_bytes': request['disk_bytes'],
              'root_uuid': request['root_uuid'], 'runtime': request['runtime'],
              'identity': measured, 'complete_disk_restored': True, 'root_filesystem_checked': True,
              'backup_unchanged': True, 'source_freshness_verified': False,
              'application_consistency_verified': False, 'adoption_accepted': False}
    result['receipt_sha256'] = digest(result)
    return result


def backup_boot_request(args):
    """Read a closed request disk only after checking all five Xen slots."""
    for key, pattern in (('klokast_backup_restore', r'[0-9a-f]{24}'),
                         ('klokast_request_sha256', r'[0-9a-f]{64}'),
                         ('klokast_inputs', r'[0-9a-f]{64}')):
        if not re.fullmatch(pattern, args.get(key, '')):
            raise CopyError('backup maintenance boot has no exact operation and input assignment')
    identities = set()
    for name in ('xvda', 'xvdb', 'xvdc', 'xvdd', 'xvde'):
        info = Path('/dev/' + name).lstat()
        node = BLOCK_SYS / name
        if (not stat.S_ISBLK(info.st_mode) or info.st_rdev in identities or
                node.joinpath('dev').read_text().strip() != f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' or
                node.joinpath('ro').read_text().strip() != ('1' if name in ('xvdc', 'xvde') else '0')):
            raise CopyError('backup maintenance disk assignments are missing, aliased, or have wrong access modes')
        identities.add(info.st_rdev)
        if name in ('xvdb', 'xvde') and node.joinpath('size').read_text().strip() != '2048':
            raise CopyError('backup maintenance request and result slots must each be 1 MiB')
    with open('/dev/xvde', 'rb', buffering=0) as stream:
        raw = stream.read(65537)
    prefix, separator, unused = raw.partition(b'\0')
    if not separator or not 1 <= len(prefix) <= 65536 or hashlib.sha256(prefix).hexdigest() != args['klokast_request_sha256']:
        raise CopyError('backup maintenance request disk is truncated or changed')
    try:
        request = json.loads(prefix, object_pairs_hook=unique_fields)
    except (ValueError, UnicodeError) as error:
        raise CopyError('backup maintenance request disk contains invalid JSON') from error
    validate_backup(request)
    if request['operation_id'] != args['klokast_backup_restore']:
        raise CopyError('backup maintenance operation differs from its request')
    marker = read_record(Path('/etc/klokast-template.json'), private=False)
    if marker.get('inputs_sha256') != args['klokast_inputs']:
        raise CopyError('backup maintenance root differs from its assigned package inputs')
    return request


def backup_boot():
    """Dedicated PID 1: never starts guest identities, services, or networking."""
    if os.getpid() != 1 or os.geteuid() != 0:
        raise CopyError('backup maintenance entry requires PID 1 in its disposable Xen guest')
    deadline = time.monotonic() + 1800
    result_safe = False
    result = None
    try:
        for name, filesystem in (('proc', 'proc'), ('sys', 'sysfs'), ('dev', 'devtmpfs')):
            if not os.path.ismount('/' + name):
                run(['mount', '-t', filesystem, filesystem, '/' + name], deadline)
        environment()
        args = {}
        for token in Path('/proc/cmdline').read_text().split():
            key, separator, value = token.partition('=')
            if separator and key.startswith('klokast_'):
                if key in args: raise CopyError('duplicate backup maintenance boot assignment')
                args[key] = value
        request = backup_boot_request(args)
        result_safe = True
        result = {'kind': 'klokast.vm-backup-restore-guest.v1', 'operation_id': request['operation_id'],
                  'inputs_sha256': args['klokast_inputs'], 'request_sha256': digest(request), 'success': False}
        result['restore'] = restore_backup(request, deadline)
        result['success'] = True
    except Exception as error:
        # Native child diagnostics and file contents never enter the console.
        print('backup restore refused: ' + type(error).__name__, flush=True)
        if result_safe:
            result['error'] = type(error).__name__ + ': ' + str(error)[:512]
    finally:
        if result_safe:
            data = canonical(result) + b'\n\0'
            if len(data) > 65536: raise CopyError('backup result exceeds its fixed slot')
            with open('/dev/xvdb', 'wb', buffering=0) as stream:
                if stream.write(data) != len(data): raise CopyError('backup result write was short')
                os.fsync(stream.fileno())
        os.sync()
        subprocess.run(['poweroff', '-f'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
        while True: time.sleep(1)


def source_device(request):
    if request.get('kind') != 'klokast.vm-retained-stage.v3':
        return '/dev/xvdc'
    # Only the recorded legacy root partition or the ordinary retained LV is
    # permitted. Never discover a filesystem and silently adopt its layout.
    partition = request['source_partition']
    disk = BLOCK_SYS / 'xvdc'
    device = disk if partition == 0 else BLOCK_SYS / 'xvdc3'
    if disk.joinpath('ro').read_text().strip() != '1' or device.joinpath('ro').read_text().strip() != '1':
        raise CopyError('source disk must be read-only at the block layer')
    if partition == 3 and (device.joinpath('partition').read_text().strip() != '3' or
                           device.resolve().parent != disk.resolve()):
        raise CopyError('legacy root partition does not belong to the assigned source disk')
    return '/dev/xvdc' + ('3' if partition else '')


def check_mounts(request):
    records = mount_records()
    source = source_device(request)
    if request.get('kind') == 'klokast.vm-retained-stage.v3' and any(
            r['source'].startswith('/dev/xvdc') and r['path'] != str(SOURCE) for r in records):
        raise CopyError('source disk has other mounted partitions or aliases')
    selected = []
    for root, device, access, key in ((SOURCE, source, 'ro', 'source_uuid'),
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


def staged_request(request):
    """Validate a separate contract; v1 never permits destination reuse."""
    fields = {'kind', 'operation_id', 'source_uuid', 'destination_uuid', 'runtime', 'entries', 'source_layout'}
    v3 = isinstance(request, dict) and request.get('kind') == 'klokast.vm-retained-stage.v3'
    if v3:
        fields.add('source_partition')
    if (not isinstance(request, dict) or set(request) !=
            fields or request['kind'] not in ('klokast.vm-retained-stage.v1', 'klokast.vm-retained-stage.v2',
                                               'klokast.vm-retained-stage.v3') or
            request['source_layout'] not in {'legacy-root', 'retained-data'}):
        raise CopyError('invalid staged retained-data request contract')
    if v3 and (type(request['source_partition']) is not int or request['source_partition'] not in (0, 3) or
               (request['source_partition'] == 3 and request['source_layout'] != 'legacy-root')):
        raise CopyError('source partition must be the recorded legacy root or an unpartitioned retained LV')
    legacy = {k: v for k, v in request.items() if k not in ('source_layout', 'source_partition')}
    legacy['kind'] = 'klokast.vm-retained-copy.v1'
    if request['kind'] in ('klokast.vm-retained-stage.v2', 'klokast.vm-retained-stage.v3'):
        entries = request['entries']
        if not isinstance(entries, list):
            raise CopyError('typed retained-data mappings are missing')
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {'key', 'source', 'type'} or
                    not isinstance(entry['key'], str) or not isinstance(entry['source'], str) or
                    entry['type'] not in ('directory', 'identity-file')):
                raise CopyError('invalid typed retained-data mapping')
            if entry['type'] == 'identity-file':
                expected = IDENTITY_FILES.get(entry['key'])
                if expected is None or entry['source'] != (expected if request['source_layout'] == 'legacy-root' else entry['key']):
                    raise CopyError('identity mapping must name the exact supported machine state file')
            elif entry['key'] in IDENTITY_FILES:
                raise CopyError('machine identity keys cannot name directory mappings')
        legacy['entries'] = [{k: v for k, v in entry.items() if k != 'type'} for entry in entries]
    if request['source_layout'] == 'retained-data':
        entries = legacy['entries']
        if (not isinstance(entries, list) or any(not isinstance(v, dict) or
                set(v) != {'key', 'source'} or v['key'] != v['source'] for v in entries)):
            raise CopyError('retained-data sources must name their exact dataset keys')
        # Reuse all identity, overlap, and dataset-key checks from v1.
        legacy['entries'] = [{'key': v['key'], 'source': 'srv/retained/' + str(v['source'])} for v in entries]
    validate(legacy)


def staged_kind(request, phase):
    version = request['kind'].rsplit('.', 1)[-1]
    return 'klokast.vm-retained-' + phase + '-result.' + version


def read_record(path, private=True):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            info.st_uid != os.geteuid() or info.st_mode & (0o077 if private else 0o022) or info.st_size > 1024 * 1024):
        raise CopyError('retained-data record has unsafe ownership, type, mode, or size')

    try:
        return json.loads(path.read_bytes(), object_pairs_hook=unique_fields)
    except (ValueError, UnicodeError) as error:
        raise CopyError('retained-data record is invalid') from error


def create_record(path, value):
    # Exclusive creation makes any interrupted stage or final sync non-reusable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def staged_environment(request, deadline):
    staged_request(request)
    if remaining(deadline) > 1800:
        raise CopyError('retained-data stage or final sync budget must not exceed 30 minutes')
    environment()
    check_mounts(request)
    if request['source_layout'] == 'legacy-root':
        runtime = runtime_identity(SOURCE)
    else:
        record = read_record(SOURCE / '.klokast-retained-identity.json')
        if (not isinstance(record, dict) or set(record) != {'kind', 'runtime'} or
                record['kind'] != 'klokast.vm-retained-identity.v1'):
            raise CopyError('retained source has no supported identity record')
        runtime = record['runtime']
    if runtime != request['runtime']:
        raise CopyError('runtime UID, GID, or subordinate identities changed')


def empty_lost_found(path):
    return path.name == 'lost+found' and stat.S_ISDIR(path.lstat().st_mode) and not any(path.iterdir())


def measure_entries(request, deadline, root=None):
    root = SOURCE if root is None else root
    result = {}
    for entry in request['entries']:
        path = below(root, entry['source'] if root == SOURCE else entry['key'])
        if entry.get('type') == 'identity-file':
            info = path.lstat()
            # environment() requires root in the networkless copy guest.
            # Keep opaque state private and reject links, aliases, and empty
            # files instead of silently enrolling a different machine later.
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or
                    not 0 < info.st_size <= MAX_IDENTITY_BYTES):
                raise CopyError('machine identity file has unsafe type, ownership, permissions, links, or size')
        elif not path.is_dir():
            raise CopyError('retained-data mapping does not name an existing directory')
        result[entry['key']] = tree(path, deadline)
    return result


def sync_entry(entry, deadline, *, final=False):
    source, destination = below(SOURCE, entry['source']), TARGET / entry['key']
    identity = entry.get('type') == 'identity-file'
    if not final and not identity:
        destination.mkdir(mode=0o700)
    argv = ['rsync', '-aHAXS', '--numeric-ids', '--one-file-system', '--modify-window=-1']
    if final:
        argv.append('--checksum')
        if not identity:
            argv.append('--delete-delay')
    # No trailing slash for an exact file. Never copy its parent directory or
    # apply directory deletion semantics to an identity-file mapping.
    suffix = '' if identity else '/'
    run(argv + ['--', str(source) + suffix, str(destination) + suffix], deadline)


def stage(request, deadline):
    """Copy a read-only snapshot into a new, operation-owned staging LV.

    Snapshot creation, its capacity checks, backup qualification, and source
    write fencing remain the outer executor's responsibility.
    """
    staged_environment(request, deadline)
    if any(not empty_lost_found(p) for p in TARGET.iterdir()):
        raise CopyError('staging destination contains unknown or partial data')
    measured = measure_entries(request, deadline)
    capacity = os.statvfs(TARGET)
    if (capacity.f_bavail * capacity.f_frsize < RESERVE + sum(v['required_bytes'] for v in measured.values()) or
            capacity.f_favail < 32 + sum(v['entries'] for v in measured.values())):
        raise CopyError('staging destination has insufficient bytes or inodes')
    pending = TARGET / '.klokast-stage-pending'
    create_record(pending, {'request_sha256': digest(request)})
    for entry in request['entries']:
        sync_entry(entry, deadline)
    if measure_entries(request, deadline, TARGET) != measured or measure_entries(request, deadline) != measured:
        raise CopyError('staged retained-data integrity verification failed')
    staged_environment(request, deadline)
    result = {'kind': staged_kind(request, 'stage'), 'request_sha256': digest(request),
              'entries': measured, 'adoption_accepted': False}
    result['receipt_sha256'] = digest(result)
    create_record(TARGET / '.klokast-stage-result.json', result)
    run(['sync', '-f', str(TARGET)], deadline)
    pending.unlink()
    run(['sync', '-f', str(TARGET)], deadline)
    return result


def allocation(root, deadline):
    """Bound final-sync scratch space without trusting sparse logical sizes."""
    pending, seen, allocated, largest = [root], set(), 0, 0
    while pending:
        remaining(deadline)
        path = pending.pop()
        info = path.lstat()
        identity = (info.st_dev, info.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        if len(seen) + len(pending) > MAX_ENTRIES:
            raise CopyError('retained-data allocation scan exceeds its entry limit')
        allocated += info.st_blocks * 512
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        elif stat.S_ISREG(info.st_mode):
            largest = max(largest, info.st_size)
    return allocated, largest


def finalize(request, stage_receipt_sha256, deadline):
    """One final sync from a stopped source, never an arbitrary retry.

    The caller must bind the stage receipt and prove writer shutdown and disk
    attachment exclusivity. A failed final sync poisons this destination.
    """
    staged_environment(request, deadline)
    if not isinstance(stage_receipt_sha256, str) or not re.fullmatch('[0-9a-f]{64}', stage_receipt_sha256):
        raise CopyError('final sync requires the exact stage receipt checksum')
    allowed = {v['key'] for v in request['entries']} | {'.klokast-stage-result.json'}
    if any(p.name not in allowed and not empty_lost_found(p) for p in TARGET.iterdir()):
        raise CopyError('final-sync destination contains unknown or partial data')
    staged = read_record(TARGET / '.klokast-stage-result.json')
    if (not isinstance(staged, dict) or set(staged) !=
            {'kind', 'request_sha256', 'entries', 'adoption_accepted', 'receipt_sha256'} or
            staged['kind'] != staged_kind(request, 'stage') or
            staged['adoption_accepted'] is not False or staged['request_sha256'] != digest(request) or
            staged['receipt_sha256'] != stage_receipt_sha256 or
            digest({k: v for k, v in staged.items() if k != 'receipt_sha256'}) != stage_receipt_sha256):
        raise CopyError('stage receipt does not match this exact operation and mapping')
    if measure_entries(request, deadline, TARGET) != staged['entries']:
        raise CopyError('staged destination changed before final sync')
    measured = measure_entries(request, deadline)
    allocated = sum(allocation(TARGET / v['key'], deadline)[0] for v in request['entries'])
    scratch = max(allocation(below(SOURCE, v['source']), deadline)[1] for v in request['entries'])
    capacity = os.statvfs(TARGET)
    growth = max(0, sum(v['required_bytes'] for v in measured.values()) - allocated)
    inode_growth = max(0, sum(v['entries'] for v in measured.values()) - sum(v['entries'] for v in staged['entries'].values()))
    if capacity.f_bavail * capacity.f_frsize < RESERVE + growth + scratch or capacity.f_favail < 32 + inode_growth:
        raise CopyError('final-sync destination has insufficient bytes or inodes')
    pending = TARGET / '.klokast-final-pending'
    create_record(pending, {'request_sha256': digest(request), 'stage_receipt_sha256': stage_receipt_sha256})
    for entry in request['entries']:
        sync_entry(entry, deadline, final=True)
    if measure_entries(request, deadline, TARGET) != measured or measure_entries(request, deadline) != measured:
        raise CopyError('final retained-data integrity verification failed')
    staged_environment(request, deadline)
    create_record(TARGET / '.klokast-retained-identity.json',
                  {'kind': 'klokast.vm-retained-identity.v1', 'runtime': request['runtime']})
    result = {'kind': staged_kind(request, 'final'), 'request_sha256': digest(request),
              'stage_receipt_sha256': stage_receipt_sha256, 'entries': measured,
              'copy_verified': True, 'adoption_accepted': False}
    result['receipt_sha256'] = digest(result)
    create_record(TARGET / '.klokast-final-result.json', result)
    run(['sync', '-f', str(TARGET)], deadline)
    pending.unlink()
    run(['sync', '-f', str(TARGET)], deadline)
    return result


if __name__ == '__main__':
    backup_boot()
