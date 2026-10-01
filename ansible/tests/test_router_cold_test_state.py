"""A stopped test installation is copied before any selector can be removed."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_candidate_disk as candidate_disks
import router_cold_test_state as cold
import router_generation_device as devices
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_cold_window as fixtures
from test_router_generations import generation, reseal


class ArchiveTests(unittest.TestCase):
    setUp = fixtures.WindowTests.setUp
    capture = fixtures.WindowTests.capture
    prepare_window = fixtures.WindowTests.prepare
    command = fixtures.WindowTests.command

    def open_unstarted(self):
        self.prepare_window()
        self.window.hold()
        self.window.open_target()
        self.archive = cold.TestState(self.bundle)
        change = patch.object(cold, 'domain', return_value=None)
        change.start(); self.addCleanup(change.stop)

    def test_no_installation_can_be_recorded_and_original_restored(self):
        self.open_unstarted()
        value = self.archive.record_unstarted()
        self.assertEqual(value['status'], 'never-allocated')
        self.assertEqual(value['files'], {})
        self.assertEqual(self.archive.record_unstarted(), value)
        self.window.restore_disk()
        self.bundle.restore()
        self.assertEqual(self.storage.accepted(), self.assignment)
        self.assertTrue((self.storage.base / 'cold-test.json').exists())

    def test_staged_operation_inputs_are_archived_before_empty_recovery(self):
        self.open_unstarted()
        operation = self.storage.base / 'operations' / self.initial
        operation.mkdir(mode=0o700)
        records.write(operation / 'request.json', {'kind': 'staged-test-request'})
        value = self.archive.record_unstarted()
        self.assertEqual(set(value['files']), {'operation/request.json'})
        self.assertEqual(records.read(self.bundle.directory / 'no-installation/operation/request.json'),
                         records.read(operation / 'request.json'))
        records.write(operation / 'request.json', {'kind': 'changed-request'})
        with self.assertRaisesRegex(TransactionError, 'changed staged inputs'):
            self.archive.record_unstarted()

    def test_unrecorded_test_disk_refuses_empty_recovery(self):
        self.open_unstarted()
        self.rows.append({**self.original,
            'lv_path': '/dev/vg0/routergen_' + self.initial,
            'lv_uuid': 'unrecorded', 'lv_tags': 'routergen_' + self.initial})
        with self.assertRaisesRegex(TransactionError, 'without its installation'):
            self.archive.record_unstarted()
        self.assertFalse((self.bundle.directory / 'no-installation').exists())

    def test_disk_record_without_installation_refuses_empty_recovery(self):
        self.open_unstarted()
        operation = self.storage.base / 'operations' / self.initial
        operation.mkdir(mode=0o700)
        records.write(operation / 'candidate-disk.json', {'kind': 'unexpected-disk'})
        with self.assertRaisesRegex(TransactionError, 'disk record without its installation'):
            self.archive.record_unstarted()
        self.assertFalse((self.bundle.directory / 'no-installation').exists())

    def prepare(self):
        self.prepare_window()
        self.window.hold()
        self.window.open_target()
        operation = self.storage.base / 'operations' / self.initial
        operation.mkdir(mode=0o700)
        records.write(operation / 'request.json', {'kind': 'test-request', 'operation_id': self.initial})
        self.installation = generations.seal({
            'kind': 'klokast.router-initial-installation.v1', 'box': 'boxa', 'role': 'router',
            'operation_id': self.initial, 'engine_commit': self.bundle.engine,
            'selection_sha256': 'a'*64, 'release_sha256': 'b'*64,
            'disk': {'path': '/dev/vg0/routergen_' + self.initial, 'uuid': None,
                     'bytes': 2147483648}, 'stage': 'planned',
            'preparation_sha256': None, 'enrollment_sha256': None,
            'machine_id': None, 'generation_sha256': None})
        with self.storage.lock():
            self.storage.record_installation(self.installation)
        self.archive = cold.TestState(self.bundle)
        change = patch.object(candidate_disks, 'observed', return_value=None)
        self.observed = change.start(); self.addCleanup(change.stop)
        return operation

    def test_snapshot_is_exact_private_and_retryable(self):
        operation = self.prepare()
        value = self.archive.capture()
        self.assertEqual(value['installation_sha256'], self.installation['record_sha256'])
        self.assertEqual(set(value['files']), {'installation.json', 'operation/request.json'})
        self.assertEqual(self.archive.capture(), value)
        self.assertEqual(records.read(self.archive.archive / 'manifest.json'), value)
        self.assertEqual((self.archive.archive.stat().st_mode & 0o777), 0o700)
        self.assertEqual(records.read(operation / 'request.json'),
                         records.read(self.archive.archive / 'operation/request.json'))
        self.assertEqual(self.storage.installation(), self.installation)
        self.assertFalse((self.storage.base / 'accepted.json').exists())
        self.assertEqual(self.window.located(self.generation)['path'], self.window.hold_path)

    def test_selector_removal_keeps_archive_and_original_recovery(self):
        self.prepare()
        archive = self.archive.capture()
        with self.assertRaisesRegex(TransactionError, 'completed exact selector removal'):
            self.archive.retire_disk()
        intent = self.archive.remove_selectors()
        self.assertEqual(intent['archive_sha256'], archive['record_sha256'])
        self.assertEqual(self.archive.remove_selectors(), intent)
        self.assertIsNone(self.storage.installation())
        self.assertEqual(self.archive.verify(), archive)
        self.assertEqual(self.window.located(self.generation)['path'], self.window.hold_path)
        retired = self.archive.retire_disk()
        self.assertEqual(retired['status'], 'never-allocated')
        self.assertEqual(self.archive.retire_disk(), retired)
        self.window.restore_disk()
        self.bundle.restore()
        self.assertEqual(self.storage.accepted(), self.assignment)

    def test_changed_installation_refuses_all_selector_removal(self):
        self.prepare()
        self.archive.capture()
        records.write(self.storage.base / 'installation.json', {'kind': 'foreign'})
        with self.assertRaisesRegex(TransactionError, 'changed after archive'):
            self.archive.remove_selectors()
        self.assertFalse((self.archive.archive / 'selectors-removal.json').exists())
        self.assertTrue((self.storage.base / 'installation.json').exists())

    def test_lbu_interruption_retries_only_recorded_selector_removal(self):
        self.prepare()
        self.archive.capture()
        with patch.object(self.archive.window, 'commit_xen', side_effect=TransactionError('LBU failed')):
            with self.assertRaisesRegex(TransactionError, 'LBU failed'):
                self.archive.remove_selectors()
        self.assertFalse((self.storage.base / 'installation.json').exists())
        self.assertTrue((self.archive.archive / 'selectors-removal.json').exists())
        self.archive.remove_selectors()
        self.assertIsNone(self.storage.installation())

    def test_changed_operation_record_refuses_archive_retry(self):
        operation = self.prepare()
        self.archive.capture()
        records.write(operation / 'request.json', {'kind': 'changed-request'})
        with self.assertRaisesRegex(TransactionError, 'changed source records'):
            self.archive.capture()
        self.assertEqual(records.read(self.archive.archive / 'operation/request.json')['kind'],
                         'test-request')

    def test_running_test_router_refuses_snapshot(self):
        self.prepare()
        self.host.initial_guest = Mock(return_value={'domid': 8})
        with self.assertRaisesRegex(TransactionError, 'all router guests stopped'):
            self.archive.capture()
        self.assertFalse(self.archive.archive.exists())

    def test_unrecorded_test_disk_refuses_snapshot(self):
        self.prepare()
        self.observed.return_value = {'lv_path': '/dev/vg0/routergen_' + self.initial}
        with self.assertRaisesRegex(TransactionError, 'unrecorded test LV'):
            self.archive.capture()
        self.assertFalse(self.archive.archive.exists())

    def test_accepted_test_archive_keeps_generation_and_tailnet_identity(self):
        operation = self.prepare()
        disk = {'path': '/dev/vg0/routergen_' + self.initial,
                'uuid': 'test-uuid', 'bytes': 2147483648}
        candidate = generation('template')
        candidate['generation_id'] = self.initial
        candidate['disk'] = disk
        reseal(candidate)
        complete = generations.seal({
            **{key: value for key, value in self.installation.items() if key != 'record_sha256'},
            'disk': disk, 'stage': 'verified', 'preparation_sha256': 'd'*64,
            'enrollment_sha256': 'e'*64, 'machine_id': 'test-machine',
            'generation_sha256': candidate['record_sha256']})
        records.write(self.storage.base / 'installation.json', complete)
        records.write(operation / 'candidate-disk.json', {
            'kind': 'klokast.router-candidate-disk.v1', 'operation_id': self.initial,
            'path': disk['path'], 'tag': 'routergen_' + self.initial,
            'uuid': disk['uuid'], 'stage': 'cloned', 'template_sha256': 'f'*64})
        with patch.object(candidate_disks, 'verify', return_value=disk):
            with self.storage.lock():
                accepted = self.storage.accept_initial(candidate)
            devices.remember(self.storage, candidate['record_sha256'], 'test-machine',
                             'boxa-router-' + self.initial, 'a'*64)
            value = self.archive.capture()
        self.assertEqual(value['accepted_sha256'], accepted['record_sha256'])
        self.assertEqual(value['generation_sha256'], candidate['record_sha256'])
        self.assertEqual(value['machine_id'], 'test-machine')
        self.assertEqual(set(value['files']) & {'accepted.json', 'generation.json', 'device.json'},
                         {'accepted.json', 'generation.json', 'device.json'})
        self.assertEqual(self.archive.verify(), value)
        config = self.root / 'etc/xen/router.cfg'
        config.write_text(generations.configuration(candidate))
        link = self.root / 'etc/xen/auto/router.cfg'
        link.symlink_to('../router.cfg')
        intent = self.archive.remove_selectors()
        self.assertEqual(intent['archive_sha256'], value['record_sha256'])
        self.assertFalse(config.exists())
        self.assertFalse(link.is_symlink())
        self.assertFalse((self.storage.base / 'accepted.json').exists())
        self.assertEqual(self.archive.verify(), value)
        with patch.object(candidate_disks, '_retire_locked', side_effect=lambda work, operation, **kwargs:
                records.write(work / 'candidate-disk.json', {
                    **records.read(work / 'candidate-disk.json'), 'stage': 'retired'})):
            retired = self.archive.retire_disk()
        self.assertEqual(retired['status'], 'retired')


if __name__ == '__main__':
    unittest.main()
