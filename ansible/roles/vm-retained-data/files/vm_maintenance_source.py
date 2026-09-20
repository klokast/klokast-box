"""Validate fixed legacy and accepted retained-data maintenance sources.

Receipts come from the root dom0 transaction reader. They select the backup
source only; they do not authorize stopping or replacing a VM.
"""
import json
from pathlib import Path
import re
import subprocess

import vm_disk_backup as backup

COMMON = {'kind', 'role', 'dom0', 'observed_at', 'vm_uuid', 'configuration_sha256',
          'disks', 'disk_mappings', 'artifacts', 'autostart', 'runtime'}
ACCEPTED = {'operation_id', 'request_sha256', 'release', 'machine', 'files_sha256'}


def details(source, box, role):
    retained = isinstance(source, dict) and source.get('kind') == 'klokast.vm-replacement-source.v1'
    if (not isinstance(source, dict) or set(source) != COMMON | (ACCEPTED if retained else set()) or
            source['kind'] not in {'klokast.vm-unmanaged-source.v1', 'klokast.vm-replacement-source.v1'} or
            source['dom0'] != box + '-dom0' or source['role'] != role or
            source['runtime'] != 'running' or source['autostart'] is not True or
            not isinstance(source['disks'], dict) or not isinstance(source['disk_mappings'], dict) or
            set(source['disks']) != set(source['disk_mappings'])):
        raise backup.BackupError('maintenance source is not the exact running shared guest')
    if retained:
        operation = source['operation_id']
        if not isinstance(operation, str) or not re.fullmatch(r'[0-9a-f]{24}', operation):
            raise backup.BackupError('accepted source has no exact generation')
        root, path = ('/dev/vg0/vmupd_' + operation + suffix for suffix in ('_root', '_data'))
        if source['disk_mappings'] != {root: 'xvda', path: 'xvdb'}:
            raise backup.BackupError('accepted source must have its recorded OS and retained disks')
        release, machine = source['release'], source['machine']
        if (not isinstance(release, dict) or release.get('kind') != 'klokast.vm-release.v2' or
                release.get('release_sha256') != backup.digest({k: v for k, v in release.items() if k != 'release_sha256'}) or
                not isinstance(machine, dict) or set(machine) !=
                {'root_uuid', 'retained_uuid', 'runtime', 'retained_receipt_sha256'} or
                not backup.SHA.fullmatch(machine.get('retained_receipt_sha256', ''))):
            raise backup.BackupError('accepted source lacks protected release or data identity')
    else:
        path = '/dev/vg0/lv_podman_' + role
        if source['disk_mappings'] != {path: 'xvda'}:
            raise backup.BackupError('legacy source must have the exact original shared-VM disk')
    identities = set()
    for name, identity in source['disks'].items():
        if (not isinstance(identity, dict) or set(identity) != {'uuid', 'bytes'} or
                not isinstance(identity['uuid'], str) or not backup.UUID.fullmatch(identity['uuid']) or
                type(identity['bytes']) is not int or identity['bytes'] <= 0 or
                identity['uuid'] in identities):
            raise backup.BackupError('maintenance source disks have invalid or overlapping identities')
        identities.add(identity['uuid'])
    return {'source': dict(source['disks'][path], path=path),
            'layout': 'retained-data' if retained else 'legacy-root',
            'partition': 0 if retained else 3}


def read(role, *, retained=False):
    action = 'replacement-source-status' if retained else 'source-status'
    argv = ['/usr/local/sbin/vm-update-transaction']
    if retained:
        reader = Path(__file__).with_name('vm-update-source-reader')
        backup.secure(reader.parent, True)
        backup.secure(reader)
        argv = ['/usr/bin/python3', str(reader)]
    result = subprocess.run([*argv, action, '--role', role],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
    if result.returncode or not 0 < len(result.stdout) <= 256 * 1024:
        raise backup.BackupError('dom0 source reader failed; the recorded source cannot be substituted')
    return json.loads(result.stdout, object_pairs_hook=backup.unique)


def same_generation(old, current):
    if {k: v for k, v in old.items() if k != 'observed_at'} != {k: v for k, v in current.items() if k != 'observed_at'}:
        raise backup.BackupError('accepted generation changed after backup; reconcile before more maintenance')


def verified_restore(source, copied, verified, box, role, operation, candidate):
    selected = details(source, box, role)
    retained = selected['layout'] == 'retained-data'
    if (copied.get('kind') != 'klokast.vm-disk-backup-result.v1' or
            copied.get('box') != box or copied.get('operation_id') != operation or
            copied.get('source') != selected['source'] or
            copied.get('receipt_sha256') != backup.digest({k: v for k, v in copied.items() if k != 'receipt_sha256'}) or
            verified.get('kind') != 'klokast.vm-verified-backup.' + ('v2' if retained else 'v1') or
            verified.get('box') != box or verified.get('operation_id') != operation or
            verified.get('source') != selected['source'] or
            verified.get('maintenance_candidate') != candidate or
            verified.get('copy_receipt_sha256') != copied['receipt_sha256'] or
            verified.get('receipt_sha256') != backup.digest({k: v for k, v in verified.items() if k != 'receipt_sha256'}) or
            verified.get('restore_verified') is not True or verified.get('cleanup_verified') is not True):
        raise backup.BackupError('candidate requires the exact verified backup and source layout')
    if retained and (verified.get('source_layout') != 'retained-data' or
                     verified.get('retained_receipt_sha256') != source['machine']['retained_receipt_sha256'] or
                     verified.get('root_uuid') != source['machine']['retained_uuid'] or
                     verified.get('runtime') != source['machine']['runtime']):
        raise backup.BackupError('restored data differs from the accepted retained identity')
    return selected
