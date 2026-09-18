"""Make an independent opaque disk backup without mounting guest filesystems.

This root-only primitive does not authorize adoption. Its caller must hold the
installation mutation lease and supply current, approved source evidence. A
live snapshot is crash-consistent evidence only, never app-consistency proof.
The native test uses new synthetic LVs; this module has no public command.
"""
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time

MIB = 1024**2
LV = re.compile(r'/dev/([A-Za-z0-9][A-Za-z0-9_+.-]{0,63})/([A-Za-z0-9][A-Za-z0-9_+.-]{0,126})')
UUID = re.compile(r'[A-Za-z0-9]{6}(?:-[A-Za-z0-9]{4}){5}-[A-Za-z0-9]{6}')
SHA = re.compile(r'[0-9a-f]{64}')


class BackupError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise BackupError('duplicate backup record field')
        value[key] = item
    return value


def secure(path, directory=False):
    info = Path(path).lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or
            (not directory and (info.st_nlink != 1 or info.st_size > MIB))):
        raise BackupError('backup records require private root-owned storage')


def store(path, value):
    secure(path.parent, True)
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value) + b'\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def number(value):
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise BackupError('invalid native LVM number') from error
    if not result.is_finite() or result < 0:
        raise BackupError('invalid native LVM number')
    return result


def integer(value):
    result = number(value)
    if result != int(result):
        raise BackupError('native LVM byte count is not integral')
    return int(result)


def validate(request):
    if (not isinstance(request, dict) or set(request) != {
            'kind', 'operation_id', 'box', 'engine_commit', 'source_evidence_sha256',
            'source', 'cow_bytes', 'requested_at'} or
            request['kind'] != 'klokast.vm-disk-backup.v1'):
        raise BackupError('invalid disk backup request contract')
    for key, pattern in (('operation_id', r'[0-9a-f]{24}'), ('box', r'[a-z0-9][a-z0-9-]{0,30}'),
                         ('engine_commit', r'[0-9a-f]{40}'), ('source_evidence_sha256', SHA.pattern)):
        if not isinstance(request[key], str) or not re.fullmatch(pattern, request[key]):
            raise BackupError('invalid backup source or operation identity')
    source = request['source']
    if (not isinstance(source, dict) or set(source) != {'path', 'uuid', 'bytes'} or
            not isinstance(source['path'], str) or not LV.fullmatch(source['path']) or
            not isinstance(source['uuid'], str) or not UUID.fullmatch(source['uuid']) or
            type(source['bytes']) is not int or not 16 * MIB <= source['bytes'] <= 128 * 1024**3 or
            source['bytes'] % 512):
        raise BackupError('backup requires an exact bounded source LV')
    if (type(request['cow_bytes']) is not int or not 16 * MIB <= request['cow_bytes'] <= source['bytes'] or
            request['cow_bytes'] % 512 or type(request['requested_at']) is not int or request['requested_at'] < 1):
        raise BackupError('invalid snapshot capacity or request time')


class Native:
    def command(self, argv):
        try:
            result = subprocess.run([str(v) for v in argv], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=30,
                                    env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BackupError('backup command unavailable or timed out: ' + str(argv[0])) from error
        if result.returncode or len(result.stdout) > 4 * MIB:
            raise BackupError('backup command failed: ' + str(argv[0]) + ' (exit ' + str(result.returncode) + ')')
        return result.stdout

    def rows(self, argv, key):
        try:
            value = json.loads(self.command(argv), object_pairs_hook=unique)
            rows = value['report'][0][key]
            if not isinstance(rows, list) or len(value['report']) != 1:
                raise ValueError()
            return [{k: v.strip() for k, v in row.items()} for row in rows]
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise BackupError('native LVM report is incomplete') from error

    def volumes(self):
        return self.rows(['lvs', '--reportformat', 'json', '--units', 'b', '--nosuffix', '-o',
                          'lv_path,lv_uuid,lv_size,lv_attr,origin_uuid,segtype,data_percent,lv_tags'], 'lv')

    def volume(self, path):
        matches = [r for r in self.volumes() if r['lv_path'] == path]
        if len(matches) != 1:
            raise BackupError('backup logical volume is missing or ambiguous')
        row = matches[0]
        info = Path(path).stat()
        if not stat.S_ISBLK(info.st_mode) or not UUID.fullmatch(row['lv_uuid']):
            raise BackupError('backup LV does not have a valid block identity')
        return {'path': path, 'uuid': row['lv_uuid'], 'bytes': integer(row['lv_size']),
                'device': info.st_rdev, 'attr': row['lv_attr'], 'origin_uuid': row['origin_uuid'],
                'segtype': row['segtype'], 'data_percent': row['data_percent'],
                'tags': sorted(filter(None, row['lv_tags'].split(',')))}

    def capacity(self, vg):
        rows = self.rows(['vgs', '--reportformat', 'json', '--units', 'b', '--nosuffix',
                          '-o', 'vg_name,vg_free,vg_extent_size', vg], 'vg')
        if len(rows) != 1 or rows[0]['vg_name'] != vg:
            raise BackupError('backup volume group is missing or ambiguous')
        return integer(rows[0]['vg_free']), integer(rows[0]['vg_extent_size'])

    def unmounted(self, volumes):
        # Include partitions of each assigned block device, not just its parent.
        roots = [(Path('/sys/dev/block') / f'{os.major(v["device"])}:{os.minor(v["device"])}').resolve(strict=True)
                 for v in volumes]
        # Device-mapper partition maps need not be sysfs children of their
        # parent. Any holder is an unqualified alias; do not infer it is safe.
        if any(any((root / 'holders').iterdir()) for root in roots):
            raise BackupError('backup block device has an unqualified holder or partition map')
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            device = (Path('/sys/dev/block') / line.split()[2])
            if device.exists() and any(device.resolve().is_relative_to(root) for root in roots):
                raise BackupError('backup source, snapshot, or destination is mounted on dom0')

    def unattached(self, volumes):
        devices = {v['device'] for v in volumes}
        records = json.loads(self.command(['xl', 'list', '-l']), object_pairs_hook=unique)
        if not isinstance(records, list) or not any(r.get('domid') == 0 for r in records):
            raise BackupError('backup requires a complete Xen inventory')
        for row in records:
            if type(row.get('domid')) is not int:
                raise BackupError('backup Xen inventory has an invalid domain')
            if row['domid'] == 0:
                continue
            config = row.get('config', {})
            if not isinstance(config.get('disks'), list):
                raise BackupError('backup Xen inventory has no complete disk list')
            for disk in config['disks']:
                path = disk.get('pdev_path')
                if not isinstance(path, str) or not path.startswith('/dev/'):
                    raise BackupError('backup Xen inventory contains an unsupported disk backend')
                if Path(path).stat().st_rdev in devices:
                    raise BackupError('backup snapshot or destination is attached to a Xen guest')

    def create(self, path, size, tag, origin=None):
        vg, name = LV.fullmatch(path).groups()
        args = ['lvcreate', '--yes', '--addtag', tag, '-L', str(size) + 'B', '-n', name]
        if origin:
            args += ['--snapshot', '--permission', 'r', origin]
        else:
            args += ['--type', 'linear', '--wipesignatures', 'y', vg]
        self.command(args)

    def readonly(self, path):
        self.command(['lvchange', '--permission', 'r', path])

    def remove(self, path):
        self.command(['lvremove', '--yes', path])

    def open(self, volume, writable=False):
        descriptor = os.open(volume['path'], os.O_RDWR if writable else os.O_RDONLY)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISBLK(info.st_mode) or info.st_rdev != volume['device']:
                raise BackupError('backup block device changed before use')
            # Linux BLKGETSIZE64 and BLKROGET verify the opened descriptor,
            # including the snapshot's logical size rather than its COW size.
            import array
            size, readonly = array.array('Q', [0]), array.array('i', [0])
            fcntl.ioctl(descriptor, 0x80081272, size, True)
            fcntl.ioctl(descriptor, 0x125e, readonly, True)
            if size[0] != volume['device_bytes'] or readonly[0] != (0 if writable else 1):
                raise BackupError('backup block size or read-only state changed')
            return os.fdopen(descriptor, 'r+b' if writable else 'rb', buffering=0)
        except BaseException:
            os.close(descriptor)
            raise


def same_identity(current, recorded):
    if any(current[k] != recorded[k] for k in ('path', 'uuid', 'bytes', 'device')):
        raise BackupError('backup LV identity changed; retain records and inspect storage')


def ordinary(volume, readonly=False, origin=False):
    attr = volume['attr']
    if (len(attr) != 10 or attr[0] not in ('-o' if origin else '-') or attr[1] != ('r' if readonly else 'w') or
            attr[4] != 'a' or volume['origin_uuid'] or volume['segtype'] != 'linear'):
        raise BackupError('backup requires an active ordinary linear LV with the exact access mode')


def make_backup(work, request, native=None):
    """Caller holds the box and installation leases; never writes the source."""
    validate(request)
    secure(work, True)
    if (work / 'journal.json').exists() or (work / 'result.json').exists():
        raise BackupError('backup operation was already used; inspect its existing records')
    native = native or Native()
    if not 0 <= time.time() - request['requested_at'] <= 300:
        raise BackupError('backup request is stale or from the future')
    started, deadline = time.time(), time.monotonic() + 1800
    source = native.volume(request['source']['path'])
    if any(source[k] != request['source'][k] for k in ('path', 'uuid', 'bytes')):
        raise BackupError('backup source differs from approved evidence')
    ordinary(source)
    native.unmounted([source])
    vg = LV.fullmatch(source['path'])[1]
    prefix = 'vmbackup_' + request['operation_id']
    paths = {key: '/dev/' + vg + '/' + prefix + '_' + key for key in ('snapshot', 'disk')}
    tags = {key: 'klokast.vm-backup.' + request['operation_id'] + '.' + key for key in paths}
    if any(v['lv_path'] in paths.values() or any(tag in v['lv_tags'].split(',') for tag in tags.values())
           for v in native.volumes()):
        raise BackupError('backup names or tags already exist; no allocation is reusable')
    free, extent = native.capacity(vg)
    if (extent <= 0 or source['bytes'] % extent or request['cow_bytes'] % extent or
            free < source['bytes'] * 2 + request['cow_bytes'] + 1024 * MIB):
        raise BackupError('backup needs space for independent backup and restore LVs, COW, and 1 GiB reserve')
    journal = {'kind': 'klokast.vm-disk-backup-journal.v1', 'request_sha256': digest(request),
               'source': source, 'allocation_paths': paths, 'allocation_tags': tags, 'resources': {},
               'started_at': started, 'stage': 'prepared'}
    store(work / 'request.json', request)
    def record(stage):
        journal.update(stage=stage, updated_at=time.time())
        store(work / 'journal.json', journal)
    def check_time():
        if time.monotonic() >= deadline:
            raise BackupError('disk backup exceeded its 30-minute limit')
    def check_source():
        current = native.volume(source['path'])
        same_identity(current, source); ordinary(current, origin=True)
        native.unmounted([current])
    def checked_resource(key, *, health=True, readonly=False):
        expected = journal['resources'][key]
        current = native.volume(expected['path']); same_identity(current, expected)
        if current['tags'] != [tags[key]]:
            raise BackupError('backup allocation tags changed; retain this LV')
        native.unmounted([current]); native.unattached([current])
        if key == 'snapshot':
            if (len(current['attr']) != 10 or current['attr'][:2] != 'sr' or
                    current['origin_uuid'] != source['uuid'] or current['segtype'] != 'linear' or current['attr'][6] != 's'):
                raise BackupError('snapshot no longer belongs to the recorded source')
            if health and (current['attr'][4] != 'a' or not current['data_percent'] or
                           number(current['data_percent']) >= 80):
                raise BackupError('snapshot is invalid or exceeds its 80 percent capacity limit')
        else:
            ordinary(current, readonly=readonly)
        current['device_bytes'] = source['bytes']
        return current
    record('prepared')
    try:
        # Journal intent before each allocation. A kill between creation and
        # UUID recording leaves an unaccepted resource for explicit inspection.
        for key in ('disk', 'snapshot'):
            record('allocating-' + key)
            native.create(paths[key], source['bytes'] if key == 'disk' else request['cow_bytes'],
                          tags[key], source['path'] if key == 'snapshot' else None)
            current = native.volume(paths[key])
            if (current['uuid'] == source['uuid'] or current['device'] == source['device'] or
                    any(current['uuid'] == v['uuid'] or current['device'] == v['device']
                        for v in journal['resources'].values())):
                raise BackupError('backup allocations alias existing storage')
            journal['resources'][key] = current
            record('allocated-' + key)
            checked_resource(key)
        check_source()
        journal['snapshot_at'] = time.time()
        record('copying')
        snapshot, backup = checked_resource('snapshot'), checked_resource('disk')
        result_hash = hashlib.sha256()
        with native.open(snapshot) as src, native.open(backup, True) as dst:
            remaining, next_check = source['bytes'], 0
            while remaining:
                check_time()
                if time.monotonic() >= next_check:
                    checked_resource('snapshot'); check_source(); next_check = time.monotonic() + 2
                data = src.read(min(4 * MIB, remaining))
                if not data or dst.write(data) != len(data):
                    raise BackupError('backup copy was truncated; destination is not usable')
                result_hash.update(data); remaining -= len(data)
            os.fsync(dst.fileno())
        checked_resource('snapshot'); check_source()
        record('sealing')
        checked_resource('disk')
        native.readonly(backup['path'])
        backup = checked_resource('disk', readonly=True)
        check_hash = hashlib.sha256()
        with native.open(backup) as stream:
            remaining = source['bytes']
            while remaining:
                check_time()
                data = stream.read(min(4 * MIB, remaining))
                if not data:
                    raise BackupError('backup read-back was truncated')
                check_hash.update(data); remaining -= len(data)
        if check_hash.digest() != result_hash.digest():
            raise BackupError('backup read-back differs from the snapshot copy')
        # The snapshot is no longer needed. A full independent LV remains.
        checked_resource('snapshot'); check_source()
        record('removing-snapshot')
        native.remove(paths['snapshot'])
        journal['snapshot_removed'] = True
        record('verified-copy')
        checked_resource('disk', readonly=True)
        result = {'kind': 'klokast.vm-disk-backup-result.v1', 'operation_id': request['operation_id'],
                  'box': request['box'], 'engine_commit': request['engine_commit'],
                  'request_sha256': digest(request), 'source_evidence_sha256': request['source_evidence_sha256'],
                  'source': request['source'], 'backup': {k: backup[k] for k in ('path', 'uuid', 'bytes')},
                  'disk_sha256': check_hash.hexdigest(), 'snapshot_at': journal['snapshot_at'],
                  'completed_at': time.time(), 'independent_copy': True, 'backup_readonly': True,
                  'restore_verified': False, 'source_freshness_verified': False,
                  'application_consistency_verified': False, 'adoption_accepted': False}
        result['receipt_sha256'] = digest(result)
        store(work / 'result.json', result)
        record('complete')
        return result
    except BaseException:
        record('failed')
        # A failed backup never removes its destination. Remove only a known
        # snapshot with unchanged origin and UUID, even if its COW overflowed.
        if 'snapshot' in journal['resources'] and not journal.get('snapshot_removed'):
            checked_resource('snapshot', health=False); check_source()
            native.remove(paths['snapshot'])
            journal['snapshot_removed'] = True
            record('failed-snapshot-removed')
        raise
