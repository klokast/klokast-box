"""Interrupted cold tests return to the exact original while keeping the fence."""
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_return as cold_return
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_cold_test_state as state_fixtures
import test_router_cold_window as fixtures


class ReturnTests(unittest.TestCase):
    setUp = fixtures.WindowTests.setUp
    capture = fixtures.WindowTests.capture
    prepare = fixtures.WindowTests.prepare
    command = fixtures.WindowTests.command

    @patch.object(cold_return.cold_filesystem.xen, 'domain', return_value=None)
    def test_interruption_before_stop_keeps_running_original_and_fence(self, _domain):
        self.prepare()
        self.host.running = True
        self.host.start = Mock()
        result = cold_return.Return(self.bundle).restore()
        self.assertEqual(result['status'], 'original-running-fenced')
        self.assertEqual(self.storage.accepted(), self.assignment)
        self.assertEqual(self.storage.cold_test()['phase'], 'restoring')
        self.host.start.assert_not_called()

    @patch.object(cold_return.cold_filesystem.xen, 'domain', return_value=None)
    def test_controller_loss_before_first_install_restores_original_and_keeps_fence(self, _domain):
        self.prepare()
        self.window.hold()
        self.window.open_target()
        self.host.start = Mock(side_effect=lambda *args, **kwargs: setattr(self.host, 'running', True))
        recovery = cold_return.Return(self.bundle)
        recovery.fence_new_work()
        with self.assertRaisesRegex(TransactionError, 'return began'):
            self.storage.initial_window(self.initial)
        with patch.object(cold_return.cold_test_state, 'domain', return_value=None), \
             patch.object(cold_return.cold_test_state.candidate_disks, 'inventory',
                          return_value=self.rows):
            result = recovery.restore()
        self.assertEqual(result['status'], 'original-running-fenced')
        self.assertEqual(self.storage.accepted(), self.assignment)
        self.assertEqual(self.original['lv_path'], '/dev/vg0/lv_router')
        self.assertEqual(self.storage.cold_test()['phase'], 'restoring')
        self.assertEqual(records.read(self.bundle.directory / 'no-installation/manifest.json')['status'],
                         'never-allocated')
        self.assertEqual(self.host.start.call_count, 1)
        self.assertEqual((self.storage.base / 'transaction.lock').stat().st_ino, self.lock_inode)


class PlannedInstallReturnTests(unittest.TestCase):
    setUp = state_fixtures.ArchiveTests.setUp
    capture = state_fixtures.ArchiveTests.capture
    prepare_window = state_fixtures.ArchiveTests.prepare_window
    prepare = state_fixtures.ArchiveTests.prepare
    command = state_fixtures.ArchiveTests.command

    @patch.object(cold_return.cold_filesystem.xen, 'domain', return_value=None)
    def test_controller_loss_after_installation_record_archives_and_retires_test(self, _domain):
        self.prepare()
        self.host.start = Mock(side_effect=lambda *args, **kwargs: setattr(self.host, 'running', True))
        result = cold_return.Return(self.bundle).restore()
        self.assertEqual(result['status'], 'original-running-fenced')
        self.assertEqual(self.storage.accepted(), self.assignment)
        self.assertIsNone(self.storage.installation())
        archive = records.read(self.bundle.directory / 'test-state/manifest.json')
        self.assertEqual(archive['initial_operation'], self.initial)
        self.assertEqual(records.read(self.bundle.directory / 'test-state/disk-retirement.json')['status'],
                         'never-allocated')
        self.assertEqual(self.storage.cold_test()['phase'], 'restoring')

    def test_only_recorded_first_router_can_be_stopped(self):
        work = self.prepare()
        disk = {'path': '/dev/vg0/routergen_' + self.initial,
                'uuid': 'test-lv-uuid', 'bytes': 2147483648}
        prepared = generations.seal({
            **{key: value for key, value in self.installation.items() if key != 'record_sha256'},
            'disk': disk, 'stage': 'prepared', 'preparation_sha256': 'd' * 64})
        records.write(self.storage.base / 'installation.json', prepared)
        xen = {'uuid': '22222222-2222-2222-2222-222222222222',
               'memory': 512, 'vcpus': 1,
               'vif': ['bridge=br-wan,mac=02:00:00:00:00:01']}
        boot = {'kernel': {'path': '/test/kernel'},
                'initramfs': {'path': '/test/initramfs'}}
        request = {'kind': 'klokast.router-initial-boot-request.v1',
                   'box': 'boxa', 'operation_id': self.initial,
                   'engine_commit': self.bundle.engine, 'xen': xen}
        content = generations.initial_configuration(xen, disk, boot)
        records.write(work / 'initial-boot-request.json', request)
        records.write(work / 'initial-boot-intent.json', {
            'kind': 'klokast.router-initial-boot-intent.v1',
            'box': 'boxa', 'operation_id': self.initial,
            'request_sha256': generations.digest(request), 'disk': disk,
            'boot': boot, 'config_sha256': hashlib.sha256(content.encode()).hexdigest()})
        (work / 'initial-router.cfg').write_text(content)
        self.host.stop_initial = Mock()
        recovery = cold_return.Return(self.bundle)
        self.assertEqual(recovery.stop_test(self.storage.cold_test()), prepared)
        self.host.stop_initial.assert_called_once()
        self.assertEqual(self.host.stop_initial.call_args.args[0], disk)
        self.assertEqual(self.host.stop_initial.call_args.args[1]['uuid'], xen['uuid'])
        records.write(work / 'initial-boot-intent.json', {
            **records.read(work / 'initial-boot-intent.json'),
            'request_sha256': 'f' * 64})
        with self.assertRaisesRegex(Exception, 'differs from its exact installation'):
            recovery.stop_test(self.storage.cold_test())
        self.host.stop_initial.assert_called_once()


if __name__ == '__main__':
    unittest.main()
