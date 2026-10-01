"""A scheduled cutover defers one retained candidate and reconciles native outcomes."""
from contextlib import ExitStack, nullcontext
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def cli():
    loader = SourceFileLoader('router_daily_cutover_test', str(ROOT / 'ansible/bin/platform-router-update'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class DailyCutoverTests(unittest.TestCase):
    def setUp(self):
        self.module = cli()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.state = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(mock.patch.object(self.module, 'STATE', self.state))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'require_controller'))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'installation_lock', side_effect=nullcontext))
        self.context = self.stack.enter_context(mock.patch.object(self.module, 'daily_preparation_context'))
        self.record = {'kind': 'klokast.router-daily-preparation.v1', 'engine_commit': 'a' * 40,
            'schedule_sha256': 'b' * 64, 'box': 'k001', 'check_operation': 'a' * 24,
            'source_operation': 'b' * 24, 'template_operation': 'c' * 24,
            'compatibility_operation': 'd' * 24, 'operation_id': 'e' * 24,
            'accepted_generation_sha256': 'c' * 64, 'report_sha256': 'd' * 64,
            'phase': 'cutover-staged', 'status': 'prepared', 'results': {}}
        self.pointer = self.state / 'daily-preparation.json'
        self.module.transport.write(self.pointer, self.record)
        self.old = self.module.router_generations.seal({'kind': 'klokast.router-assignment.v1',
            'box': 'k001', 'role': 'router', 'current_sha256': 'c' * 64, 'previous_sha256': None,
            'operation_id': '0' * 24, 'engine_commit': 'a' * 40, 'policy_sha256': 'e' * 64,
            'evidence_sha256': 'f' * 64})
        self.request = {'kind': 'klokast.router-transaction-request.v1', 'role': 'router', 'box': 'k001',
            'operation_id': 'e' * 24, 'engine_commit': 'a' * 40, 'policy_sha256': '1' * 64,
            'accepted_sha256': self.old['record_sha256'], 'old_sha256': 'c' * 64,
            'candidate_sha256': '2' * 64, 'cutover_seconds': 1800, 'recovery_seconds': 900}
        directory = self.state / self.record['operation_id']
        directory.mkdir(mode=0o700)
        self.module.transport.write(directory / 'transaction-request.json', self.request)
        self.policy = {'enabled': True}
        self.policy_reader = self.stack.enter_context(mock.patch.object(self.module, 'check_policy_at',
            return_value=({'activated': True}, {'signed': True}, self.policy, self.request['policy_sha256'])))
        self.window = self.stack.enter_context(mock.patch.object(self.module.router_updates, 'require_cutover_window',
            return_value=9999999999))
        self.launcher = self.stack.enter_context(mock.patch.object(self.module, 'run_replacement_cutover',
            side_effect=self.launch))
        self.completer = self.stack.enter_context(mock.patch.object(self.module, 'read_replacement_completion',
            side_effect=lambda box, operation: self.completion()))
        self.no_prepare = self.stack.enter_context(mock.patch.object(self.module, 'prepare_replacement',
            side_effect=AssertionError('cutover cannot allocate another candidate')))
        self.outcome = 'accepted'

    def saved(self):
        return self.module.transport.load(self.pointer)

    def launch(self, box, operation, **kwargs):
        self.assertEqual((box, operation), ('k001', 'e' * 24))
        self.assertEqual(kwargs, {'require_maintenance_window': True})
        self.assertEqual((self.saved()['phase'], self.saved()['status']), ('cutover-running', 'running'))
        return {'kind': 'klokast.router-replacement-cutover-result.v1', 'box': box,
            'operation_id': operation, 'status': self.outcome}

    def completion(self):
        assignment = self.module.router_records.accepted_candidate(self.request, '3' * 64)
        if self.outcome == 'rolled-back':
            assignment = self.old
        value = self.module.router_generations.seal({'kind': 'klokast.router-completed-operation.v2',
            'box': 'k001', 'operation_id': 'e' * 24, 'engine_commit': 'a' * 40, 'request': self.request,
            'assignment': assignment, 'current_assignment': assignment, 'outcome': self.outcome})
        return {'kind': 'klokast.router-completion-read-result.v1', 'box': 'k001',
            'operation_id': 'e' * 24, 'completion': value, 'replacement_authorized': False}

    def test_outside_window_defers_same_candidate_without_mutation_or_launch(self):
        self.window.side_effect = self.module.router_updates.CutoverWindowClosed('outside')
        for _ in range(2):
            self.assertEqual(self.module.daily_cutover()['status'], 'deferred')
        self.assertEqual(self.saved(), self.record)
        self.launcher.assert_not_called()
        self.completer.assert_not_called()
        self.no_prepare.assert_not_called()

    def test_window_closing_during_native_inspection_reuses_only_an_ungranted_candidate(self):
        self.launcher.side_effect = self.module.router_updates.CutoverWindowClosed('window closed')
        self.assertEqual(self.module.daily_cutover()['status'], 'deferred')
        self.assertEqual(self.saved(), self.record)
        self.completer.assert_not_called()
        self.module.transport.write(self.state / ('e' * 24) / 'cutover-start-grant.json', {'retained': True})
        with self.assertRaisesRegex(self.module.UpdateError, 'retained launch marker'):
            self.module.daily_cutover()
        self.assertEqual(self.saved()['status'], 'failed')

    def test_valid_window_launches_once_and_keeps_cleanup_barrier(self):
        result = self.module.daily_cutover()
        self.assertEqual(result['status'], 'accepted-needs-cleanup')
        self.assertEqual(self.saved()['phase'], 'cutover-completed')
        with self.assertRaisesRegex(self.module.UpdateError, 'cleanup'):
            self.module.daily_cutover()
        self.launcher.assert_called_once()
        self.assertEqual(self.saved()['results']['completion']['completion']['request'], self.request)

    def test_rollback_is_failure_and_blocks_another_router_until_reconciliation(self):
        self.outcome = 'rolled-back'
        with self.assertRaisesRegex(self.module.UpdateError, 'rolled back'):
            self.module.daily_cutover()
        self.assertEqual(self.saved()['status'], 'rolled-back-needs-cleanup')
        with self.assertRaises(self.module.UpdateError):
            self.module.daily_cutover()
        self.launcher.assert_called_once()
        result = self.module.reconcile_daily()
        self.assertEqual(result['status'], 'rolled-back-needs-cleanup')
        self.launcher.assert_called_once()

    def test_controller_exception_after_launch_is_retained_and_reconciled_without_restart(self):
        self.completer.side_effect = self.module.UpdateError('reply lost')
        with self.assertRaisesRegex(self.module.UpdateError, 'reply lost'):
            self.module.daily_cutover()
        self.assertEqual((self.saved()['phase'], self.saved()['status']), ('cutover-running', 'failed'))
        self.completer.side_effect = lambda *args: self.completion()
        result = self.module.reconcile_daily()
        self.assertEqual(result['status'], 'accepted-needs-cleanup')
        self.launcher.assert_called_once()

    def test_process_loss_retains_running_record_and_native_rollback_is_reconciled(self):
        self.launcher.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.module.daily_cutover()
        self.assertEqual(self.saved()['status'], 'running')
        with self.assertRaisesRegex(self.module.UpdateError, 'reconciliation'):
            self.module.daily_cutover()
        self.outcome = 'rolled-back'
        self.assertEqual(self.module.reconcile_daily()['status'], 'rolled-back-needs-cleanup')
        self.assertEqual(self.launcher.call_count, 1)

    def test_missing_native_completion_never_looks_reconciled(self):
        self.launcher.side_effect = self.module.UpdateError('connection lost')
        with self.assertRaises(self.module.UpdateError):
            self.module.daily_cutover()
        original = self.saved()
        self.completer.side_effect = self.module.UpdateError('pending native operation')
        with self.assertRaisesRegex(self.module.UpdateError, 'pending native'):
            self.module.reconcile_daily()
        self.assertEqual(self.saved(), original)
        self.launcher.assert_called_once()

    def test_changed_engine_policy_or_retained_request_blocks_launch(self):
        self.context.side_effect = self.module.UpdateError('engine changed')
        with self.assertRaises(self.module.UpdateError):
            self.module.daily_cutover()
        self.context.side_effect = None
        self.policy_reader.return_value = ({'activated': True}, {'signed': True}, self.policy, 'f' * 64)
        with self.assertRaises(self.module.UpdateError):
            self.module.daily_cutover()
        self.policy_reader.return_value = ({'activated': True}, {'signed': True}, self.policy, '1' * 64)
        self.request['old_sha256'] = '4' * 64
        self.module.transport.write(self.state / ('e' * 24) / 'transaction-request.json', self.request)
        with self.assertRaisesRegex(self.module.UpdateError, 'another retained'):
            self.module.daily_cutover()
        self.launcher.assert_not_called()

    def test_foreign_completion_or_launch_disagreement_is_not_accepted(self):
        self.completer.side_effect = lambda *args: {**self.completion(), 'box': 'k002'}
        with self.assertRaisesRegex(self.module.UpdateError, 'another completion'):
            self.module.daily_cutover()
        self.assertEqual(self.saved()['status'], 'failed')
        self.module.transport.write(self.pointer, self.record)
        self.completer.side_effect = lambda *args: self.completion()
        self.launcher.side_effect = lambda *args, **kwargs: {
            'kind': 'klokast.router-replacement-cutover-result.v1', 'box': 'k001',
            'operation_id': 'e' * 24, 'status': 'rolled-back'}
        with self.assertRaisesRegex(self.module.UpdateError, 'contradicts'):
            self.module.daily_cutover()
        self.assertEqual(self.saved()['status'], 'failed')

    def test_corrupt_pointer_unknown_phase_or_missing_pointer_never_launch(self):
        for fields in ({'operation_id': '../escape'}, {'status': 'accepted'}, {'phase': 'start-again'}):
            self.module.transport.write(self.pointer, {**self.record, **fields})
            with self.assertRaises(self.module.UpdateError):
                self.module.daily_cutover()
        self.pointer.unlink()
        self.assertEqual(self.module.daily_cutover()['status'], 'deferred')
        self.launcher.assert_not_called()

    def test_preparation_failure_cannot_be_reconciled_as_a_completed_cutover(self):
        self.module.transport.write(self.pointer, {**self.record, 'phase': 'template', 'status': 'failed'})
        with self.assertRaisesRegex(self.module.UpdateError, 'no completed cutover'):
            self.module.reconcile_daily()
        self.launcher.assert_not_called()
        self.completer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
