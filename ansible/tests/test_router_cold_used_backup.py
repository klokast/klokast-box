"""A used backup requires completed recovery and exact LV absence on retry."""
from pathlib import Path
import sys
import unittest
import contextlib
import io
import json
import os
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_cycle as cycle
import router_cold_health as health
import router_cold_test_state as test_state
import router_cold_filesystem as filesystem
import router_executor as executor
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_cold_disk as fixtures


class UsedBackupTests(unittest.TestCase):
    save = fixtures.ColdDiskTests.save

    def setUp(self):
        fixtures.ColdDiskTests.setUp(self)
        self.storage.base = self.bundle.directory
        self.storage.cold_test.return_value = None
        self.host.guest.return_value = ('router', {})
        self.host.inventory.return_value = []
        records.write(self.bundle.directory / 'accepted.json', self.storage.accepted.return_value)
        self.save(stage='copied', source_sha256='d' * 64)
        (self.bundle.directory / 'filesystem').mkdir(mode=0o700)
        self.completion = generations.seal({'kind': 'klokast.router-cold-completion.v1',
            'metadata_sha256': self.metadata['record_sha256'],
            'generation_sha256': self.generation['record_sha256'], 'cleared_at': 20})
        records.write(self.bundle.directory / 'completion.json', self.completion)
        self.returned = generations.seal({'kind': 'klokast.router-cold-supervisor-result.v1',
            'box': self.storage.box, 'operation_id': self.bundle.operation,
            'engine_commit': self.bundle.engine, 'reason': 'controller-return',
            'status': 'original-running-fenced', 'finished_at': 10})
        self.test_source = generations.seal({'initial_operation': 'b' * 24, 'status': 'no-device', 'machine_id': None})
        self.device_receipt = generations.seal({'kind': 'klokast.router-cold-device-cleanup.v1',
            'box': self.storage.box, 'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
            'source_sha256': self.test_source['record_sha256'], 'status': 'no-device',
            'machine_id': None, 'provider_id': None})
        self.health = Mock()
        self.health.clear_fence.return_value = self.completion
        self.test = Mock()
        self.test.cleanup_source.return_value = self.test_source
        for target, name, options in (
                (health, 'Health', {'return_value': self.health}),
                (test_state, 'TestState', {'return_value': self.test}),
                (cycle.Cycle, 'status', {'side_effect': lambda: {'result': self.returned}}),
                (filesystem, 'loops', {'return_value': []}),
                (fixtures.cold.disks, 'refuse_referenced_disk', {})):
            changed = patch.object(target, name, **options)
            changed.start(); self.addCleanup(changed.stop)
        self.boot_check = Mock(return_value='accepted-assignment-verified')
        changed = patch.object(fixtures.cold, 'retirement_checksum',
            side_effect=lambda backup: self.checksum(Path(backup['path']), backup['bytes']))
        changed.start(); self.addCleanup(changed.stop)

    def retire(self):
        return self.backup.retire_completed('e' * 40, self.boot_check, self.device_receipt)

    def test_intent_precedes_exact_removal_and_retry_keeps_completion(self):
        def remove(argv, *args, **kwargs):
            self.assertEqual(argv, ['/sbin/lvremove', '--yes', self.backup.path])
            intent = records.read(self.bundle.directory / 'used-backup-retirement-intent.json')
            self.assertEqual(intent['backup']['uuid'], 'backup-uuid')
            self.assertEqual(intent['completion_sha256'], self.completion['record_sha256'])
            self.rows = []
        self.command.side_effect = remove
        result = self.retire()
        self.assertEqual(result['status'], 'used-backup-retired')
        self.assertEqual(result['bytes_reclaimed'], fixtures.cold.disks.BYTES)
        self.assertEqual(self.retire(), result)
        self.assertEqual(self.command.call_count, 1)

    def test_lost_removal_reply_reconciles_absence_without_a_second_remove(self):
        def remove(*args, **kwargs):
            self.rows = []
            raise TransactionError('lost remove reply')
        self.command.side_effect = remove
        with self.assertRaisesRegex(TransactionError, 'lost remove reply'):
            self.retire()
        self.assertEqual(self.retire()['status'], 'used-backup-retired')
        self.assertEqual(self.command.call_count, 1)

    def test_retirement_fences_late_allocation_and_copy(self):
        self.command.side_effect = lambda *args, **kwargs: setattr(self, 'rows', [])
        self.retire()
        for action in (self.backup.allocate, self.backup.copy):
            with self.subTest(action=action), self.assertRaisesRegex(TransactionError, 'writes are closed'):
                action()
        self.assertEqual(self.command.call_count, 1)

    def test_active_worker_or_recovery_fence_refuses_before_health(self):
        with cycle.Cycle(self.bundle).exclusive():
            with self.assertRaisesRegex(TransactionError, 'another cold supervisor'):
                self.retire()
        self.storage.cold_test.return_value = {'phase': 'restoring'}
        with self.assertRaisesRegex(TransactionError, 'active recovery fence'):
            self.retire()
        self.health.clear_fence.assert_not_called()
        self.command.assert_not_called()

    def test_failed_health_uncertain_identity_or_incomplete_copy_keeps_backup(self):
        self.health.clear_fence.side_effect = TransactionError('health refused')
        with self.assertRaisesRegex(TransactionError, 'health refused'):
            self.retire()
        self.health.clear_fence.side_effect = None
        self.test.cleanup_source.return_value = {**self.test_source, 'status': 'identity-uncertain'}
        with self.assertRaisesRegex(TransactionError, 'reconciled test identity'):
            self.retire()
        self.test.cleanup_source.return_value = self.test_source
        self.save(stage='allocated', source_sha256=None)
        with self.assertRaisesRegex(TransactionError, 'completed disk copy'):
            self.retire()
        self.command.assert_not_called()

    def test_changed_source_or_supervisor_completion_refuses(self):
        self.storage.accepted.return_value = {'current_sha256': 'f' * 64}
        with self.assertRaisesRegex(TransactionError, 'completed exact original recovery'):
            self.retire()
        self.storage.accepted.return_value = {'current_sha256': 'a' * 64}
        self.returned = generations.seal({**{k: v for k, v in self.returned.items() if k != 'record_sha256'},
                                          'finished_at': 30})
        with self.assertRaisesRegex(TransactionError, 'completed exact original recovery'):
            self.retire()
        self.command.assert_not_called()

    def test_missing_backup_without_intent_and_changed_bytes_refuse(self):
        self.rows = []
        with self.assertRaisesRegex(TransactionError, 'missing before its retirement intent'):
            self.retire()
        self.rows = [self.row]
        self.checksum.return_value = 'f' * 64
        with self.assertRaisesRegex(TransactionError, 'bytes changed'):
            self.retire()
        self.assertFalse((self.bundle.directory / 'used-backup-retirement-intent.json').exists())
        self.command.assert_not_called()

    def test_attached_backup_or_referenced_disk_refuses(self):
        self.host.wait_detached.side_effect = TransactionError('backup attached')
        with self.assertRaisesRegex(TransactionError, 'backup attached'):
            self.retire()
        self.command.assert_not_called()

    def test_attached_inspector_slot_or_unknown_file_keeps_backup(self):
        with patch.object(filesystem, 'loops', return_value=['/dev/loop9']):
            with self.assertRaisesRegex(TransactionError, 'attached inspector files'):
                self.retire()
        (self.bundle.directory / 'filesystem' / 'unknown.slot').touch()
        with self.assertRaisesRegex(TransactionError, 'unknown or attached inspector files'):
            self.retire()
        self.command.assert_not_called()

    def test_live_inspector_or_boot_reference_keeps_backup(self):
        inspector = filesystem.Inspector(self.bundle)
        for info, boot in (({'name': inspector.name}, {}),
                           ({'name': 'other', 'uuid': inspector.identity}, {}),
                           ({'name': 'other'}, {'kernel': str(inspector.work / 'bootstrap-kernel')})):
            with self.subTest(info=info, boot=boot):
                self.host.inventory.return_value = [{'config': {'c_info': info, 'b_info': boot}}]
                with self.assertRaisesRegex(TransactionError, 'live inspector or boot reference'):
                    self.retire()
        self.command.assert_not_called()

    def test_aliased_inspector_file_keeps_backup(self):
        path = self.bundle.directory / 'filesystem' / 'job.slot'
        path.write_bytes(b'job')
        path.chmod(0o600)
        os.link(path, self.bundle.directory / 'alias')
        with self.assertRaisesRegex(TransactionError, 'unsafe type, size, or ownership'):
            self.retire()
        self.command.assert_not_called()

    def test_device_cleanup_receipt_for_another_source_refuses_before_retirement(self):
        self.device_receipt = generations.seal({**{k: v for k, v in self.device_receipt.items()
                                                  if k != 'record_sha256'}, 'source_sha256': 'f' * 64})
        with self.assertRaisesRegex(TransactionError, 'exact test-device cleanup'):
            self.retire()
        self.command.assert_not_called()
        self.assertFalse((self.bundle.directory / 'used-backup-retirement-intent.json').exists())

    def test_enrolled_test_requires_exact_completed_revocation_receipt(self):
        source = generations.seal({**{k: v for k, v in self.test_source.items() if k != 'record_sha256'},
                                   'status': 'revocation-required', 'machine_id': 'test-machine'})
        self.test.cleanup_source.return_value = source
        device = {**{k: v for k, v in self.device_receipt.items() if k != 'record_sha256'},
                  'source_sha256': source['record_sha256'], 'machine_id': 'test-machine', 'status': 'deleted'}
        self.device_receipt = generations.seal(device)
        with self.assertRaisesRegex(TransactionError, 'exact test-device cleanup'):
            self.retire()
        self.device_receipt = generations.seal({**device, 'provider_id': 'provider123'})
        self.command.side_effect = lambda *args, **kwargs: setattr(self, 'rows', [])
        self.assertEqual(self.retire()['device_cleanup_sha256'], self.device_receipt['record_sha256'])

    def test_renamed_or_reappeared_backup_uuid_refuses(self):
        self.rows = [{**self.row, 'lv_path': '/dev/vg0/renamed', 'lv_tags': 'unowned'}]
        with self.assertRaisesRegex(TransactionError, 'renamed'):
            self.retire()
        self.rows = [self.row]
        self.command.side_effect = lambda *args, **kwargs: setattr(self, 'rows', [])
        self.retire()
        self.rows = [{**self.row, 'lv_path': '/dev/vg0/renamed', 'lv_tags': 'unowned'}]
        with self.assertRaisesRegex(TransactionError, 'renamed'):
            self.retire()
        self.assertEqual(self.command.call_count, 1)

    def test_retired_test_lv_reappearing_by_tag_or_uuid_refuses(self):
        archive = self.bundle.directory / 'test-state'
        archive.mkdir(mode=0o700)
        records.write(archive / 'installation.json', {'disk': {'uuid': 'test-uuid'}})
        for identity, tags in (('test-uuid', 'unowned'), ('other-uuid', 'routergen_' + 'b' * 24)):
            self.rows = [self.row, {**self.row, 'lv_path': '/dev/vg0/renamed-test',
                                   'lv_uuid': identity, 'lv_tags': tags}]
            with self.subTest(uuid=identity), self.assertRaisesRegex(TransactionError, 'test LV again'):
                self.retire()
        self.command.assert_not_called()

    def test_changed_intent_or_completion_refuses_retry(self):
        self.command.side_effect = lambda *args, **kwargs: setattr(self, 'rows', [])
        self.retire()
        p = self.bundle.directory / 'used-backup-retirement-complete.json'
        result = records.read(p)
        records.write(p, generations.seal({**{k: v for k, v in result.items() if k != 'record_sha256'},
                                           'bytes_reclaimed': 1}))
        with self.assertRaisesRegex(TransactionError, 'completion changed'):
            self.retire()
        self.assertEqual(self.command.call_count, 1)

    def test_native_dispatch_passes_saved_source_current_engine_and_bounded_receipt(self):
        records.write(self.bundle.directory / 'manifest.json', generations.seal({
            'kind': 'klokast.router-cold-metadata.v1', 'box': 'k001',
            'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine}))
        storage = Mock(box='k001', base=self.bundle.directory)
        target = storage.base / 'cold-backups' / self.bundle.operation
        target.mkdir(mode=0o700, parents=True)
        records.write(target / 'manifest.json', records.read(self.bundle.directory / 'manifest.json'))
        backup = Mock()
        backup.retire_completed.return_value = {'status': 'used-backup-retired'}
        output = io.StringIO()
        with patch.object(executor.records, 'Records', return_value=storage), \
                patch.object(executor.native, 'Native'), \
                patch.object(executor.cold_backup, 'Bundle', return_value=self.bundle) as selected, \
                patch.object(executor.cold_disk, 'DiskBackup', return_value=backup), \
                patch.object(executor, 'boot_assignment', return_value='accepted-assignment-verified') as boot, \
                patch.object(executor.sys, 'stdin', io.StringIO(json.dumps(self.device_receipt))), \
                contextlib.redirect_stdout(output):
            executor.main(['cold-retire-used-backup', '--box', 'k001', '--operation-id', self.bundle.operation], 'e' * 40)
            args = backup.retire_completed.call_args.args
            self.assertEqual(args[0], 'e' * 40)
            self.assertEqual(args[2], self.device_receipt)
            self.assertEqual(args[1](), 'accepted-assignment-verified')
            boot.assert_called_once_with(storage, require_running=True)
        selected.assert_called_once_with(storage, self.bundle.operation, self.bundle.engine)
        self.assertEqual(json.loads(output.getvalue())['action'], 'cold-retire-used-backup')


class BackupHashTests(unittest.TestCase):
    def test_hash_uses_bounded_native_command_and_exact_device_output(self):
        backup = {'path': '/dev/vg0/routercold_' + 'a' * 24 + '_backup', 'bytes': 2147483648}
        with patch.object(fixtures.cold.disks.native, 'command', return_value='d' * 64 + '  ' + backup['path'] + '\n') as run:
            self.assertEqual(fixtures.cold.retirement_checksum(backup), 'd' * 64)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/sha256sum', backup['path']])
            self.assertEqual(run.call_args.kwargs['maximum_seconds'], 90)

    def test_wrong_device_or_malformed_digest_refuses(self):
        backup = {'path': '/dev/vg0/routercold_' + 'a' * 24 + '_backup', 'bytes': 2147483648}
        for output in ('d' * 64 + '  /dev/vg0/lv_router', 'not-a-hash  ' + backup['path'],
                       'd' * 64 + '  ' + backup['path'] + ' extra'):
            with patch.object(fixtures.cold.disks.native, 'command', return_value=output), \
                    self.subTest(output=output), self.assertRaisesRegex(TransactionError, 'invalid digest'):
                fixtures.cold.retirement_checksum(backup)


if __name__ == '__main__':
    unittest.main()
