"""Interrupted cold-test transitions keep one original LV and a durable fence."""
import time
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_disk as cold_disk
import router_cold_identity as cold_identity
import router_cold_recovery as cold_recovery
import router_cold_window as cold
import router_records as records
from router_transaction import TransactionError
import test_router_cold_backup as fixtures


class WindowTests(unittest.TestCase):
    setUp = fixtures.ColdBundleTests.setUp
    capture = fixtures.ColdBundleTests.capture

    def stage_proofs(self):
        self.host.running = True
        self.host.inventory = Mock(return_value=[
            {'domid': 0, 'config': {'c_info': {'name': 'Domain-0', 'uuid': '0'*8 + '-0000-0000-0000-' + '0'*12}}},
            {'domid': 1, 'config': {'c_info': {'name': 'router', 'uuid': self.generation['xen']['uuid']}}},
            {'domid': 2, 'config': {'c_info': {'name': 'ops', 'uuid': '11111111-1111-1111-1111-111111111111'}}}])
        metadata, generation = self.bundle.verify()
        status = {'BackendState': 'Running', 'Self': {'ID': 'test-device', 'HostName': 'boxa-router', 'Online': True}}
        peer = {'BackendState': 'Running', 'Peer': {'test-device': status['Self']}}
        proof = cold_identity.create('boxa', self.bundle.operation, self.bundle.engine,
                                     metadata['record_sha256'], generation['record_sha256'],
                                     status, peer, int(time.time()))
        cold_identity.Identity(self.bundle).stage(proof)
        cold_recovery.Baseline(self.bundle).capture()

    def prepare(self):
        self.capture()
        self.window = cold.Window(self.bundle)
        WindowTests.stage_proofs(self)
        self.host.initial_guest = Mock(return_value=None)
        self.original = {'lv_path': '/dev/vg0/lv_router', 'lv_uuid': self.generation['disk']['uuid'],
                         'lv_size': '2147483648', 'lv_attr': '-wi-a-----', 'origin': '', 'lv_tags': ''}
        self.backup_row = {**self.original, 'lv_path': self.window.backup.path,
                           'lv_uuid': 'backup-uuid', 'lv_tags': self.window.backup.tag}
        self.rows = [self.original, self.backup_row]
        self.hashes = {self.original['lv_path']: 'd'*64, self.backup_row['lv_path']: 'd'*64}
        change = patch.object(cold_disk.disks, 'inventory', side_effect=lambda: self.rows)
        change.start(); self.addCleanup(change.stop)
        change = patch.object(cold, 'checksum', side_effect=lambda path, size: self.hashes[str(path)])
        change.start(); self.addCleanup(change.stop)
        change = patch.object(cold.native, 'command', side_effect=self.command)
        self.commands = change.start(); self.addCleanup(change.stop)
        self.disk_record = self.window.backup.save({'kind': 'klokast.router-cold-disk.v1', 'box': 'boxa',
            'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
            'metadata_sha256': self.bundle.verify()[0]['record_sha256'], 'source': self.generation['disk'],
            'backup': {'path': self.backup_row['lv_path'], 'uuid': 'backup-uuid', 'bytes': 2147483648},
            'stage': 'copied', 'source_sha256': 'd'*64})
        records.write(self.bundle.directory / 'filesystem.json', {
            'kind': 'klokast.router-cold-filesystem.v1', 'operation_id': self.bundle.operation,
            'metadata_sha256': self.bundle.verify()[0]['record_sha256'],
            'disk_sha256': self.disk_record['record_sha256'], 'backup_uuid': 'backup-uuid',
            'readonly': True, 'root_verified': True})
        self.initial = 'b'*24
        self.expiry = int(time.time()) + 3600
        self.window.arm(self.initial, self.expiry)
        self.host.running = False

    def test_arm_requires_identity_and_dependent_baseline(self):
        self.prepare()
        self.window.marker.unlink()
        self.host.running = True
        for name in ('original-identity.json', 'dependent-baseline.json'):
            path = self.bundle.directory / name
            saved = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaises((TransactionError, FileNotFoundError)):
                self.window.arm(self.initial, self.expiry)
            self.assertFalse(self.window.marker.exists())
            path.write_bytes(saved)

    def test_arm_requires_fresh_identity_and_same_live_guests(self):
        self.prepare()
        self.window.marker.unlink()
        self.host.running = True
        with patch.object(cold.time, 'time', return_value=self.expiry - 1):
            with self.assertRaisesRegex(TransactionError, 'fresh original Tailnet identity'):
                self.window.arm(self.initial, self.expiry)
        self.host.inventory.return_value.pop()
        with self.assertRaisesRegex(TransactionError, 'unchanged live router and dependent guests'):
            self.window.arm(self.initial, self.expiry)
        self.assertFalse(self.window.marker.exists())

    def test_arm_retry_does_not_need_router_to_still_run(self):
        self.prepare()
        with patch.object(cold.time, 'time', return_value=self.expiry - 1):
            self.assertEqual(self.window.arm(self.initial, self.expiry)['phase'], 'armed')

    def command(self, argv, *args, **kwargs):
        if argv[0] == '/sbin/lvrename':
            self.assertIn(self.storage.cold_test()['phase'], ('holding', 'restoring'))
            old, new = '/dev/vg0/' + argv[2], '/dev/vg0/' + argv[3]
            row = next(row for row in self.rows if row['lv_path'] == old)
            row['lv_path'] = new
            self.hashes[new] = self.hashes.pop(old)
        elif argv[0] == '/bin/dd':
            self.assertEqual(self.storage.cold_test()['phase'], 'restoring')
            self.assertEqual(argv[1], 'if=' + self.window.backup.path)
            self.assertEqual(argv[2], 'of=' + self.window.hold_path)
            self.hashes[self.window.hold_path] = self.hashes[self.window.backup.path]
        else:
            self.assertEqual(argv[0], '/usr/sbin/lbu')
        return ''

    def test_hold_and_open_leave_original_records_and_lock_in_place(self):
        self.prepare()
        self.assertEqual(self.window.hold()['phase'], 'held')
        self.assertEqual(self.window.hold()['phase'], 'held')
        self.assertEqual(self.original['lv_path'], self.window.hold_path)
        with self.assertRaisesRegex(TransactionError, 'outside the supervised'):
            self.storage.initial_window(self.initial)
        self.assertEqual(self.window.open_target()['phase'], 'open')
        self.storage.initial_window(self.initial)
        for operation in ('c'*24, self.bundle.operation):
            with self.assertRaisesRegex(TransactionError, 'outside the supervised'):
                self.storage.initial_window(operation)
        self.assertFalse((self.storage.base / 'accepted.json').exists())
        self.assertFalse((self.root / 'etc/xen/router.cfg').exists())
        self.assertFalse((self.root / 'etc/xen/auto/router.cfg').is_symlink())
        self.assertEqual(self.storage.generation(self.generation['record_sha256']), self.generation)
        self.assertEqual((self.storage.base / 'transaction.lock').stat().st_ino, self.lock_inode)
        self.assertTrue((self.bundle.directory / 'accepted.json').is_file())

    def test_interrupted_lvrename_reconciles_only_recorded_uuid_and_names(self):
        self.prepare()
        original_phase = self.window.phase
        def interrupt(value, phase):
            if phase == 'held':
                raise OSError('interrupted after lvrename')
            return original_phase(value, phase)
        with patch.object(self.window, 'phase', side_effect=interrupt), self.assertRaises(OSError):
            self.window.hold()
        self.assertEqual(self.storage.cold_test()['phase'], 'holding')
        self.assertEqual(self.original['lv_path'], self.window.hold_path)
        self.assertEqual(self.window.hold()['phase'], 'held')
        renames = [call for call in self.commands.call_args_list if call.args[0][0] == '/sbin/lvrename']
        self.assertEqual(len(renames), 1)

    def test_opening_retry_after_lbu_failure_keeps_the_boot_fence(self):
        self.prepare()
        self.window.hold()
        with patch.object(self.window, 'commit_xen', side_effect=TransactionError('lbu failed')):
            with self.assertRaisesRegex(TransactionError, 'lbu failed'):
                self.window.open_target()
        self.assertEqual(self.storage.cold_test()['phase'], 'opening')
        self.assertFalse((self.storage.base / 'accepted.json').exists())
        self.assertEqual(self.window.open_target()['phase'], 'open')

    def test_hold_requires_exact_readonly_filesystem_proof(self):
        self.prepare()
        path = self.bundle.directory / 'filesystem.json'
        value = records.read(path)
        for field, changed in (('readonly', False), ('root_verified', False), ('backup_uuid', 'foreign')):
            records.write(path, {**value, field: changed})
            with self.subTest(field=field), self.assertRaisesRegex(TransactionError, 'filesystem proof'):
                self.window.hold()
        self.assertEqual(self.original['lv_path'], '/dev/vg0/lv_router')

    def test_expiry_prevents_new_work_but_allows_original_disk_restoration(self):
        self.prepare()
        self.window.hold()
        with patch.object(cold.time, 'time', return_value=self.expiry):
            with self.assertRaisesRegex(TransactionError, 'outside its held'):
                self.window.open_target()
            self.window.restore_disk()
        self.assertEqual(self.original['lv_path'], '/dev/vg0/lv_router')
        self.assertEqual(self.storage.cold_test()['phase'], 'restoring')

    def test_changed_live_selector_refuses_all_removal(self):
        self.prepare()
        self.window.hold()
        config = self.root / 'etc/xen/router.cfg'
        config.write_text('# foreign router config\n')
        with self.assertRaisesRegex(TransactionError, 'changed live selector'):
            self.window.open_target()
        self.assertTrue((self.storage.base / 'accepted.json').is_file())
        self.assertTrue((self.root / 'etc/xen/auto/router.cfg').is_symlink())
        self.assertEqual(self.storage.cold_test()['phase'], 'held')

    def test_missing_unrecorded_hold_or_occupied_original_name_refuses_mutation(self):
        self.prepare()
        self.rows.append({**self.original, 'lv_path': self.window.hold_path, 'lv_uuid': 'foreign'})
        with self.assertRaisesRegex(TransactionError, 'ambiguous'):
            self.window.hold()
        self.rows.pop()
        self.original['lv_uuid'] = 'foreign'
        with self.assertRaisesRegex(TransactionError, 'identity'):
            self.window.hold()
        self.assertEqual(self.storage.cold_test()['phase'], 'armed')

    def test_held_damage_restores_from_backup_before_returning_original_name(self):
        self.prepare()
        self.window.hold()
        self.window.open_target()
        self.hashes[self.window.hold_path] = 'e'*64
        self.window.restore_disk()
        self.assertEqual(self.original['lv_path'], '/dev/vg0/lv_router')
        self.assertEqual(self.hashes['/dev/vg0/lv_router'], 'd'*64)
        self.bundle.restore()
        self.assertEqual(self.storage.accepted(), self.assignment)
        self.assertEqual(self.storage.cold_test()['phase'], 'restoring')
        self.assertEqual((self.storage.base / 'transaction.lock').stat().st_ino, self.lock_inode)

    def test_corrupt_backup_never_overwrites_the_held_original(self):
        self.prepare()
        self.window.hold()
        self.window.open_target()
        self.hashes[self.window.backup.path] = 'e'*64
        with self.assertRaisesRegex(TransactionError, 'backup checksum changed'):
            self.window.restore_disk()
        self.assertEqual(self.original['lv_path'], self.window.hold_path)
        self.assertFalse(any(call.args[0][0] == '/bin/dd' for call in self.commands.call_args_list))

    def test_test_installation_must_be_archived_before_original_disk_restoration(self):
        self.prepare()
        self.window.hold()
        self.window.open_target()
        with patch.object(self.storage, 'installation', return_value={'stage': 'prepared'}):
            with self.assertRaisesRegex(TransactionError, 'pending update or installation'):
                self.window.restore_disk()
        self.assertEqual(self.storage.cold_test()['phase'], 'open')
        self.assertEqual(self.original['lv_path'], self.window.hold_path)

    def test_lbu_refuses_unrelated_drift(self):
        self.prepare()
        self.commands.side_effect = None
        self.commands.return_value = 'U home/neo/.ash_history\n'
        self.commands.reset_mock()
        with self.assertRaisesRegex(TransactionError, 'outside its two Xen boot paths'):
            self.window.commit_xen()
        self.commands.assert_called_once()


if __name__ == '__main__':
    unittest.main()
