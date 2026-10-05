"""Scheduled callers must bind protected per-box readiness to current policy."""
from contextlib import ExitStack, nullcontext
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def cli():
    loader = SourceFileLoader('router_rollout_controller_test', str(ROOT / 'ansible/bin/platform-router-update'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RolloutControllerTests(unittest.TestCase):
    def setUp(self):
        self.module = cli()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.state = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(mock.patch.object(self.module, 'STATE', self.state))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'require_controller'))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'approved_engine', return_value='a' * 40))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'installation_lock', side_effect=nullcontext))
        self.status = {'kind': 'klokast.router-rollout-status.v1', 'box': 'k001', 'engine_commit': 'a' * 40,
            'ready': False, 'policy_sha256': None, 'readiness_sha256': None, 'reason': 'proof-missing'}
        self.qualification = {'kind': 'klokast.router-rollout-readiness.v1', 'box': 'k001',
            'engine_commit': 'a' * 40, 'policy_sha256': 'b' * 64, 'record_sha256': 'c' * 64,
            'status': 'native-forward-and-rollback-qualified', 'replacement_authorized': False}
        self.policy = {'enabled': True, 'targets': {'k001': ['router']}, 'exclusions': []}
        self.schedule = {'kind': 'klokast.vm-update-schedule.v1', 'policy': self.policy,
            'enabled': True, 'replacement_ready': False}
        self.policy_context = (self.schedule, {'signed': True}, self.policy, 'b' * 64)
        self.reader = self.stack.enter_context(mock.patch.object(self.module, 'check_policy_at',
            return_value=self.policy_context))
        self.outer_change = lambda value: value
        self.command = self.stack.enter_context(mock.patch.object(self.module.transport, 'command',
            side_effect=self.dispatch))

    def dispatch(self, argv, **kwargs):
        if 'status' in argv:
            return ''
        if 'rev-parse' in argv:
            return 'a' * 40
        self.assertIn(self.module.REPO / 'ansible/playbooks/74-router-rollout-readiness.yml', argv)
        arguments = self.module.transport.load(Path(argv[-1][1:]))
        value = self.qualification if arguments['router_rollout_action'] == 'qualify-rollout' else self.status
        self.module.transport.write(Path(arguments['router_rollout_result_dir']) /
            ('rollout-' + arguments['router_rollout_token'] + '.json'), self.outer_change({
                'kind': 'klokast.router-command-result.v1', 'box': 'k001', 'engine_commit': 'a' * 40,
                'action': arguments['router_rollout_action'], 'result': value}))
        return ''

    def test_readiness_absent_cannot_authorize_a_scheduled_grant(self):
        value = self.module.rollout_readiness('k001')
        self.assertFalse(value['ready'])
        with self.assertRaisesRegex(self.module.UpdateError, 'native forward'):
            self.module.require_rollout_qualified('k001', 'a' * 40, 'b' * 64, self.state, 'd' * 12)

    def test_same_box_policy_and_exact_native_proof_allow_the_restriction_check(self):
        self.status.update(ready=True, policy_sha256='b' * 64, readiness_sha256='c' * 64)
        value = self.module.require_rollout_qualified('k001', 'a' * 40, 'b' * 64, self.state, 'd' * 12)
        self.assertTrue(value['ready'])
        with self.assertRaisesRegex(self.module.UpdateError, 'native forward'):
            self.module.require_rollout_qualified('k001', 'a' * 40, 'f' * 64, self.state, 'e' * 12)

    def test_foreign_engine_target_or_shared_vm_status_is_not_router_proof(self):
        for key, value in (('box', 'k002'), ('engine_commit', 'f' * 40), ('action', 'assignment-status')):
            with self.subTest(key=key):
                self.outer_change = lambda report: {**report, key: value}
                with self.assertRaisesRegex(self.module.UpdateError, 'another native'):
                    self.module.rollout_readiness('k001')
        self.outer_change = lambda value: value
        self.status['kind'] = 'klokast.vm-replacement-readiness.v1'
        with self.assertRaisesRegex(self.module.UpdateError, 'incomplete'):
            self.module.rollout_readiness('k001')

    def test_truthy_or_incomplete_ready_status_refuses(self):
        for fields in ({'ready': 1}, {'ready': True}, {'extra': True}):
            with self.subTest(fields=fields):
                original = dict(self.status)
                self.status.update(fields)
                with self.assertRaises(self.module.UpdateError):
                    self.module.rollout_readiness('k001')
                self.status = original

    def test_qualification_takes_only_two_exact_native_operations_and_current_policy(self):
        value = self.module.rollout_readiness('k001', forward='d' * 24, rollback='e' * 24)
        self.assertEqual(value['record_sha256'], 'c' * 64)
        self.assertFalse(value['replacement_authorized'])
        self.assertEqual(self.reader.call_count, 2)
        for forward, rollback in (('d' * 24, 'd' * 24), ('../other', 'e' * 24), (None, 'e' * 24)):
            with self.subTest(forward=forward), self.assertRaises(self.module.UpdateError):
                self.module.rollout_readiness('k001', forward=forward, rollback=rollback)

    def test_paused_or_excluded_policy_refuses_native_qualification(self):
        self.schedule['enabled'] = False
        with self.assertRaisesRegex(self.module.UpdateError, 'active signed target'):
            self.module.rollout_readiness('k001', forward='d' * 24, rollback='e' * 24)
        self.schedule['enabled'] = True
        self.policy['exclusions'] = [{'box': 'k001', 'role': 'router', 'reason': 'test hold'}]
        with self.assertRaisesRegex(self.module.UpdateError, 'active signed target'):
            self.module.rollout_readiness('k001', forward='d' * 24, rollback='e' * 24)
        self.assertFalse(any(call.args[0][0] == 'ansible-playbook' for call in self.command.call_args_list))

    def test_changed_policy_after_qualification_cannot_return_scheduling_permission(self):
        self.reader.side_effect = [self.policy_context,
            ({**self.schedule, 'enabled': False}, None, self.policy, 'b' * 64)]
        with self.assertRaisesRegex(self.module.UpdateError, 'source changed'):
            self.module.rollout_readiness('k001', forward='d' * 24, rollback='e' * 24)


if __name__ == '__main__':
    unittest.main()
