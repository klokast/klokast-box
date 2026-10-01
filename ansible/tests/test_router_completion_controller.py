"""Completion transport binds one native result to the active engine and operation."""
from contextlib import ExitStack, nullcontext
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def cli():
    loader = SourceFileLoader('router_completion_controller_test', str(ROOT / 'ansible/bin/platform-router-update'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CompletionControllerTests(unittest.TestCase):
    def setUp(self):
        self.module = cli()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.state = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(mock.patch.object(self.module, 'STATE', self.state))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'require_controller'))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'approved_engine', return_value='a' * 40))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'installation_lock', side_effect=nullcontext))
        self.operation = 'b' * 24
        self.request = {'kind': 'klokast.router-transaction-request.v1', 'role': 'router',
            'box': 'k001', 'operation_id': self.operation, 'engine_commit': 'a' * 40,
            'policy_sha256': 'c' * 64, 'accepted_sha256': 'd' * 64,
            'old_sha256': 'e' * 64, 'candidate_sha256': 'f' * 64,
            'cutover_seconds': 1800, 'recovery_seconds': 900}
        assignment = self.module.router_records.accepted_candidate(self.request, '0' * 64)
        self.completion = {'kind': 'klokast.router-completed-operation.v2', 'box': 'k001',
            'operation_id': self.operation, 'engine_commit': 'a' * 40, 'request': self.request,
            'completion_sha256': '1' * 64, 'assignment': assignment,
            'current_assignment': assignment, 'outcome': 'accepted', 'candidate_started': True,
            'old_started': False, 'reason': 'cutover', 'state_change_observed': None,
            'copy_receipts': {'forward': '2' * 64},
            'devices': {'old': None, 'candidate': None}, 'acceptance_sha256': '3' * 64}
        self.native_change = lambda value: value
        self.command = self.stack.enter_context(mock.patch.object(self.module.transport, 'command',
            side_effect=self.dispatch))

    def dispatch(self, argv, **kwargs):
        if 'status' in argv:
            return ''
        if 'rev-parse' in argv:
            return 'a' * 40
        self.assertIn(self.module.REPO / 'ansible/playbooks/74-router-completion-read.yml', argv)
        arguments = self.module.transport.load(Path(argv[-1][1:]))
        self.assertEqual(arguments['router_completion_box'], 'k001')
        self.assertEqual(arguments['router_completion_operation'], self.operation)
        value = {'kind': 'klokast.router-command-result.v1', 'box': 'k001',
            'action': 'completion-status', 'engine_commit': 'a' * 40,
            'result': self.module.router_generations.seal(self.completion)}
        self.module.transport.write(Path(arguments['router_completion_result_dir']) / 'completion.json',
            self.native_change(value))
        return ''

    def test_retains_exact_native_proof_without_granting_authority(self):
        result = self.module.read_replacement_completion('k001', self.operation)
        self.assertEqual(result['completion']['request'], self.request)
        self.assertFalse(result['replacement_authorized'])
        self.assertTrue((Path(result['evidence_directory']) / 'completion.json').is_file())
        self.assertEqual(result['completion']['assignment'], self.completion['assignment'])

    def test_different_outer_box_engine_or_action_refuses(self):
        for key, value in (('box', 'k002'), ('engine_commit', '0' * 40), ('action', 'assignment-status')):
            with self.subTest(key=key):
                self.native_change = lambda result: {**result, key: value}
                with self.assertRaisesRegex(self.module.UpdateError, 'another native source'):
                    self.module.read_replacement_completion('k001', self.operation)

    def test_changed_hash_foreign_operation_or_wrong_outcome_refuses(self):
        self.native_change = lambda value: {**value, 'result': {**value['result'], 'outcome': 'rolled-back'}}
        with self.assertRaises(RuntimeError):
            self.module.read_replacement_completion('k001', self.operation)
        self.native_change = lambda value: value
        for key, value in (('operation_id', '0' * 24), ('outcome', 'worker-finished')):
            with self.subTest(key=key):
                old = self.completion[key]
                self.completion[key] = value
                with self.assertRaisesRegex(self.module.UpdateError, 'different source or outcome'):
                    self.module.read_replacement_completion('k001', self.operation)
                self.completion[key] = old

    def test_invalid_selector_and_unapproved_engine_refuse_before_ansible(self):
        with self.assertRaises(self.module.UpdateError):
            self.module.read_replacement_completion('k001', '../other')
        self.command.assert_not_called()
        self.module.transport.approved_engine.return_value = '0' * 40
        with self.assertRaisesRegex(self.module.UpdateError, 'activated engine'):
            self.module.read_replacement_completion('k001', self.operation)
        self.assertFalse(any(call.args[0][0] == 'ansible-playbook' for call in self.command.call_args_list))


if __name__ == '__main__':
    unittest.main()
