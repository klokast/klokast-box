"""Synthetic retained-data acceptance cases, only in the template test VM."""
import copy
import fcntl
import json
import os
from pathlib import Path
import shutil
import struct
import time
import uuid

import retained_data as data


def backup_test(run, operation):
    """Restore the preceding partitioned fixture; no production disks or state."""
    size = 256 * 1024 * 1024
    if any(int(Path('/sys/class/block/' + name + '/size').read_text()) * 512 != size for name in ('xvdc', 'xvdd')):
        raise RuntimeError('backup fixture requires its two disposable 256 MiB disks')
    deadline = time.monotonic() + 120
    marker = json.loads(Path('/etc/klokast-template.json').read_text())
    runtime = {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[200000, 65536]]}
    request = {'kind': 'klokast.vm-backup-restore.v1', 'operation_id': operation,
               'engine_commit': marker['engine_commit'], 'backup_receipt_sha256': data.digest({'synthetic_backup': operation}),
               'disk_bytes': size, 'disk_sha256': data.disk_digest('/dev/xvdc', size, deadline),
               'root_partition': 3, 'root_uuid': data.filesystem_uuid('/dev/xvdc3'), 'runtime': runtime}
    try:
        try:
            data.restore_backup(request, deadline)
        except data.CopyError as error:
            if 'read-only backup' not in str(error): raise
        else:
            raise RuntimeError('backup verification accepted a writable backup')
        for device in ('/dev/xvdc', '/dev/xvdc3'):
            with open(device, 'rb', buffering=0) as stream:
                fcntl.ioctl(stream.fileno(), 0x125d, struct.pack('i', 1))
        run(['mount', '-o', 'ro,noload,nodev,nosuid,noexec', '/dev/xvdc3', str(data.SOURCE)])
        try:
            expected = data.tree(data.SOURCE / 'var/lib/tailscale/tailscaled.state', deadline)
        finally:
            run(['umount', str(data.SOURCE)])
        before = data.disk_digest('/dev/xvdd', size, deadline)
        bad = dict(request, disk_sha256='0' * 64)
        try:
            data.restore_backup(bad, deadline)
        except data.CopyError as error:
            if 'protected backup receipt' not in str(error): raise
        else:
            raise RuntimeError('backup verification accepted changed backup bytes')
        if data.disk_digest('/dev/xvdd', size, deadline) != before or data.BACKUP_PENDING.exists():
            raise RuntimeError('invalid backup changed the restore target')
        result = data.restore_backup(request, deadline)
        if (result['identity'] != expected or result['adoption_accepted'] is not False or
                result['source_freshness_verified'] is not False or result['application_consistency_verified'] is not False or
                not all(result[k] for k in ('complete_disk_restored', 'root_filesystem_checked', 'backup_unchanged'))):
            raise RuntimeError('full backup restore did not preserve synthetic state or its authority boundary')
        try:
            data.restore_backup(request, deadline)
        except data.CopyError as error:
            if 'already used' not in str(error): raise
        else:
            raise RuntimeError('backup verification reused its restore staging')
        return {'restore_verified': True, 'readonly_backup_required': True, 'changed_backup_refused': True,
                'receipt_sha256': result['receipt_sha256'], 'production_data_used': False}
    finally:
        for device in ('/dev/xvdc', '/dev/xvdc3'):
            with open(device, 'rb', buffering=0) as stream:
                fcntl.ioctl(stream.fileno(), 0x125d, struct.pack('i', 0))


def partition_test(run, operation):
    """Synthetic MBR fixture on the dedicated disposable source disk only."""
    source, target = data.SOURCE, data.TARGET
    disk = Path('/dev/xvdc')
    sectors = int(Path('/sys/class/block/xvdc/size').read_text())
    if sectors != 256 * 1024 * 1024 // 512:
        raise RuntimeError('partition fixture requires the exact disposable 256 MiB disk')
    table = bytearray(512)
    table[478:494] = struct.pack('<B3sB3sII', 0, b'\0' * 3, 0x83, b'\0' * 3, 2048, sectors - 2048)
    table[510:512] = b'\x55\xaa'
    with disk.open('r+b', buffering=0) as stream:
        stream.write(b'\0' * (4 * 1024 * 1024))
        stream.seek(0); stream.write(table); os.fsync(stream.fileno())
        fcntl.ioctl(stream.fileno(), 0x125f)  # Linux BLKRRPART, fixture only.
    for _ in range(100):
        if Path('/dev/xvdc3').exists(): break
        time.sleep(0.1)
    identities = [str(uuid.uuid4()), str(uuid.uuid4())]
    mounted = []

    def readonly(value):
        for device in ('/dev/xvdc', '/dev/xvdc3'):
            with open(device, 'rb', buffering=0) as stream:
                fcntl.ioctl(stream.fileno(), 0x125d, struct.pack('i', value))  # BLKROSET.

    try:
        for device, identity, mount in zip(('/dev/xvdc3', '/dev/xvdd'), identities, (source, target)):
            run(['mkfs.ext4', '-F', '-U', identity, device])
            run(['mount', '-o', 'nodev,nosuid,noexec', device, str(mount)])
            mounted.append(mount)
        (source / 'etc').mkdir()
        (source / 'etc/passwd').write_text('neo:x:2000:2000:Runtime:/home/neo:/bin/sh\n')
        for key in ('subuid', 'subgid'):
            (source / 'etc' / key).write_text('neo:200000:65536\n')
        state = source / 'var/lib/tailscale/tailscaled.state'
        state.parent.mkdir(parents=True)
        state.write_bytes(b'partitioned-synthetic-identity'); state.chmod(0o600)
        request = {'kind': 'klokast.vm-retained-stage.v3', 'operation_id': operation,
                   'source_uuid': identities[0], 'destination_uuid': identities[1],
                   'runtime': {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[200000, 65536]]},
                   'entries': [{'key': 'platform-tailscale-state', 'source': 'var/lib/tailscale/tailscaled.state',
                                'type': 'identity-file'}], 'source_layout': 'legacy-root', 'source_partition': 3}
        run(['mount', '-o', 'remount,ro', str(source)])
        try:
            data.stage(request, time.monotonic() + 120)
        except data.CopyError as error:
            if 'read-only at the block layer' not in str(error): raise
        else:
            raise RuntimeError('partition stage accepted a writable source disk')
        readonly(1)
        deadline = time.monotonic() + 120
        staged = data.stage(request, deadline)
        final = data.finalize(request, staged['receipt_sha256'], deadline)
        if not final['copy_verified'] or (target / 'platform-tailscale-state').read_bytes() != state.read_bytes():
            raise RuntimeError('partitioned source did not preserve the synthetic identity')
        return {'legacy_partition_verified': True, 'block_readonly_required': True, 'production_data_used': False}
    finally:
        for mount in reversed(mounted):
            run(['umount', str(mount)])
        readonly(0)


def identity_test(run, operation):
    """Opaque dummy identity only; this VM never receives production secrets."""
    source, target = data.SOURCE, data.TARGET
    identities = [str(uuid.uuid4()), str(uuid.uuid4())]
    for device, identity, mount in zip(('/dev/xvdc', '/dev/xvdd'), identities, (source, target)):
        run(['mkfs.ext4', '-F', '-U', identity, device])
        run(['mount', '-o', 'nodev,nosuid,noexec', device, str(mount)])
    deadline = time.monotonic() + 120
    cases = []
    try:
        (source / 'etc').mkdir()
        (source / 'etc/passwd').write_text('neo:x:2000:2000:Runtime:/home/neo:/bin/sh\n')
        for key in ('subuid', 'subgid'):
            (source / 'etc' / key).write_text('neo:200000:65536\n')
        state = source / 'var/lib/tailscale/tailscaled.state'
        state.parent.mkdir(parents=True)
        state.write_bytes(b'synthetic-identity-before')
        state.chmod(0o600)
        os.chown(state, 0, 2345)
        os.setxattr(state, 'user.synthetic', b'identity metadata')
        (state.parent / 'unrelated').write_text('do not copy this file')
        request = {'kind': 'klokast.vm-retained-stage.v2', 'operation_id': operation,
                   'source_uuid': identities[0], 'destination_uuid': identities[1],
                   'runtime': {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[200000, 65536]]},
                   'entries': [{'key': 'platform-tailscale-state', 'source': 'var/lib/tailscale/tailscaled.state',
                                'type': 'identity-file'}], 'source_layout': 'legacy-root'}
        run(['mount', '-o', 'remount,ro', str(source)])
        bad = copy.deepcopy(request)
        bad['entries'][0]['source'] = 'var/lib/tailscale'
        try:
            data.stage(bad, deadline)
        except data.CopyError:
            cases.append('whole identity directory refused')
        else:
            raise RuntimeError('identity stage accepted a whole directory')
        staged = data.stage(request, deadline)
        run(['mount', '-o', 'remount,rw', str(source)])
        previous = state.stat()
        state.write_bytes(b'synthetic-identity-after!')
        os.utime(state, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        state.chmod(0o644)
        run(['mount', '-o', 'remount,ro', str(source)])
        try:
            data.finalize(request, staged['receipt_sha256'], deadline)
        except data.CopyError as error:
            if 'machine identity file' not in str(error):
                raise
            cases.append('unsafe identity permissions refused')
        else:
            raise RuntimeError('identity final sync accepted unsafe permissions')
        run(['mount', '-o', 'remount,rw', str(source)])
        state.chmod(0o600)
        run(['mount', '-o', 'remount,ro', str(source)])
        final = data.finalize(request, staged['receipt_sha256'], deadline)
        copied = target / 'platform-tailscale-state'
        if (final['adoption_accepted'] is not False or not final['copy_verified'] or
                data.tree(copied, deadline) != data.tree(state, deadline) or
                copied.read_bytes() != b'synthetic-identity-after!' or
                copied.stat().st_uid != 0 or copied.stat().st_gid != 2345 or
                copied.stat().st_mode & 0o7777 != 0o600 or (target / 'unrelated').exists()):
            raise RuntimeError('exact identity copy failed content or metadata checks')
        # Recreate a retained source generation on the first synthetic disk.
        # Delete only known fixture paths; no production disk is attached.
        run(['mount', '-o', 'remount,rw', str(source)])
        shutil.copy2(copied, source / copied.name)
        os.chown(source / copied.name, 0, 2345)
        shutil.copy2(target / '.klokast-retained-identity.json', source / '.klokast-retained-identity.json')
        shutil.rmtree(source / 'etc')
        shutil.rmtree(source / 'var')
        run(['mount', '-o', 'remount,ro', str(source)])
        run(['umount', str(target)])
        next_uuid = str(uuid.uuid4())
        run(['mkfs.ext4', '-F', '-U', next_uuid, '/dev/xvdd'])
        run(['mount', '-o', 'nodev,nosuid,noexec', '/dev/xvdd', str(target)])
        next_request = {**request, 'operation_id': ('0' if operation[0] != '0' else '1') + operation[1:],
                        'source_layout': 'retained-data', 'destination_uuid': next_uuid,
                        'entries': [{'key': copied.name, 'source': copied.name, 'type': 'identity-file'}]}
        staged = data.stage(next_request, deadline)
        final = data.finalize(next_request, staged['receipt_sha256'], deadline)
        if not final['copy_verified'] or data.tree(copied, deadline) != data.tree(source / copied.name, deadline):
            raise RuntimeError('retained identity generation verification failed')
        return {'identity_copy_verified': True, 'retained_generation_verified': True,
                'negative_cases': cases, 'production_data_used': False}
    finally:
        run(['umount', str(target)])
        run(['umount', str(source)])


def test(run, operation):
    source, target = data.SOURCE, data.TARGET
    source.mkdir()
    target.mkdir()
    identities = [str(uuid.uuid4()), str(uuid.uuid4())]
    for device, identity, mount in zip(('/dev/xvdc', '/dev/xvdd'), identities, (source, target)):
        run(['mkfs.ext4', '-F', '-U', identity, device])
        run(['mount', '-o', 'nodev,nosuid,noexec', device, str(mount)])
    deadline = time.monotonic() + 120
    cases = []

    def rejected(request, expected):
        try:
            data.copy(request, deadline)
        except data.CopyError as error:
            if expected not in str(error):
                raise
            cases.append(expected)
        else:
            raise RuntimeError('retained-data test accepted an unsafe request: ' + expected)

    try:
        (source / 'etc').mkdir()
        (source / 'etc/passwd').write_text('neo:x:2000:2000:Runtime:/home/neo:/bin/sh\n')
        for key in ('subuid', 'subgid'):
            (source / 'etc' / key).write_text('neo:200000:65536\n')
        library = source / 'srv/music/library'
        library.mkdir(parents=True)
        payload = library / 'track'
        payload.write_bytes(b'synthetic retained contents\n' * 4096)
        os.chown(payload, 200123, 200456)
        payload.chmod(0o640)
        os.setxattr(payload, 'user.klokast-test', b'synthetic metadata')
        os.utime(payload, ns=(1720000000123456789, 1720000000123456789))
        os.link(payload, library / 'linked-track')
        (library / 'symlink').symlink_to('/etc/shadow')
        (library / 'empty').mkdir(mode=0o750)
        with (library / 'sparse').open('wb') as stream:
            stream.seek(4 * 1024 * 1024)
            stream.write(b'end')
        huge = source / 'srv/music/oversized'
        huge.mkdir()
        with (huge / 'sparse').open('wb') as stream:
            stream.truncate(512 * 1024 * 1024)
        unknown = source / 'srv/music/unknown'
        unknown.mkdir()
        os.mkfifo(unknown / 'pipe')
        external = source / 'srv/music/external'
        external.mkdir()
        os.link(payload, external / 'outside-link')
        # The valid mapping has a complete hardlink group after this removal.
        broken = source / 'srv/music/broken'
        broken.mkdir()
        (broken / 'file').write_text('external hardlink')
        os.link(broken / 'file', external / 'other-link')
        (external / 'outside-link').unlink()
        (source / 'srv/music/alias').symlink_to(library)
        request = {'kind': 'klokast.vm-retained-copy.v1', 'operation_id': operation,
                   'source_uuid': identities[0], 'destination_uuid': identities[1],
                   'runtime': {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[200000, 65536]]},
                   'entries': [{'key': 'music-library', 'source': 'srv/music/library'}]}
        rejected(request, 'wrong access')
        run(['mount', '-o', 'remount,ro', str(source)])
        changed = copy.deepcopy(request)
        changed['runtime']['subuid'] = [[300000, 65536]]
        rejected(changed, 'identities changed')
        changed = copy.deepcopy(request)
        changed['destination_uuid'] = str(uuid.uuid4())
        rejected(changed, 'UUID differs')
        for name, message in (('unknown', 'device, socket, or FIFO'), ('broken', 'hardlink extends'),
                              ('alias', 'contains a symlink'), ('oversized', 'insufficient bytes')):
            changed = copy.deepcopy(request)
            changed['entries'][0]['source'] = 'srv/music/' + name
            rejected(changed, message)
        (target / 'unknown-data').write_text('must survive refusal')
        rejected(request, 'unknown or partial data')
        if (target / 'unknown-data').read_text() != 'must survive refusal':
            raise RuntimeError('refused copy changed unknown retained data')
        (target / 'unknown-data').unlink()
        result = data.copy(request, deadline)
        copied = target / 'music-library'
        if (result['adoption_accepted'] is not False or
                (copied / 'track').stat().st_ino != (copied / 'linked-track').stat().st_ino or
                (copied / 'sparse').stat().st_blocks * 512 >= (copied / 'sparse').stat().st_size or
                (copied / 'track').stat().st_uid != 200123 or (copied / 'track').stat().st_gid != 200456):
            raise RuntimeError('copy did not preserve sparse extents, numeric ownership, or hardlinks')
        rejected(request, 'unknown or partial data')
        before = result['entries']['music-library']
        (copied / 'track').write_text('corrupted')
        if data.tree(copied, deadline) == before:
            raise RuntimeError('retained-data verification missed changed bytes')
        cases.append('changed bytes detected')
        return {'copy_verified': True, 'negative_cases': cases, 'production_data_used': False}
    finally:
        run(['umount', str(target)])
        run(['umount', str(source)])


def stage_test(run, operation):
    """Use only the same two disposable synthetic disks as the copy test."""
    source, target = data.SOURCE, data.TARGET
    identities = [str(uuid.uuid4()), str(uuid.uuid4())]
    for device, identity, mount in zip(('/dev/xvdc', '/dev/xvdd'), identities, (source, target)):
        run(['mkfs.ext4', '-F', '-U', identity, device])
        run(['mount', '-o', 'nodev,nosuid,noexec', device, str(mount)])
    deadline = time.monotonic() + 120
    cases = []
    try:
        (source / 'etc').mkdir()
        (source / 'etc/passwd').write_text('neo:x:2000:2000:Runtime:/home/neo:/bin/sh\n')
        for key in ('subuid', 'subgid'):
            (source / 'etc' / key).write_text('neo:200000:65536\n')
        library = source / 'srv/music/library'
        library.mkdir(parents=True)
        payload = library / 'track'
        payload.write_bytes(b'before')
        os.chown(payload, 200123, 200456)
        os.link(payload, library / 'linked-track')
        os.setxattr(payload, 'user.klokast-test', b'before')
        (library / 'removed').write_text('delete only from this synthetic dataset')
        request = {'kind': 'klokast.vm-retained-stage.v1', 'operation_id': operation,
                   'source_uuid': identities[0], 'destination_uuid': identities[1],
                   'runtime': {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[200000, 65536]]},
                   'entries': [{'key': 'music-library', 'source': 'srv/music/library'}], 'source_layout': 'legacy-root'}
        run(['mount', '-o', 'remount,ro', str(source)])
        staged = data.stage(request, deadline)

        def rejected(value, receipt, message):
            try:
                data.finalize(value, receipt, deadline)
            except data.CopyError as error:
                if message not in str(error):
                    raise
                cases.append(message)
            else:
                raise RuntimeError('final sync accepted an unsafe request: ' + message)

        changed = copy.deepcopy(request)
        changed['operation_id'] = ('0' if operation[0] != '0' else '1') + operation[1:]
        rejected(changed, staged['receipt_sha256'], 'exact operation')
        rejected(request, '0' * 64, 'exact operation')
        (target / 'unknown').write_text('keep')
        rejected(request, staged['receipt_sha256'], 'unknown or partial')
        if (target / 'unknown').read_text() != 'keep':
            raise RuntimeError('final sync changed unknown data')
        (target / 'unknown').unlink()
        run(['mount', '-o', 'remount,rw', str(source)])
        previous = payload.stat()
        payload.write_bytes(b'after!')
        os.utime(payload, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        os.setxattr(payload, 'user.klokast-test', b'after')
        (library / 'removed').unlink()
        (library / 'new').write_text('synthetic final write')
        with (library / 'sparse').open('wb') as stream:
            stream.seek(4 * 1024 * 1024); stream.write(b'end')
        run(['mount', '-o', 'remount,ro', str(source)])
        final = data.finalize(request, staged['receipt_sha256'], deadline)
        copied = target / 'music-library'
        if (final['adoption_accepted'] is not False or not final['copy_verified'] or
                data.tree(copied, deadline) != data.tree(library, deadline) or
                (copied / 'track').read_bytes() != b'after!' or (copied / 'removed').exists() or
                (copied / 'track').stat().st_uid != 200123 or (copied / 'track').stat().st_gid != 200456 or
                (copied / 'track').stat().st_ino != (copied / 'linked-track').stat().st_ino or
                (copied / 'sparse').stat().st_blocks * 512 >= (copied / 'sparse').stat().st_size):
            raise RuntimeError('final sync did not preserve synthetic data and ownership')
        rejected(request, staged['receipt_sha256'], 'unknown or partial')
        return {'stage_verified': True, 'final_sync_verified': True,
                'negative_cases': cases, 'production_data_used': False}
    finally:
        run(['umount', str(target)])
        run(['umount', str(source)])
