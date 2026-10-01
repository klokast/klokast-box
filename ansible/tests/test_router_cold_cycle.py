"""A cold window returns the original after timeout or interrupted restart."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_cycle as cold_cycle
import router_generations as generations
import router_records as records
import test_router_cold_backup as fixtures
import test_router_cold_window as window_fixtures


class CycleTests(unittest.TestCase):
    setUp = fixtures.ColdBundleTests.setUp
    capture = fixtures.ColdBundleTests.capture

    def test_timeout_returns_original_without_controller_signal(self):
        self.capture()
        cycle = cold_cycle.Cycle(self.bundle)
        marker = {'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine}
        self.storage.cold_test = Mock(side_effect=[None, marker])
        cycle.request.verify = Mock(return_value={'record_sha256': 'a' * 64})
        cycle.open = Mock(return_value={'expires_at': 100})
        cycle.return_signaled = Mock()
        with patch.object(cold_cycle.time, 'time', return_value=100), \
             patch.object(cold_cycle.cold_return.Return, 'restore') as restore:
            result = cycle.run()
        self.assertEqual(result['reason'], 'timeout')
        self.assertEqual(result['status'], 'original-running-fenced')
        cycle.return_signaled.assert_not_called()
        restore.assert_called_once_with()
        self.assertEqual(records.read(cycle.result), result)

    def test_restart_with_existing_fence_only_returns_original(self):
        self.capture()
        cycle = cold_cycle.Cycle(self.bundle)
        self.storage.cold_test = Mock(return_value={
            'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine})
        cycle.request.verify = Mock()
        cycle.open = Mock()
        with patch.object(cold_cycle.cold_return.Return, 'restore') as restore:
            result = cycle.run()
        self.assertEqual(result['reason'], 'interrupted')
        restore.assert_called_once_with()
        cycle.request.verify.assert_not_called()
        cycle.open.assert_not_called()
        with patch.object(cold_cycle.cold_return.Return, 'restore') as retry:
            self.assertEqual(cycle.run(), result)
        retry.assert_called_once_with()

    def test_failure_after_arming_calls_original_return_before_reporting_error(self):
        self.capture()
        cycle = cold_cycle.Cycle(self.bundle)
        marker = {'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine}
        self.storage.cold_test = Mock(side_effect=[None, marker])
        cycle.request.verify = Mock(return_value={'record_sha256': 'a' * 64})
        cycle.open = Mock(side_effect=RuntimeError('backup inspection failed'))
        with patch.object(cold_cycle.cold_return.Return, 'restore') as restore:
            with self.assertRaisesRegex(RuntimeError, 'backup inspection failed'):
                cycle.run()
        restore.assert_called_once_with()
        self.assertFalse(cycle.result.exists())


class ReturnSignalTests(unittest.TestCase):
    setUp = window_fixtures.WindowTests.setUp
    capture = window_fixtures.WindowTests.capture
    prepare = window_fixtures.WindowTests.prepare
    command = window_fixtures.WindowTests.command

    def test_exact_early_return_signal_is_immutable_and_retryable(self):
        self.prepare()
        self.window.hold()
        marker = self.window.open_target()
        cycle = cold_cycle.Cycle(self.bundle)
        metadata, original = self.bundle.verify()
        request = generations.seal({'kind': 'klokast.router-cold-supervised-request.v1',
            'box': self.storage.box, 'operation_id': self.bundle.operation,
            'engine_commit': self.bundle.engine,
            'metadata_sha256': metadata['record_sha256'],
            'generation_sha256': original['record_sha256'],
            'initial_operation': self.initial})
        records.write(cycle.request.path, request)
        ready = generations.seal({'kind': 'klokast.router-cold-supervisor-ready.v1',
            'box': self.storage.box, 'operation_id': self.bundle.operation,
            'engine_commit': self.bundle.engine,
            'request_sha256': request['record_sha256'],
            'marker_sha256': marker['record_sha256'],
            'initial_operation': self.initial, 'opened_at': marker['armed_at'],
            'expires_at': marker['expires_at']})
        records.write(cycle.ready, ready)
        with patch.object(cold_cycle.time, 'time', return_value=marker['armed_at'] + 1):
            signal = cycle.signal_return()
            self.assertTrue(cycle.return_signaled(ready))
            self.assertEqual(cycle.signal_return(), signal)
        self.assertEqual(signal['ready_sha256'], ready['record_sha256'])


if __name__ == '__main__':
    unittest.main()
