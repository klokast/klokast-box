"""Synthetic retained-data acceptance cases, only in the template test VM."""
import copy
import os
from pathlib import Path
import time
import uuid

import retained_data as data


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
