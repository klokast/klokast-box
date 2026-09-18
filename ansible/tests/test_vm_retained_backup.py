"""Restore the recorded retained generation without reverting later writes."""
import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from test_vm_retained_data import d
import test_vm_backup_restore as restore_tests
import test_vm_backup_evidence as evidence_tests
import vm_backup_verify as verify


class RetainedBackup(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.deadline = time.monotonic() + 60
        self.runtime = {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[300000, 65536]]}
        self.entries = [{'key': key, 'source': key, 'type': 'identity-file'} for key in d.IDENTITY_FILES]
        self.entries.append({'key': 'documents', 'source': 'documents', 'type': 'directory'})
        for key in d.IDENTITY_FILES:
            path = self.root / key; path.write_bytes(b'opaque synthetic identity'); path.chmod(0o600)
        (self.root / 'documents').mkdir()
        (self.root / 'documents/example').write_text('before acceptance')
        self.initial = d.measure_entries({'entries': self.entries}, self.deadline, self.root)
        self.stage = {'kind': 'klokast.vm-retained-stage-result.v3', 'request_sha256': '1' * 64,
                      'entries': self.initial, 'adoption_accepted': False}
        self.stage['receipt_sha256'] = d.digest(self.stage)
        self.final = {'kind': 'klokast.vm-retained-final-result.v3', 'request_sha256': '1' * 64,
                      'stage_receipt_sha256': self.stage['receipt_sha256'], 'entries': self.initial,
                      'copy_verified': True, 'adoption_accepted': False}
        self.final['receipt_sha256'] = d.digest(self.final)
        for name, value in (('.klokast-stage-result.json', self.stage), ('.klokast-final-result.json', self.final),
                            ('.klokast-retained-identity.json', {'kind': 'klokast.vm-retained-identity.v1', 'runtime': self.runtime})):
            d.create_record(self.root / name, value)
        self.request = {'kind': 'klokast.vm-backup-restore.v2', 'operation_id': 'a' * 24,
                        'engine_commit': 'b' * 40, 'backup_receipt_sha256': 'c' * 64, 'disk_sha256': 'd' * 64,
                        'disk_bytes': 16 * 1024**2, 'root_partition': 0,
                        'root_uuid': '11111111-1111-4111-8111-111111111111', 'runtime': self.runtime,
                        'source_layout': 'retained-data', 'retained_receipt_sha256': self.final['receipt_sha256'],
                        'entries': self.entries}

    def contents(self, request=None):
        selected = request or self.request
        d.validate_backup(selected)
        return d.retained_backup_contents(selected, self.root, self.deadline)

    def test_live_generation_can_differ_from_final_sync_contents(self):
        self.assertEqual(self.contents(), self.initial)
        (self.root / 'documents/example').write_text('later production writes')
        (self.root / 'platform-tailscale-state').write_bytes(b'updated synthetic node state')
        measured = self.contents()
        self.assertNotEqual(measured['documents'], self.initial['documents'])
        self.assertNotEqual(measured['platform-tailscale-state'], self.initial['platform-tailscale-state'])
        self.assertEqual(measured['platform-ssh-ed25519'], self.initial['platform-ssh-ed25519'])

    def test_wrong_layout_generation_or_dataset_mapping_is_refused(self):
        for key, value in (('root_partition', 3), ('source_layout', 'legacy-root'),
                           ('retained_receipt_sha256', 'e' * 64), ('kind', 'klokast.vm-backup-restore.v1'),
                           ('entries', self.entries[1:]), ('entries', self.entries[:-1]),
                           ('runtime', dict(self.runtime, uid=2001))):
            request = copy.deepcopy(self.request); request[key] = value
            with self.subTest(key=key), self.assertRaises(d.CopyError): self.contents(request)
        request = copy.deepcopy(self.request); request['entries'][-1]['source'] = 'other'
        with self.assertRaises(d.CopyError): self.contents(request)

    def test_unknown_data_pending_markers_and_nonempty_lost_found_block(self):
        for name in ('unknown', '.klokast-final-pending', '.klokast-copy-pending'):
            path = self.root / name; path.touch()
            with self.subTest(name=name), self.assertRaisesRegex(d.CopyError, 'unknown data'): self.contents()
            path.unlink()
        lost = self.root / 'lost+found'; lost.mkdir()
        self.contents()
        (lost / 'recovered').touch()
        with self.assertRaisesRegex(d.CopyError, 'unknown data'): self.contents()

    def test_changed_or_unsafe_generation_records_are_refused(self):
        path = self.root / '.klokast-stage-result.json'
        path.write_text('{}')
        with self.assertRaisesRegex(d.CopyError, 'stage record'): self.contents()
        path.write_bytes(d.canonical(self.stage)); path.chmod(0o644)
        with self.assertRaisesRegex(d.CopyError, 'unsafe'): self.contents()
        path.chmod(0o600)
        final = self.root / '.klokast-final-result.json'; final.unlink(); final.symlink_to(path)
        with self.assertRaisesRegex(d.CopyError, 'symlink'): self.contents()

    def test_private_identity_permissions_still_apply_to_live_state(self):
        path = self.root / 'platform-ssh-rsa'; path.chmod(0o644)
        with self.assertRaisesRegex(d.CopyError, 'identity file'): self.contents()
        path.chmod(0o600); path.unlink(); path.symlink_to(self.root / 'platform-ssh-ecdsa')
        with self.assertRaisesRegex(d.CopyError, 'symlink'): self.contents()

    def test_restore_path_emits_new_contract_without_reading_legacy_accounts(self):
        legacy = restore_tests.BackupRestore(); legacy.setUp(); self.addCleanup(legacy.doCleanups)
        request = dict(legacy.request, kind=self.request['kind'], source_layout='retained-data',
                       retained_receipt_sha256=self.request['retained_receipt_sha256'], entries=self.entries)
        measured = self.contents()
        with patch.object(d, 'retained_backup_contents', return_value=measured) as inspect:
            result = legacy.execute(request)
        inspect.assert_called_once()
        self.assertEqual(result['kind'], 'klokast.vm-backup-restore-result.v2')
        self.assertEqual(result['entries'], measured)
        self.assertFalse(result['application_consistency_verified'])
        self.assertFalse(result['adoption_accepted'])

    def test_controller_refuses_rehashed_wrong_generation_or_missing_datasets(self):
        evidence = evidence_tests.BackupEvidence(); evidence.setUp()
        request = dict(evidence.request, kind=self.request['kind'], root_partition=0,
                       source_layout='retained-data', retained_receipt_sha256=self.request['retained_receipt_sha256'],
                       entries=self.entries)
        measured = self.contents()
        value = evidence.result
        value['request_sha256'] = d.digest(request)
        value['restore'].update(kind='klokast.vm-backup-restore-result.v2', request_sha256=d.digest(request),
                                source_layout='retained-data', retained_receipt_sha256=request['retained_receipt_sha256'],
                                entries=measured, identity=measured['platform-tailscale-state'])
        value['restore']['receipt_sha256'] = d.digest({k: v for k, v in value['restore'].items() if k != 'receipt_sha256'})
        verify.validate_copy(request, evidence.copy)
        self.assertEqual(verify.verify_result(request, value, '9' * 64), value['restore'])
        for key, replacement in (('retained_receipt_sha256', 'e' * 64), ('source_layout', 'legacy-root'),
                                 ('entries', {}), ('kind', 'klokast.vm-backup-restore-result.v1')):
            bad = copy.deepcopy(value); bad['restore'][key] = replacement
            bad['restore']['receipt_sha256'] = d.digest({k: v for k, v in bad['restore'].items() if k != 'receipt_sha256'})
            with self.subTest(key=key), self.assertRaises(verify.backup.BackupError):
                verify.verify_result(request, bad, '9' * 64)


if __name__ == '__main__': unittest.main()
