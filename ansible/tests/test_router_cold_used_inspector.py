"""Owned used and interrupted inspector files require exact retirement."""
import hashlib
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_filesystem as cold
import router_cold_recovery as recovery
import router_executor as executor
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_cold_used_backup as fixtures


class UsedInspectorTests(unittest.TestCase):
    save = fixtures.UsedBackupTests.save
    retire_backup = fixtures.UsedBackupTests.retire

    def setUp(self):
        fixtures.UsedBackupTests.setUp(self)
        self.inspector = cold.Inspector(self.bundle)
        self.baseline = unittest.mock.Mock()
        self.baseline.restored.return_value = (self.metadata, self.generation, {'record_sha256': '8'*64})
        changed = patch.object(recovery, 'Baseline', return_value=self.baseline)
        changed.start(); self.addCleanup(changed.stop)
        self.disk = records.read(self.backup.record)
        self.payloads = {'kernel': b'kernel', 'initramfs': b'initramfs'}
        self.capsule = generations.seal({'kind': 'klokast.router-cold-filesystem-bootstrap.v1',
            'box': self.storage.box, 'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
            'inputs_sha256': '1'*64, 'guest_sha256': '2'*64,
            'boot': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                     for name, data in self.payloads.items()}})
        records.write(self.bundle.directory / 'filesystem-bootstrap.json', self.capsule)
        records.write(self.bundle.directory / 'supervised-request.json', {'initial_operation': 'b'*24})
        self.staging = {'phase': 'ready', 'files': {}}
        for name, data in self.payloads.items():
            path = self.inspector.work / ('bootstrap-' + name)
            path.write_bytes(data); path.chmod(0o600)
            info = path.stat()
            self.staging['files'][name] = {'device': info.st_dev, 'inode': info.st_ino, 'inflight': None, 'next_part': 1}
        changed = patch.object(cold.BootstrapStaging, 'load', side_effect=lambda: (
            {'accepted': self.storage.accepted.return_value},
            {'capsule': self.capsule, 'parts': {'kernel': [{}], 'initramfs': [{}]}}, self.staging))
        changed.start(); self.addCleanup(changed.stop)
        self.writes = cold.InspectorWrites(self.bundle)
        self.command.side_effect = lambda *args, **kwargs: setattr(self, 'rows', [])
        self.retirement = self.retire_backup()

    def reserve(self):
        self.writes.reserve(self.metadata, self.disk, self.capsule,
                            self.inspector.job(self.capsule, self.metadata, self.disk))

    def collect(self):
        return self.inspector.retire_used('f'*40, self.retirement)

    def test_complete_inspector_collection_retries_without_losing_small_records(self):
        self.reserve()
        self.inspector.slot(self.inspector.work / 'result.slot')
        self.inspector.slot(self.inspector.work / 'job.slot', content=b'job\n')
        self.writes.write('guest.cfg', b'name = "guest"\n')
        # Result data are opaque, bounded guest output. Their inode stays owned.
        with (self.inspector.work / 'result.slot').open('r+b') as stream: stream.write(b'result\0')
        result = self.collect()
        self.assertEqual(result['status'], 'used-inspector-retired')
        self.assertEqual(result['bytes_reclaimed'], 15 + 2*cold.MIB + len(b'name = "guest"\n'))
        self.assertFalse(list(self.inspector.work.iterdir()))
        self.assertTrue(self.backup.record.exists())
        self.assertTrue(self.writes.path.exists())
        self.assertEqual(self.collect(), result)
        self.assertEqual(self.command.call_count, 1)  # Only the earlier exact LV retirement.

    def test_inspector_never_started_collects_only_owned_boot_files(self):
        result = self.collect()
        self.assertIsNone(result['ownership_sha256'])
        self.assertEqual(result['bytes_reclaimed'], 15)

    def test_partial_slot_with_recorded_inode_is_collected_after_recovery(self):
        self.reserve()
        with patch.object(cold.os, 'posix_fallocate', side_effect=OSError('allocation failed')):
            with self.assertRaisesRegex(OSError, 'allocation failed'):
                self.inspector.slot(self.inspector.work / 'result.slot')
        self.assertEqual(self.collect()['bytes_reclaimed'], 15)

    def test_empty_creation_orphan_is_owned_but_unknown_payload_is_not(self):
        self.reserve()
        original = self.writes.save
        def stop(value):
            if value['files'].get('guest.cfg', {}).get('phase') == 'writing': raise RuntimeError('lost ownership reply')
            original(value)
        with patch.object(self.writes, 'save', side_effect=stop):
            with self.assertRaises(RuntimeError): self.writes.write('guest.cfg', b'configuration')
        self.assertEqual(self.collect()['bytes_reclaimed'], 15)

    def test_unrecorded_legacy_or_unknown_files_are_retained(self):
        path = self.inspector.work / 'job.slot'
        path.write_bytes(b'unowned'); path.chmod(0o600)
        with self.assertRaisesRegex(TransactionError, 'unowned files'): self.collect()
        self.assertTrue((self.inspector.work / 'bootstrap-kernel').exists())
        self.assertEqual(path.read_bytes(), b'unowned')

    def test_changed_boot_bytes_or_inode_refuse_before_removal(self):
        path = self.inspector.work / 'bootstrap-kernel'
        path.write_bytes(b'change')
        with self.assertRaisesRegex(TransactionError, 'differs from its ownership'): self.collect()
        self.assertTrue((self.inspector.work / 'bootstrap-initramfs').exists())

    def test_configuration_changed_after_completed_write_refuses(self):
        self.reserve(); self.writes.write('guest.cfg', b'configuration')
        (self.inspector.work / 'guest.cfg').write_bytes(b'wrong-content')
        with self.assertRaisesRegex(TransactionError, 'differs from its ownership'): self.collect()

    def test_payload_without_durable_inode_is_not_adopted(self):
        self.reserve()
        original = self.writes.save
        def stop(value):
            if value['files'].get('guest.cfg', {}).get('phase') == 'writing': raise RuntimeError('lost ownership reply')
            original(value)
        with patch.object(self.writes, 'save', side_effect=stop):
            with self.assertRaises(RuntimeError): self.writes.write('guest.cfg', b'configuration')
        (self.inspector.work / 'guest.cfg').write_bytes(b'unknown')
        with self.assertRaisesRegex(TransactionError, 'unsafe type, size, or ownership'): self.collect()

    def test_owned_file_alias_or_replaced_inode_refuses(self):
        path = self.inspector.work / 'bootstrap-kernel'
        alias = self.bundle.directory / 'alias'
        cold.os.link(path, alias)
        with self.assertRaisesRegex(TransactionError, 'unsafe type, size, or ownership'): self.collect()
        alias.unlink()
        old = self.bundle.directory / 'old-kernel'
        path.rename(old)
        path.write_bytes(b'kernel'); path.chmod(0o600)
        with self.assertRaisesRegex(TransactionError, 'differs from its ownership'): self.collect()

    def paused_plan(self):
        original = cold.BootstrapStaging.inspect
        def stop(measured, path, maximum, **kwargs):
            if (self.bundle.directory / 'used-inspector-cleanup-progress.json').exists():
                raise RuntimeError('paused after plan')
            return original(measured, path, maximum, **kwargs)
        with patch.object(cold.BootstrapStaging, 'inspect', stop):
            with self.assertRaisesRegex(RuntimeError, 'paused after plan'): self.collect()

    def test_missing_file_without_removal_intent_is_not_a_lost_reply(self):
        self.paused_plan()
        (self.inspector.work / 'bootstrap-kernel').unlink()
        with self.assertRaisesRegex(TransactionError, 'disappeared without removal intent'): self.collect()
        self.assertTrue((self.inspector.work / 'bootstrap-initramfs').exists())

    def test_bytes_changed_after_plan_are_not_a_new_cleanup_target(self):
        self.paused_plan()
        (self.inspector.work / 'bootstrap-kernel').write_bytes(b'change')
        with self.assertRaisesRegex(TransactionError, 'changed after retirement plan'): self.collect()

    def test_changed_progress_or_plan_refuses_before_unlink(self):
        self.paused_plan()
        path = self.bundle.directory / 'used-inspector-cleanup-progress.json'
        progress = records.read(path)
        records.write(path, generations.seal({**{k: v for k, v in progress.items() if k != 'record_sha256'},
            'removed': ['bootstrap-initramfs']}))
        with self.assertRaisesRegex(TransactionError, 'progress changed'): self.collect()
        self.assertTrue((self.inspector.work / 'bootstrap-kernel').exists())

    def test_active_fence_original_loss_and_reappeared_backup_refuse(self):
        for change, message in ((lambda: setattr(self, 'rows', [self.row]), 'retired LV again'),
                (lambda: setattr(self.storage.cold_test, 'return_value', {}), 'recovered original changed'),
                (lambda: setattr(self.host.guest, 'return_value', None), 'recovered original changed')):
            with self.subTest(message=message):
                self.rows = []; self.storage.cold_test.return_value = None; self.host.guest.return_value = ('router', {})
                change()
                with self.assertRaisesRegex(TransactionError, message): self.collect()
                self.assertTrue((self.inspector.work / 'bootstrap-kernel').exists())

    def test_deleted_backing_loop_and_live_boot_reference_refuse(self):
        with patch.object(cold, 'loops', return_value=['/dev/loop9']):
            with self.assertRaisesRegex(TransactionError, 'loop attachment'): self.collect()
        self.host.inventory.return_value = [{'config': {'c_info': {'name': 'other'},
            'b_info': {'ramdisk': str(self.inspector.work / 'bootstrap-initramfs')}}}]
        with self.assertRaisesRegex(TransactionError, 'boot reference'): self.collect()

    def test_changed_dependent_guest_set_refuses_before_collection(self):
        original = self.baseline.restored.return_value
        self.baseline.restored.side_effect = [original, (self.metadata, self.generation, {'record_sha256': '7'*64})]
        with self.assertRaisesRegex(TransactionError, 'recovered original changed'): self.collect()
        self.assertTrue((self.inspector.work / 'bootstrap-kernel').exists())

    def test_supervisor_lock_prevents_collection_during_local_work(self):
        import router_cold_cycle as cycle
        with cycle.Cycle(self.bundle).exclusive():
            with self.assertRaisesRegex(TransactionError, 'another cold supervisor'): self.collect()

    def test_lost_unlink_reply_resumes_exact_recorded_removal(self):
        original = Path.unlink
        def lost(path, *args, **kwargs):
            original(path, *args, **kwargs)
            if path.name == 'bootstrap-kernel': raise RuntimeError('lost unlink reply')
        with patch.object(Path, 'unlink', lost):
            with self.assertRaisesRegex(RuntimeError, 'lost unlink reply'): self.collect()
        self.assertFalse((self.inspector.work / 'bootstrap-kernel').exists())
        self.assertEqual(self.collect()['bytes_reclaimed'], 15)

    def test_disappearance_without_removal_intent_and_reappearance_refuse(self):
        (self.inspector.work / 'bootstrap-kernel').unlink()
        with self.assertRaises(FileNotFoundError): self.collect()

    def test_retired_file_reappearance_is_not_a_new_cleanup_target(self):
        self.collect()
        path = self.inspector.work / 'bootstrap-kernel'
        path.write_bytes(b'kernel'); path.chmod(0o600)
        with self.assertRaisesRegex(TransactionError, 'reappeared'): self.collect()
        self.assertTrue(path.exists())

    def test_changed_ownership_source_or_incomplete_boot_refuses(self):
        self.reserve()
        value = self.writes.load(); self.writes.save({**value, 'disk_sha256': '9'*64})
        with self.assertRaisesRegex(TransactionError, 'another source'): self.collect()
        self.writes.save(value)
        self.staging['files']['kernel']['inflight'] = 0
        with self.assertRaisesRegex(TransactionError, 'ownership is incomplete'): self.collect()

    def test_native_artifact_dispatch_rechecks_historical_retirement_before_collection(self):
        storage = Mock(box='k001', base=self.bundle.directory)
        target = storage.base / 'cold-backups' / self.bundle.operation
        target.mkdir(mode=0o700, parents=True)
        records.write(target / 'manifest.json', generations.seal({
            'kind': 'klokast.router-cold-metadata.v1', 'box': 'k001',
            'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine}))
        backup, inspector = Mock(), Mock()
        backup.retire_completed.return_value = self.retirement
        inspector.retire_used.return_value = {'status': 'used-inspector-retired'}
        with patch.object(executor.records, 'Records', return_value=storage), \
                patch.object(executor.native, 'Native'), \
                patch.object(executor.cold_backup, 'Bundle', return_value=self.bundle), \
                patch.object(executor.cold_disk, 'DiskBackup', return_value=backup), \
                patch.object(executor.cold_filesystem, 'Inspector', return_value=inspector), \
                patch.object(executor.sys, 'stdin', io.StringIO(json.dumps(self.device_receipt))), \
                contextlib.redirect_stdout(io.StringIO()):
            executor.main(['cold-retire-used-inspector', '--box', 'k001', '--operation-id', self.bundle.operation], 'f'*40)
        self.assertEqual(backup.retire_completed.call_args.args[0], 'e'*40)
        self.assertEqual(backup.retire_completed.call_args.args[2], self.device_receipt)
        inspector.retire_used.assert_called_once_with('f'*40, self.retirement)


if __name__ == '__main__':
    unittest.main()
