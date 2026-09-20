"""Accepted-generation source binding for recurring retained-data maintenance."""
import copy
from contextlib import nullcontext
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'roles/vm-retained-data/files'))
import vm_maintenance_source as source
import vm_disk_backup as backup
import test_vm_update_transaction as transaction_tests


def load(name, file):
    loader = SourceFileLoader(name, str(file))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class MaintenanceSource(unittest.TestCase):
    def setUp(self):
        self.operation = 'a' * 24
        self.os_path = '/dev/vg0/vmupd_' + self.operation + '_root'
        self.data_path = '/dev/vg0/vmupd_' + self.operation + '_data'
        self.disks = {self.os_path: {'uuid': 'AAAAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAAAA', 'bytes': 4 * 1024**3},
                      self.data_path: {'uuid': 'BBBBBB-BBBB-BBBB-BBBB-BBBB-BBBB-BBBBBB', 'bytes': 1024**3}}
        self.release = {'kind': 'klokast.vm-release.v2', 'packages': {'podman': '5'}, 'inputs_sha256': 'b' * 64}
        self.release['release_sha256'] = backup.digest(self.release)
        self.value = {'kind': 'klokast.vm-replacement-source.v1', 'role': 'dmz', 'dom0': 'boxa-dom0',
                      'observed_at': 1, 'vm_uuid': 'vm-uuid', 'configuration_sha256': 'c' * 64,
                      'disks': self.disks, 'disk_mappings': {self.os_path: 'xvda', self.data_path: 'xvdb'},
                      'artifacts': {}, 'autostart': True, 'runtime': 'running',
                      'operation_id': self.operation, 'request_sha256': 'd' * 64, 'release': self.release,
                      'machine': {'root_uuid': 'root', 'retained_uuid': 'data', 'runtime': {},
                                  'retained_receipt_sha256': 'e' * 64}, 'files_sha256': {}}

    def test_selects_only_the_accepted_retained_disk(self):
        result = source.details(self.value, 'boxa', 'dmz')
        self.assertEqual(result, {'source': dict(self.disks[self.data_path], path=self.data_path),
                                  'layout': 'retained-data', 'partition': 0})

    def test_refuses_alias_drift_and_unrelated_disk(self):
        for mutate in (lambda v: v.update(runtime='stopped'), lambda v: v.update(autostart=False),
                       lambda v: v['disk_mappings'].update({self.data_path: 'xvda'}),
                       lambda v: v['disks'][self.data_path].update(uuid='AAAAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAAAA'),
                       lambda v: v['release']['packages'].update(podman='changed'),
                       lambda v: v.update(operation_id='f' * 24),
                       lambda v: v.update(extra='caller authority')):
            value = copy.deepcopy(self.value); mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(backup.BackupError):
                source.details(value, 'boxa', 'dmz')

    def test_legacy_source_remains_exact(self):
        value = {k: v for k, v in self.value.items() if k in source.COMMON}
        value.update(kind='klokast.vm-unmanaged-source.v1',
                     disks={'/dev/vg0/lv_podman_dmz': {'uuid': 'CCCCCC-CCCC-CCCC-CCCC-CCCC-CCCC-CCCCCC', 'bytes': 4096}},
                     disk_mappings={'/dev/vg0/lv_podman_dmz': 'xvda'})
        self.assertEqual(source.details(value, 'boxa', 'dmz')['partition'], 3)
        value['disk_mappings'] = {self.data_path: 'xvdb'}
        with self.assertRaises(backup.BackupError): source.details(value, 'boxa', 'dmz')

    def test_recheck_allows_only_new_observation_time(self):
        source.same_generation(self.value, dict(self.value, observed_at=2))
        with self.assertRaises(backup.BackupError):
            source.same_generation(self.value, dict(self.value, request_sha256='f' * 64))

    def test_reader_does_not_fallback_after_error(self):
        with patch.object(source.backup, 'secure'), \
                patch.object(source.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='')) as run:
            with self.assertRaises(backup.BackupError): source.read('dmz', retained=True)
            self.assertEqual(run.call_count, 1)
            self.assertIn('replacement-source-status', run.call_args.args[0])

    def test_small_retained_backup_bounds_snapshot_and_rechecks_source(self):
        import contextlib
        import io
        module = load('maintenance_backup_test', ROOT / 'roles/vm-retained-data/files/vm-supervised-backup')
        records = []
        current = dict(self.value, observed_at=100)
        current['dom0'] = 'k002-dom0'
        argv = ['backup', '--box', 'k002', '--role', 'dmz', '--operation-id', 'f' * 24,
                '--engine-commit', 'a' * 40, '--source-layout', 'retained-data']
        with patch.object(sys, 'argv', argv), patch.object(module.os, 'geteuid', return_value=0), \
                patch.object(module.socket, 'gethostname', return_value='k002-dom0'), \
                patch('time.time', return_value=100), patch.object(backup, 'secure'), \
                patch.object(backup, 'store'), patch.object(source, 'read', return_value=current) as read, \
                patch.object(backup, 'make_backup', side_effect=lambda path, req: records.append(req) or {'copied': True}), \
                contextlib.redirect_stdout(io.StringIO()):
            module.main()
            self.assertEqual(read.call_count, 2)
            request = records[0]
            self.assertEqual(request['cow_bytes'], self.disks[self.data_path]['bytes'])
            self.assertEqual(request['source']['path'], self.data_path)
            backup.validate(request)
            read.side_effect = [current, dict(current, request_sha256='a' * 64)]
            with self.assertRaisesRegex(backup.BackupError, 'changed after backup'): module.main()

    def test_verified_restore_cannot_mix_legacy_and_retained_receipts(self):
        copied = {'kind': 'klokast.vm-disk-backup-result.v1', 'operation_id': 'f' * 24, 'box': 'boxa',
                  'source': dict(self.disks[self.data_path], path=self.data_path)}
        copied['receipt_sha256'] = backup.digest(copied)
        verified = {'kind': 'klokast.vm-verified-backup.v2', 'operation_id': 'f' * 24, 'box': 'boxa',
                    'source': copied['source'], 'copy_receipt_sha256': copied['receipt_sha256'],
                    'maintenance_candidate': 'b' * 24, 'restore_verified': True, 'cleanup_verified': True,
                    'source_layout': 'retained-data', 'root_uuid': 'data', 'runtime': {},
                    'retained_receipt_sha256': 'e' * 64}
        verified['receipt_sha256'] = backup.digest(verified)
        args = (self.value, copied, verified, 'boxa', 'dmz', 'f' * 24, 'b' * 24)
        self.assertEqual(source.verified_restore(*args)['partition'], 0)
        for key, value in (('kind', 'klokast.vm-verified-backup.v1'), ('source_layout', 'legacy-root'),
                           ('retained_receipt_sha256', 'f' * 64), ('root_uuid', 'other'),
                           ('copy_receipt_sha256', 'b' * 64), ('cleanup_verified', False)):
            previous = dict(verified)
            verified[key] = value
            verified['receipt_sha256'] = backup.digest({k: v for k, v in verified.items() if k != 'receipt_sha256'})
            with self.subTest(key=key), self.assertRaises(backup.BackupError): source.verified_restore(*args)
            verified.clear(); verified.update(previous)

    def generation_records(self):
        prepared = {'operation_id': self.operation, 'box': 'boxa', 'role': 'dmz',
                    'root': dict(self.disks[self.os_path], path=self.os_path),
                    'data': dict(self.disks[self.data_path], path=self.data_path),
                    'root_uuid': 'root', 'data_uuid': 'data'}
        prepared['receipt_sha256'] = backup.digest(prepared)
        receipt = {'kind': 'klokast.vm-retained-final-result.v3', 'copy_verified': True}
        receipt['receipt_sha256'] = backup.digest(receipt)
        machine = {'kind': 'klokast.vm-personalize.v2', 'operation_id': self.operation,
                   'box': 'boxa', 'role': 'dmz', 'engine_commit': 'f' * 40, 'root_uuid': 'root', 'retained_uuid': 'data',
                   'retained_receipt_sha256': receipt['receipt_sha256'], 'runtime': {'uid': 1000},
                   'release_sha256': self.release['release_sha256'], 'packages': self.release['packages'],
                   'inputs_sha256': self.release['inputs_sha256'], 'files': {'etc/hostname': 'boxa-dmz\n'},
                   'admin_password_hash': 'private-password-value'}
        wrapper = {'mode': 'personalize', 'operation_id': self.operation, 'request': machine}
        files = {path: {'sha256': backup.digest(path), 'mode': 0o600} for path in {
            'etc/passwd', 'etc/group', 'etc/shadow', 'etc/subuid', 'etc/subgid', 'etc/fstab',
            'etc/conf.d/tailscale', 'etc/doas.d/doas.conf', 'etc/klokast/app-resources/vm-input.d/000-empty.nft',
            *('etc/ssh/ssh_host_' + key + '_key' for key in ('rsa', 'ecdsa', 'ed25519'))}}
        import hashlib
        files.update({path: {'sha256': hashlib.sha256(content.encode()).hexdigest(), 'mode': 0o644}
                      for path, content in machine['files'].items()})
        personal = {key: machine[key] for key in ('operation_id', 'release_sha256', 'inputs_sha256',
                    'engine_commit', 'root_uuid', 'retained_uuid', 'runtime', 'retained_receipt_sha256')}
        personal.update(kind='klokast.vm-personalization-result.v2', request_sha256=backup.digest(machine),
                        packages_unchanged=True, hostname='boxa-dmz', files=files)
        return {'prepared.json': prepared,
                'personalize/request.json': wrapper,
                'personalize/result.json': {'mode': 'personalize', 'operation_id': self.operation,
                    'success': True, 'request_sha256': backup.digest(wrapper), 'receipt': personal},
                'finalize/result.json': {'mode': 'finalize', 'operation_id': self.operation, 'success': True, 'receipt': receipt},
                'release.json': self.release}

    def test_root_reader_binds_provenance_and_redacts_private_fields(self):
        t = transaction_tests.t
        records = self.generation_records()
        assignment = {'stage': 'complete', 'selection': 'accepted', 'runtime': 'running', 'autostart': True,
                      'configuration_drift': False, 'autostart_drift': False, 'disks': self.disks,
                      'boot_configuration': {'disk': ['phy:' + self.os_path + ',xvda,w', 'phy:' + self.data_path + ',xvdb,w']},
                      'release_sha256': self.release['release_sha256'], 'vm_uuid': 'vm-uuid',
                      'request_sha256': 'd' * 64, 'configuration_sha256': 'c' * 64, 'artifacts': {}}
        tx = SimpleNamespace(work=Path('/state') / self.operation, request={'box': 'boxa'})
        def read(path):
            relative = str(path).split(self.operation + '/', 1)[1]
            return records[relative]
        with patch.object(t, 'current_assignment', return_value=nullcontext(tx)), \
                patch.object(t, 'assignment_report', return_value=assignment), \
                patch.object(t, 'secure'), patch.object(t, 'read', side_effect=read), \
                patch.object(t.socket, 'gethostname', return_value='boxa-dom0'):
            result = t.replacement_source_status('dmz')
            self.assertNotIn('private-password-value', json.dumps(result))
            self.assertIn('etc/shadow', result['files_sha256'])
            self.assertIn('etc/klokast-personalization.json', result['files_sha256'])
            records['personalize/result.json']['success'] = False
            with self.assertRaises(t.Refused): t.replacement_source_status('dmz')
            records['personalize/result.json']['success'] = True
            self.assertEqual(source.details(result, 'boxa', 'dmz')['source']['path'], self.data_path)
            records['personalize/request.json']['request']['retained_uuid'] = 'wrong'
            with self.assertRaises(t.Refused): t.replacement_source_status('dmz')
            records['personalize/request.json']['request']['retained_uuid'] = 'data'
            assignment['configuration_drift'] = True
            with self.assertRaises(t.Refused): t.replacement_source_status('dmz')


if __name__ == '__main__': unittest.main()
