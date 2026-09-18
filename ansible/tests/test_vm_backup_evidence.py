"""Refuse mixed backup generations and incomplete restore evidence."""
import copy
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ansible/roles/vm-retained-data/files'))
import vm_backup_verify as v


class BackupEvidence(unittest.TestCase):
    def setUp(self):
        self.copy = {'kind': 'klokast.vm-disk-backup-result.v1', 'operation_id': 'a' * 24,
            'box': 'boxa', 'engine_commit': 'b' * 40, 'request_sha256': 'c' * 64,
            'source_evidence_sha256': 'd' * 64,
            'source': {'path': '/dev/vg0/old', 'uuid': '111111-1111-1111-1111-1111-1111-111111', 'bytes': 16 * 1024**2},
            'backup': {'path': '/dev/vg0/vmbackup_' + 'a' * 24 + '_disk',
                       'uuid': '222222-2222-2222-2222-2222-2222-222222', 'bytes': 16 * 1024**2},
            'disk_sha256': 'e' * 64, 'snapshot_at': time.time() - 30, 'completed_at': time.time() - 10,
            'independent_copy': True, 'backup_readonly': True, 'restore_verified': False,
            'source_freshness_verified': False, 'application_consistency_verified': False, 'adoption_accepted': False}
        self.copy['receipt_sha256'] = v.backup.digest(self.copy)
        self.request = {'kind': 'klokast.vm-backup-restore.v1', 'operation_id': 'a' * 24, 'engine_commit': 'b' * 40,
                        'backup_receipt_sha256': self.copy['receipt_sha256'], 'disk_sha256': 'e' * 64,
                        'disk_bytes': 16 * 1024**2, 'root_partition': 3,
                        'root_uuid': '11111111-1111-4111-8111-111111111111',
                        'runtime': {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[200000, 65536]]}}
        restore = {'kind': 'klokast.vm-backup-restore-result.v1', 'request_sha256': v.data.digest(self.request),
                   **{k: self.request[k] for k in ('backup_receipt_sha256', 'disk_sha256', 'disk_bytes', 'root_uuid', 'runtime')},
                   'identity': {'sha256': 'f' * 64, 'entries': 1, 'required_bytes': 4096},
                   'complete_disk_restored': True, 'root_filesystem_checked': True, 'backup_unchanged': True,
                   'source_freshness_verified': False, 'application_consistency_verified': False, 'adoption_accepted': False}
        restore['receipt_sha256'] = v.data.digest(restore)
        self.result = {'kind': 'klokast.vm-backup-restore-guest.v1', 'operation_id': 'a' * 24,
                       'inputs_sha256': '9' * 64, 'request_sha256': v.data.digest(self.request), 'success': True, 'restore': restore}

    def test_exact_copy_and_restore_bindings_pass_without_adoption(self):
        v.validate_copy(self.request, self.copy)
        self.assertEqual(v.verify_result(self.request, self.result, '9' * 64), self.result['restore'])
        self.assertFalse(self.result['restore']['adoption_accepted'])

    def test_changed_copy_or_request_cannot_mix_generations(self):
        for key, value in (('operation_id', '0' * 24), ('engine_commit', '0' * 40), ('disk_sha256', '0' * 64),
                           ('disk_bytes', 32 * 1024**2), ('backup_receipt_sha256', '0' * 64)):
            with self.subTest(key=key), self.assertRaises(v.backup.BackupError):
                v.validate_copy(dict(self.request, **{key: value}), self.copy)
        bad = copy.deepcopy(self.copy); bad['source_evidence_sha256'] = '0' * 64
        with self.assertRaises(v.backup.BackupError): v.validate_copy(self.request, bad)

    def test_rehashed_unsafe_copy_fields_still_fail(self):
        for change in (
            lambda x: x.update(backup_readonly=False),
            lambda x: x.update(adoption_accepted=True),
            lambda x: x['backup'].update(uuid=x['source']['uuid']),
            lambda x: x['backup'].update(path='/dev/vg0/unrelated'),
            lambda x: x.update(snapshot_at=float('inf')),
            lambda x: x.update(completed_at=time.time() + 60),
            lambda x: x.update(extra='caller authority'),
        ):
            bad = copy.deepcopy(self.copy); change(bad)
            bad['receipt_sha256'] = v.backup.digest({k: value for k, value in bad.items() if k != 'receipt_sha256'})
            request = dict(self.request, backup_receipt_sha256=bad['receipt_sha256'])
            with self.subTest(change=change), self.assertRaises(v.backup.BackupError): v.validate_copy(request, bad)

    def test_wrong_guest_generation_or_failed_result_is_refused(self):
        for key, value in (('operation_id', '0' * 24), ('inputs_sha256', '0' * 64), ('success', False),
                           ('request_sha256', '0' * 64), ('extra', 'unexpected')):
            with self.subTest(key=key), self.assertRaises(v.backup.BackupError):
                v.verify_result(self.request, dict(self.result, **{key: value}), '9' * 64)

    def test_rehashed_restore_cannot_claim_missing_checks_or_authority(self):
        for key, value in (('backup_unchanged', False), ('root_filesystem_checked', False),
                           ('application_consistency_verified', True), ('adoption_accepted', True),
                           ('disk_bytes', 32 * 1024**2), ('backup_receipt_sha256', '0' * 64),
                           ('identity', {'sha256': 'f' * 64, 'entries': 2, 'required_bytes': 4096})):
            bad = copy.deepcopy(self.result); bad['restore'][key] = value
            bad['restore']['receipt_sha256'] = v.data.digest({k: value for k, value in bad['restore'].items() if k != 'receipt_sha256'})
            with self.subTest(key=key), self.assertRaises(v.backup.BackupError): v.verify_result(self.request, bad, '9' * 64)


if __name__ == '__main__': unittest.main()
