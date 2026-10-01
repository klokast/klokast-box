"""Cleanup transport binds root resource selections to the exact completion."""
from pathlib import Path
import unittest
from unittest import mock

import test_router_completion_controller as completion_tests
from test_router_generations import generation


class CleanupControllerTests(unittest.TestCase):
    def setUp(self):
        completion_tests.CompletionControllerTests.setUp(self)
        self.directory = self.state / ('1' * 24)
        self.directory.mkdir(mode=0o700)
        self.generations = []
        for origin in ('template', 'legacy'):
            value = {key: item for key, item in generation(origin).items() if key != 'record_sha256'}
            value['box'] = 'k001'
            self.generations.append(self.module.router_generations.seal(value))
        self.request['candidate_sha256'], self.request['old_sha256'] = (
            value['record_sha256'] for value in self.generations)
        assignment = self.module.router_records.accepted_candidate(self.request, '0' * 64)
        self.completion.update(request=self.request, assignment=assignment, current_assignment=assignment)
        self.completion = self.module.router_generations.seal(self.completion)
        self.reader = self.stack.enter_context(mock.patch.object(self.module, 'read_replacement_completion',
            return_value={'completion': self.completion, 'evidence_directory': str(self.directory)}))
        self.plan = {'kind': 'klokast.router-cleanup-plan.v1', 'box': 'k001',
            'operation_id': self.operation, 'engine_commit': 'a' * 40, 'outcome': 'accepted',
            'completion_sha256': self.completion['record_sha256'],
            'assignment_sha256': assignment['record_sha256'],
            'original_assignment_sha256': self.request['accepted_sha256'],
            'keep': [{'generation': value, 'device': None} for value in self.generations], 'retire': [],
            'status': 'planned-no-retirement', 'retirement_authorized': False}

    def dispatch(self, argv, **kwargs):
        self.assertIn(self.module.REPO / 'ansible/playbooks/74-router-cleanup-plan.yml', argv)
        arguments = self.module.transport.load(Path(argv[-1][1:]))
        self.assertEqual(arguments['router_cleanup_operation'], self.operation)
        value = {'kind': 'klokast.router-command-result.v1', 'box': 'k001',
            'action': 'cleanup-plan', 'engine_commit': 'a' * 40,
            'result': self.module.router_generations.seal(self.plan)}
        self.module.transport.write(Path(arguments['router_cleanup_result_dir']) / 'cleanup.json',
            self.native_change(value))
        return ''

    def read(self):
        # Each query has a separate private evidence directory in production.
        (self.directory / 'cleanup-plan.log').unlink(missing_ok=True)
        return self.module.read_cleanup_plan('k001', self.operation)

    def test_checked_plan_retains_both_generations_without_authorizing_retirement(self):
        result = self.read()
        self.assertEqual(result['plan']['keep'], self.plan['keep'])
        self.assertFalse(result['retirement_authorized'])
        self.reader.assert_called_once_with('k001', self.operation)

    def test_foreign_native_source_or_completion_refuses(self):
        self.native_change = lambda value: {**value, 'engine_commit': '0' * 40}
        with self.assertRaisesRegex(self.module.UpdateError, 'another native source'):
            self.read()
        self.native_change = lambda value: value
        self.plan['completion_sha256'] = '0' * 64
        with self.assertRaisesRegex(self.module.UpdateError, 'exact protected completion'):
            self.read()

    def test_retained_generation_cannot_be_a_retirement_target(self):
        self.plan['retire'] = [self.plan['keep'][1]]
        with self.assertRaisesRegex(self.module.UpdateError, 'retained generation'):
            self.read()

    def test_unsafe_resource_shape_or_changed_activation_refuses(self):
        self.plan['keep'][0] = {'generation': self.generations[0], 'device': None, 'path': '/dev/vg0/ops'}
        with self.assertRaises(self.module.UpdateError):
            self.read()
        self.plan['keep'][0].pop('path')
        self.module.transport.approved_engine.return_value = '0' * 40
        with self.assertRaisesRegex(self.module.UpdateError, 'engine changed'):
            self.read()


if __name__ == '__main__':
    unittest.main()
