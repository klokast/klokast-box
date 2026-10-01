"""No retry may adopt a foreign backup or overwrite a changed source."""
import copy
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_disk as cold
import router_generations as generations
import router_records as records
from router_transaction import TransactionError


class ColdDiskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        for name, value in (('ROOT_UID', os.geteuid()), ('parents', lambda path: None)):
            change = patch.object(records, name, value)
            change.start(); self.addCleanup(change.stop)
        self.storage = Mock(box='boxa')
        self.storage.lock.return_value = nullcontext()
        self.storage.accepted.return_value = {'current_sha256': 'a'*64}
        self.host = Mock()
        self.host.guest.return_value = None
        self.host.disk.side_effect = lambda item, **kwargs: item['uuid']
        self.bundle = Mock(storage=self.storage, host=self.host, directory=work,
                           operation='a'*24, engine='c'*40)
        self.metadata = {'record_sha256': 'b'*64}
        self.generation = {'record_sha256': 'a'*64,
                           'disk': {'path': '/dev/vg0/lv_router', 'uuid': 'original-uuid', 'bytes': 2147483648}}
        self.bundle.verify.return_value = (self.metadata, self.generation)
        self.backup = cold.DiskBackup(self.bundle)
        self.row = {'lv_path': self.backup.path, 'lv_uuid': 'backup-uuid', 'lv_size': '2147483648',
                    'lv_attr': '-wi-a-----', 'origin': '', 'lv_tags': self.backup.tag}
        self.value = {'kind': 'klokast.router-cold-disk.v1', 'box': 'boxa',
                      'operation_id': 'a'*24, 'engine_commit': 'c'*40, 'metadata_sha256': 'b'*64,
                      'source': self.generation['disk'],
                      'backup': {'path': self.backup.path, 'uuid': 'backup-uuid', 'bytes': 2147483648},
                      'stage': 'allocated', 'source_sha256': None}
        self.rows = [self.row]
        change = patch.object(cold.disks, 'inventory', side_effect=lambda: self.rows)
        change.start(); self.addCleanup(change.stop)
        change = patch.object(cold.disks.native, 'command')
        self.command = change.start(); self.addCleanup(change.stop)
        change = patch.object(cold, 'checksum', return_value='d'*64)
        self.checksum = change.start(); self.addCleanup(change.stop)

    def save(self, **changes):
        value = generations.seal({**self.value, **changes})
        records.write(self.backup.record, value)
        return value

    def test_allocate_persists_intent_then_exact_uuid_and_retry_does_not_reallocate(self):
        self.rows = []
        def allocate(argv, *args, **kwargs):
            planned = records.read(self.backup.record)
            self.assertEqual(planned['stage'], 'planned')
            self.assertIsNone(planned['backup']['uuid'])
            self.rows = [self.row]
        self.command.side_effect = allocate
        value = self.backup.allocate()
        self.assertEqual(value['backup']['uuid'], 'backup-uuid')
        self.assertEqual(value['stage'], 'allocated')
        self.assertEqual(self.backup.allocate(), value)
        self.assertEqual(self.command.call_count, 1)
        self.assertEqual(self.command.call_args.args[0][0], '/sbin/lvcreate')
        self.host.detached.assert_not_called()  # Reservation is safe while A runs.

    def test_unrecorded_or_partial_allocation_never_adopts_observed_uuid(self):
        with self.assertRaisesRegex(TransactionError, 'unrecorded LV'):
            self.backup.allocate()
        self.save(stage='planned', backup={**self.value['backup'], 'uuid': None})
        with self.assertRaisesRegex(TransactionError, 'no recorded UUID'):
            self.backup.allocate()
        self.command.assert_not_called()

    def test_failed_allocation_with_proved_absence_can_retry(self):
        self.rows = []
        self.command.side_effect = TransactionError('lvcreate failed')
        with self.assertRaises(TransactionError):
            self.backup.allocate()
        self.assertEqual(records.read(self.backup.record)['stage'], 'planned')
        self.command.side_effect = lambda *args, **kwargs: setattr(self, 'rows', [self.row])
        self.assertEqual(self.backup.allocate()['stage'], 'allocated')

    def test_copy_checks_detachment_and_durable_source_hash_before_dd(self):
        self.save()
        def run_copy(argv, *args, **kwargs):
            self.assertEqual(argv, ['/bin/dd', 'if=/dev/vg0/lv_router', 'of=' + self.backup.path,
                                    'bs=4M', 'count=512', 'conv=notrunc,fsync'])
            intent = records.read(self.backup.record)
            self.assertEqual(intent['stage'], 'copying')
            self.assertEqual(intent['source_sha256'], 'd'*64)
            self.assertEqual(self.host.detached.call_count, 2)
        self.command.side_effect = run_copy
        value = self.backup.copy()
        self.assertEqual(value['stage'], 'copied')
        self.assertEqual(self.backup.copy(), value)
        self.assertEqual(self.command.call_count, 1)
        self.assertEqual(self.checksum.call_count, 6)

    def test_attached_disk_or_alias_refuses_before_reading_or_writing(self):
        self.save()
        self.host.detached.side_effect = TransactionError('still attached')
        with self.assertRaisesRegex(TransactionError, 'still attached'):
            self.backup.copy()
        self.host.detached.side_effect = None
        self.host.disk.side_effect = None
        self.host.disk.return_value = 123
        with self.assertRaisesRegex(TransactionError, 'aliases'):
            self.backup.copy()
        self.checksum.assert_not_called()
        self.command.assert_not_called()

    def test_running_router_refuses_before_reading_or_writing(self):
        self.save()
        self.host.guest.return_value = ('accepted', {})
        with self.assertRaisesRegex(TransactionError, 'router stopped'):
            self.backup.copy()
        self.checksum.assert_not_called()
        self.command.assert_not_called()

    def test_changed_source_or_completed_corrupt_backup_is_never_recopied(self):
        for stage, sums in (('copying', ['e'*64]), ('copied', ['d'*64, 'e'*64])):
            self.save(stage=stage, source_sha256='d'*64)
            self.checksum.side_effect = sums
            with self.subTest(stage=stage), self.assertRaises(TransactionError):
                self.backup.copy()
        self.command.assert_not_called()

    def test_interrupted_dd_retries_only_unchanged_source(self):
        self.save()
        self.command.side_effect = TransactionError('interrupted copy')
        with self.assertRaisesRegex(TransactionError, 'interrupted copy'):
            self.backup.copy()
        self.assertEqual(records.read(self.backup.record)['stage'], 'copying')
        self.command.side_effect = None
        self.assertEqual(self.backup.copy()['stage'], 'copied')
        self.assertEqual(self.command.call_count, 2)

    def test_checksum_mismatch_keeps_both_disks_and_unfinished_copy_record(self):
        self.save()
        self.checksum.side_effect = ['d'*64, 'e'*64]
        with self.assertRaisesRegex(TransactionError, 'does not match'):
            self.backup.copy()
        self.assertEqual(records.read(self.backup.record)['stage'], 'copying')
        self.assertEqual(self.command.call_count, 1)
        self.assertEqual(self.command.call_args.args[0][0], '/bin/dd')

    def test_changed_lv_or_metadata_identity_never_writes(self):
        original = copy.deepcopy(self.row)
        for key, value in (('lv_uuid', 'foreign'), ('lv_tags', 'unowned'), ('origin', 'snapshot'),
                           ('lv_size', '1'), ('lv_attr', 'swi-a-----')):
            self.save()
            self.rows = [{**original, key: value}]
            with self.subTest(field=key), self.assertRaises(TransactionError):
                self.backup.copy()
        self.rows = [original]
        self.save(metadata_sha256='0'*64)
        with self.assertRaisesRegex(TransactionError, 'different source'):
            self.backup.copy()
        self.command.assert_not_called()
        self.checksum.assert_not_called()

    def test_changed_accepted_source_refuses_even_a_completed_backup(self):
        self.save(stage='copied', source_sha256='d'*64)
        self.storage.accepted.return_value = {'current_sha256': 'f'*64}
        with self.assertRaisesRegex(TransactionError, 'no longer the accepted'):
            self.backup.copy()
        self.command.assert_not_called()

    def prepared_abort_inputs(self):
        self.save()
        self.storage.cold_test.return_value = None
        self.host.guest.return_value = ('router', {})
        records.write(self.bundle.directory / 'accepted.json', self.storage.accepted.return_value)
        change = patch.object(cold.disks, 'refuse_referenced_disk')
        change.start(); self.addCleanup(change.stop)

    def test_prepared_abort_records_intent_before_exact_lv_removal_and_retries(self):
        self.prepared_abort_inputs()
        def remove(argv, *args, **kwargs):
            self.assertEqual(argv, ['/sbin/lvremove', '--yes', self.backup.path])
            self.assertTrue((self.bundle.directory / 'prepared-abort-intent.json').is_file())
            self.rows = []
        self.command.side_effect = remove
        result = self.backup.abort_prepared('d' * 40)
        self.assertEqual(result['status'], 'retired')
        self.assertEqual(result['source_engine_commit'], 'c' * 40)
        self.assertEqual(self.backup.abort_prepared('d' * 40), result)
        self.assertEqual(self.command.call_count, 1)

    def test_prepared_abort_reconciles_lost_lvremove_reply(self):
        self.prepared_abort_inputs()
        def remove(argv, *args, **kwargs):
            self.rows = []
            raise TransactionError('lost lvremove reply')
        self.command.side_effect = remove
        with self.assertRaisesRegex(TransactionError, 'lost lvremove reply'):
            self.backup.abort_prepared('d' * 40)
        self.assertTrue((self.bundle.directory / 'prepared-abort-intent.json').is_file())
        self.assertFalse((self.bundle.directory / 'prepared-abort-completion.json').exists())
        self.assertEqual(self.backup.abort_prepared('d' * 40)['status'], 'retired')
        self.assertEqual(self.command.call_count, 1)

    def test_prepared_abort_refuses_request_changed_identity_or_used_backup(self):
        self.prepared_abort_inputs()
        request = self.bundle.directory / 'supervised-request.json'
        request.touch()
        with self.assertRaisesRegex(TransactionError, 'before any outage'):
            self.backup.abort_prepared('d' * 40)
        request.unlink()
        self.rows = [{**self.row, 'lv_uuid': 'foreign'}]
        with self.assertRaisesRegex(TransactionError, 'identity'):
            self.backup.abort_prepared('d' * 40)
        self.rows = [self.row]
        self.host.wait_detached.side_effect = TransactionError('backup attached')
        with self.assertRaisesRegex(TransactionError, 'backup attached'):
            self.backup.abort_prepared('d' * 40)
        self.host.wait_detached.side_effect = None
        self.save(stage='copied', source_sha256='f' * 64)
        with self.assertRaisesRegex(TransactionError, 'copied or used'):
            self.backup.abort_prepared('d' * 40)
        self.assertFalse((self.bundle.directory / 'prepared-abort-intent.json').exists())
        self.command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
