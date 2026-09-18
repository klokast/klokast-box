"""Connect a protected independent backup receipt to an isolated restore boot.

Called only by a root-owned workflow holding installation and box leases.
This module selects no production release and grants no adoption authority.
Dom0 treats all guest disks as opaque blocks, including the restored copy.
"""
import hashlib
import math
import os
from pathlib import Path
import re
import time
import uuid

import retained_data as data
import vm_disk_backup as backup


def validate_copy(request, receipt):
    data.validate_backup(request)
    fields = {'kind', 'operation_id', 'box', 'engine_commit', 'request_sha256', 'source_evidence_sha256',
              'source', 'backup', 'disk_sha256', 'snapshot_at', 'completed_at', 'independent_copy',
              'backup_readonly', 'restore_verified', 'source_freshness_verified',
              'application_consistency_verified', 'adoption_accepted', 'receipt_sha256'}
    if (not isinstance(receipt, dict) or set(receipt) != fields or receipt['kind'] != 'klokast.vm-disk-backup-result.v1' or
            receipt['receipt_sha256'] != backup.digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'}) or
            receipt['receipt_sha256'] != request['backup_receipt_sha256'] or
            receipt['operation_id'] != request['operation_id'] or receipt['engine_commit'] != request['engine_commit'] or
            receipt['disk_sha256'] != request['disk_sha256'] or
            any(receipt[k] is not True for k in ('independent_copy', 'backup_readonly')) or
            any(receipt[k] is not False for k in ('restore_verified', 'source_freshness_verified',
                                               'application_consistency_verified', 'adoption_accepted'))):
        raise backup.BackupError('independent backup receipt differs from the restore request')
    for key in ('source', 'backup'):
        item = receipt[key]
        if (not isinstance(item, dict) or set(item) != {'path', 'uuid', 'bytes'} or
                not isinstance(item['path'], str) or not backup.LV.fullmatch(item['path']) or
                not isinstance(item['uuid'], str) or not backup.UUID.fullmatch(item['uuid']) or
                type(item['bytes']) is not int or item['bytes'] != request['disk_bytes']):
            raise backup.BackupError('backup receipt has no exact independent LV identity')
    if receipt['source']['path'] == receipt['backup']['path'] or receipt['source']['uuid'] == receipt['backup']['uuid']:
        raise backup.BackupError('backup receipt aliases its original source')
    expected = '/dev/' + backup.LV.fullmatch(receipt['source']['path'])[1] + '/vmbackup_' + request['operation_id'] + '_disk'
    if receipt['backup']['path'] != expected:
        raise backup.BackupError('backup receipt names an unrelated operation allocation')
    if (not isinstance(receipt['box'], str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', receipt['box']) or
            any(not isinstance(receipt[k], str) or not backup.SHA.fullmatch(receipt[k])
                for k in ('request_sha256', 'source_evidence_sha256')) or
            any(type(receipt[k]) not in (int, float) or not math.isfinite(receipt[k]) for k in ('snapshot_at', 'completed_at')) or
            not 0 < receipt['snapshot_at'] <= receipt['completed_at'] <= time.time() + 5):
        raise backup.BackupError('backup receipt has invalid source evidence or timestamps')


def verify_result(request, value, inputs_sha256):
    fields = {'kind', 'operation_id', 'inputs_sha256', 'request_sha256', 'success', 'restore'}
    if (not isinstance(value, dict) or set(value) != fields or value['kind'] != 'klokast.vm-backup-restore-guest.v1' or
            value['operation_id'] != request['operation_id'] or value['inputs_sha256'] != inputs_sha256 or
            value['request_sha256'] != data.digest(request) or value['success'] is not True):
        raise backup.BackupError('maintenance guest returned incomplete or mismatched restore evidence')
    receipt = value['restore']
    if (not isinstance(receipt, dict) or set(receipt) != {
            'kind', 'request_sha256', 'backup_receipt_sha256', 'disk_sha256', 'disk_bytes', 'root_uuid', 'runtime',
            'identity', 'complete_disk_restored', 'root_filesystem_checked', 'backup_unchanged',
            'source_freshness_verified', 'application_consistency_verified', 'adoption_accepted', 'receipt_sha256'} or
            receipt['kind'] != 'klokast.vm-backup-restore-result.v1' or
            receipt['request_sha256'] != data.digest(request) or
            any(receipt[k] != request[k] for k in ('backup_receipt_sha256', 'disk_sha256', 'disk_bytes', 'root_uuid', 'runtime')) or
            any(receipt[k] is not True for k in ('complete_disk_restored', 'root_filesystem_checked', 'backup_unchanged')) or
            any(receipt[k] is not False for k in ('source_freshness_verified', 'application_consistency_verified', 'adoption_accepted')) or
            receipt['receipt_sha256'] != data.digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})):
        raise backup.BackupError('restore receipt does not prove the exact backup contents and numeric identity')
    identity = receipt['identity']
    if (not isinstance(identity, dict) or set(identity) != {'sha256', 'entries', 'required_bytes'} or
            not isinstance(identity['sha256'], str) or not backup.SHA.fullmatch(identity['sha256']) or
            type(identity['entries']) is not int or identity['entries'] != 1 or
            type(identity['required_bytes']) is not int or not 4096 <= identity['required_bytes'] <= 8 * backup.MIB or
            identity['required_bytes'] % 4096):
        raise backup.BackupError('restore receipt has invalid private identity measurements')
    return receipt


def verify(work, request, copy_receipt, candidate, host, native=None):
    """Keep the backup; delete only this operation's disposable restore assets."""
    validate_copy(request, copy_receipt)
    backup.secure(work, True)
    if (work / 'journal.json').exists() or (work / 'result.json').exists():
        raise backup.BackupError('backup restore operation cannot be reused')
    if any(work.iterdir()):
        raise backup.BackupError('restore staging contains unrecorded files; allocate a new empty directory')
    native = native or backup.Native()
    deadline = time.monotonic() + 3600
    operation = request['operation_id']
    if (candidate.parent != Path('/mnt/dom0_data/klokast-vm-templates/candidates') or
            not re.fullmatch('[0-9a-f]{24}', candidate.name)):
        raise backup.BackupError('maintenance template is not a recorded candidate')
    host.safe_directory(candidate)
    manifest_path = candidate / 'candidate.json'
    host.safe_file(manifest_path, backup.MIB)
    import json
    manifest = json.loads(manifest_path.read_text(), object_pairs_hook=backup.unique)
    if (manifest.get('operation_id') != candidate.name or manifest.get('kind') != 'klokast.vm-template-candidate.v1' or
            manifest.get('validation') != 'base-boot-tested' or manifest.get('accepted') is not False or
            not isinstance(manifest.get('inputs_sha256'), str) or not backup.SHA.fullmatch(manifest['inputs_sha256']) or
            manifest.get('boot_test', {}).get('maintenance_restore', {}).get('success') is not True):
        raise backup.BackupError('maintenance candidate lacks the required separate restore boot test')
    artifacts = manifest.get('artifacts', {})
    if set(artifacts) != {'root', 'kernel', 'initramfs'}:
        raise backup.BackupError('maintenance candidate has incomplete boot artifacts')
    for name, maximum in (('root', 4 * 1024**3), ('kernel', 32 * backup.MIB), ('initramfs', 128 * backup.MIB)):
        item, path = artifacts[name], candidate / name
        host.safe_file(path, maximum)
        if (not isinstance(item, dict) or set(item) != {'bytes', 'sha256'} or
                type(item['bytes']) is not int or item['bytes'] != path.stat().st_size or
                host.checksum(path) != item['sha256']):
            raise backup.BackupError('maintenance artifact differs from its protected candidate')
    volume = native.volume(copy_receipt['backup']['path'])
    def check_backup():
        current = native.volume(volume['path'])
        backup.same_identity(current, volume)
        if any(current[k] != copy_receipt['backup'][k] for k in ('path', 'uuid', 'bytes')):
            raise backup.BackupError('backup LV differs from its protected copy receipt')
        if current['tags'] != ['klokast.vm-backup.' + operation + '.disk']:
            raise backup.BackupError('backup allocation tags differ from the recorded operation')
        backup.ordinary(current, readonly=True)
        native.unmounted([current]); native.unattached([current])
        current['device_bytes'] = request['disk_bytes']
        value = hashlib.sha256()
        with native.open(current) as stream:
            count = request['disk_bytes']
            while count:
                data.remaining(deadline)
                chunk = stream.read(min(4 * backup.MIB, count))
                if not chunk: raise backup.BackupError('independent backup read was truncated')
                value.update(chunk); count -= len(chunk)
        if value.hexdigest() != request['disk_sha256']:
            raise backup.BackupError('independent backup changed before or after the restore boot')
    check_backup()
    vg = backup.LV.fullmatch(volume['path'])[1]
    restore_path = '/dev/' + vg + '/vmrestore_' + operation
    restore_tag = 'klokast.vm-restore.' + operation
    if any(v['lv_path'] == restore_path or restore_tag in v['lv_tags'].split(',') for v in native.volumes()):
        raise backup.BackupError('disposable restore LV already exists')
    free, extent = native.capacity(vg)
    capacity = os.statvfs(work)
    xen = {key.strip(): value.strip() for key, value in
           (line.split(':', 1) for line in host.run(['xl', 'info']).stdout.splitlines() if ':' in line)}
    if (free < request['disk_bytes'] + 1024 * backup.MIB or extent <= 0 or request['disk_bytes'] % extent or
            capacity.f_bavail * capacity.f_frsize < artifacts['root']['bytes'] + 1024 * backup.MIB or
            int(xen.get('free_memory', '0').strip()) < 5120):
        raise backup.BackupError('isolated restore needs an independent LV, OS copy, 1 GiB reserves, and 5 GiB free Xen memory')
    name, identity = 'vm-maintenance-backup-' + operation, str(uuid.uuid4())
    if host.domain(name) is not None:
        raise backup.BackupError('backup maintenance domain already exists')
    root, payload, result_slot = (work / n for n in ('root.slot', 'request.slot', 'result.slot'))
    config, loops, restore = work / 'maintenance.cfg', {}, None
    journal = {'kind': 'klokast.vm-backup-verification-journal.v1', 'request_sha256': data.digest(request),
               'copy_receipt_sha256': copy_receipt['receipt_sha256'], 'candidate': candidate.name,
               'domain': name, 'uuid': identity, 'restore_path': restore_path, 'restore_tag': restore_tag,
               'stage': 'prepared', 'started_at': time.time()}
    backup.store(work / 'request.json', request)
    backup.store(work / 'copy-receipt.json', copy_receipt)
    def record(stage):
        journal.update(stage=stage, updated_at=time.time())
        backup.store(work / 'journal.json', journal)
    record('prepared')
    try:
        record('allocating-restore')
        native.create(restore_path, request['disk_bytes'], restore_tag)
        restore = native.volume(restore_path)
        if (restore['bytes'] != request['disk_bytes'] or restore['tags'] != [restore_tag] or
                restore['uuid'] in (volume['uuid'], copy_receipt['source']['uuid']) or
                restore['device'] == volume['device']):
            raise backup.BackupError('restore allocation aliases protected storage or has wrong tags')
        backup.ordinary(restore); native.unmounted([restore]); native.unattached([restore])
        journal['restore'] = restore
        record('allocated-restore')
        with (candidate / 'root').open('rb') as source, root.open('xb') as target:
            os.posix_fallocate(target.fileno(), 0, artifacts['root']['bytes'])
            while chunk := source.read(backup.MIB):
                data.remaining(deadline)
                if target.write(chunk) != len(chunk): raise backup.BackupError('maintenance OS copy was short')
            target.flush(); os.fsync(target.fileno())
        if host.checksum(root) != artifacts['root']['sha256']:
            raise backup.BackupError('maintenance OS copy differs from its candidate')
        with payload.open('xb') as stream:
            stream.write(data.canonical(request) + b'\0'); stream.truncate(backup.MIB)
            stream.flush(); os.fsync(stream.fileno())
        with result_slot.open('xb') as stream:
            stream.truncate(backup.MIB); stream.flush(); os.fsync(stream.fileno())
        for path in (root, result_slot, payload):
            loops[path] = host.attach_loop(path, readonly=path == payload)
            journal['loops'] = {str(k): v for k, v in loops.items()}
            record('attached-slots')
        disks = [f'phy:{loops[root]},xvda,w', f'phy:{loops[result_slot]},xvdb,w',
                 f'phy:{volume["path"]},xvdc,r', f'phy:{restore_path},xvdd,w', f'phy:{loops[payload]},xvde,r']
        extra = ('root=/dev/xvda rootfstype=ext4 rw modules=ext4 console=hvc0 '
                 'init=/usr/local/libexec/retained_data.py klokast_backup_restore=' + operation +
                 ' klokast_inputs=' + manifest['inputs_sha256'] + ' klokast_request_sha256=' + data.digest(request))
        config.write_text(f'name = {name!r}\nuuid = {identity!r}\ntype = "pvh"\nmemory = 4096\nmaxmem = 4096\nvcpus = 2\n'
                          f'kernel = {str(candidate / "kernel")!r}\nramdisk = {str(candidate / "initramfs")!r}\n'
                          f'extra = {extra!r}\ndisk = {disks!r}\nvif = []\n'
                          'on_poweroff = "destroy"\non_reboot = "destroy"\non_crash = "destroy"\n')
        record('booting-restore')
        host.boot_guest(work, name, identity, config,
                        {'operation_id': operation, 'inputs_sha256': manifest['inputs_sha256']},
                        slot=result_slot.name, kind='klokast.vm-backup-restore-guest.v1', timeout=min(1800, data.remaining(deadline)))
        value = host.read_slot(result_slot)
        backup.store(work / 'guest-result.json', value)
        receipt = verify_result(request, value, manifest['inputs_sha256'])
        check_backup()
        record('restore-verified')
    finally:
        if host.domain(name) is not None:
            raise backup.BackupError('backup maintenance guest remains; retain all restore assets')
        for path, device in list(loops.items()):
            host.detach_loop(path, device); del loops[path]
        if restore is not None:
            current = native.volume(restore_path); backup.same_identity(current, restore)
            backup.ordinary(current)
            if current['tags'] != [restore_tag]: raise backup.BackupError('restore cleanup tags changed; retain this LV')
            native.unmounted([current]); native.unattached([current])
            native.remove(restore_path)
        for path in (root, payload, result_slot, config): path.unlink(missing_ok=True)
        record('cleaned')
    result = {'kind': 'klokast.vm-verified-backup.v1', 'operation_id': operation, 'box': copy_receipt['box'],
              'engine_commit': request['engine_commit'], 'source': copy_receipt['source'], 'backup': copy_receipt['backup'],
              'copy_receipt_sha256': copy_receipt['receipt_sha256'], 'restore_receipt_sha256': receipt['receipt_sha256'],
              'disk_sha256': request['disk_sha256'], 'maintenance_candidate': candidate.name,
              'maintenance_inputs_sha256': manifest['inputs_sha256'], 'snapshot_at': copy_receipt['snapshot_at'],
              'verified_at': time.time(), 'identity': receipt['identity'], 'restore_verified': True,
              'root_uuid': request['root_uuid'], 'runtime': request['runtime'],
              'cleanup_verified': True, 'source_freshness_verified': False,
              'application_consistency_verified': False, 'adoption_accepted': False}
    result['receipt_sha256'] = backup.digest(result)
    backup.store(work / 'result.json', result)
    return result
