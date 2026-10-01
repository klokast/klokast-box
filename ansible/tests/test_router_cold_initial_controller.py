"""The supervised proof uses normal provisioning and always requests recovery."""
from contextlib import nullcontext
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from test_router_template_cli import load_cli


class ColdInitialTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.result = Path(temporary.name)
        self.operation, self.engine = 'a' * 24, 'b' * 40
        self.pointer = self.cli.router_generations.seal({
            'kind': 'klokast.router-initial-provision-pointer.v1', 'box': 'k001',
            'engine_commit': self.engine, 'operation_id': 'c' * 24,
            'source_operation': 'd' * 24, 'template_operation': 'e' * 24,
            'selection_sha256': 'f' * 64, 'release_sha256': '1' * 64})
        self.request = {'initial_provision': self.pointer, 'record_sha256': '2' * 64}
        self.ready = {'initial_operation': self.pointer['operation_id'],
                      'request_sha256': self.request['record_sha256'],
                      'opened_at': int(time.time()) - 1, 'expires_at': int(time.time()) + 3600}
        self.open = {'finished': False, 'pointer': {'result': {'phase': 'open', 'ready': self.ready}}}
        self.done = {'finished': True, 'rc': 0, 'pointer': {'result': {'phase': 'restoring'}}}
        self.read = Mock(side_effect=[self.open, self.open, self.done])
        self.initial = {key: self.pointer[key] for key in (
            'operation_id', 'engine_commit', 'selection_sha256', 'release_sha256')}
        self.status = Mock(side_effect=[
            {'installation': {**self.initial, 'stage': 'prepared'}, 'assignment': None},
            {'installation': {**self.initial, 'stage': 'prepared'}, 'assignment': None},
            {'installation': {**self.initial, 'stage': 'verified', 'generation_sha256': '3' * 64},
             'assignment': {'current_sha256': '3' * 64}}])
        self.commands = Mock()
        self.return_signal, self.finish = Mock(), Mock()
        for target, name, value in (
                (self.cli, 'STATE', self.result), (self.cli, 'CACHE', self.result / 'cache'),
                (self.cli, 'cold_supervisor_context', Mock(return_value=(self.result, self.engine, self.request))),
                (self.cli, 'initial_template_source', Mock(return_value={
                    'selection_sha256': self.pointer['selection_sha256'],
                    'release': {'receipt_sha256': self.pointer['release_sha256']}})),
                (self.cli, 'read_cold_supervisor', self.read),
                (self.cli, 'signal_cold_supervisor_return', self.return_signal),
                (self.cli, 'finish_cold_recovery', self.finish),
                (self.cli, 'provisioning_status', self.status),
                (self.cli.transport, 'command', self.commands),
                (self.cli.transport, 'installation_lock', nullcontext),
                (self.cli.time, 'sleep', Mock())):
            context = patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)

    def run_initial(self):
        return self.cli.run_cold_initial('k001', self.operation)

    def test_normal_phase_retry_and_accept_use_the_reserved_template_then_restore(self):
        self.assertEqual(self.run_initial()['status'], 'initial-accepted-original-restored')
        self.assertEqual(self.cli.initial_provision_pointer('k001', self.engine)[1], self.pointer)
        self.assertEqual([call.args[0] for call in self.commands.call_args_list], [
            [self.cli.REPO / 'ansible/bin/provision-box', '--box', 'k001',
             '--from', phase, '--to', phase, '--yes'] for phase in ('30', '30', '31')])
        self.return_signal.assert_called_once_with('k001', self.operation)
        self.finish.assert_called_once_with('k001', self.operation)
        self.assertTrue((self.result / 'cold-initial-accept.json').is_file())

    def test_provisioning_failure_still_returns_original_and_keeps_error(self):
        self.commands.side_effect = self.cli.UpdateError('provisioning interrupted')
        with self.assertRaisesRegex(self.cli.UpdateError, 'provisioning interrupted'):
            self.run_initial()
        self.return_signal.assert_called_once_with('k001', self.operation)
        self.finish.assert_called_once_with('k001', self.operation)
        self.status.assert_not_called()

    def test_foreign_pointer_is_preserved_and_original_returned(self):
        foreign = {key: value for key, value in self.pointer.items() if key != 'record_sha256'}
        foreign['operation_id'] = 'f' * 24
        foreign = self.cli.router_generations.seal(foreign)
        self.cli.transport.write(self.result / 'initial-provision-k001.json', foreign)
        with self.assertRaisesRegex(self.cli.UpdateError, 'another provisioning pointer'):
            self.run_initial()
        self.assertEqual(self.cli.initial_provision_pointer('k001', self.engine)[1], foreign)
        self.commands.assert_not_called()
        self.finish.assert_called_once()

    def test_expired_or_closed_window_never_calls_provisioning(self):
        self.ready['expires_at'] = int(time.time()) - 1
        with self.assertRaisesRegex(self.cli.UpdateError, 'exact open'):
            self.run_initial()
        self.commands.assert_not_called()
        self.return_signal.assert_not_called()
        self.finish.assert_not_called()


if __name__ == '__main__':
    unittest.main()
