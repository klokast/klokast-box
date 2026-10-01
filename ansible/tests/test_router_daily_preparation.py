"""Required-update preparation records selectors before allocation and stops on loss."""
from contextlib import ExitStack
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def cli():
    loader = SourceFileLoader('router_daily_preparation_test', str(ROOT / 'ansible/bin/platform-router-update'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class DailyPreparationTests(unittest.TestCase):
    def setUp(self):
        self.module = cli()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.state = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(mock.patch.object(self.module, 'STATE', self.state))
        self.stack.enter_context(mock.patch.object(self.module, 'CACHE', self.state / 'cache'))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'require_controller'))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'approved_engine', return_value='a' * 40))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'command',
            side_effect=lambda argv: '' if 'status' in argv else 'a' * 40))
        self.schedule = {'kind': 'klokast.vm-update-schedule.v1', 'activated': True,
            'replacement_ready': False, 'policy': {'enabled': True,
            'targets': {'k001': ['router'], 'k002': ['router']}, 'exclusions': []}}
        self.reader = self.stack.enter_context(mock.patch.object(self.module, 'schedule_source',
            side_effect=lambda **kwargs: self.schedule))
        self.check_operation = 'b' * 24
        self.check = {'box': 'k001', 'status': 'update-required', 'report_sha256': 'c' * 64,
                      'evidence_directory': str(self.state / self.check_operation)}
        self.daily = self.stack.enter_context(mock.patch.object(self.module, 'daily_check', return_value={
            'engine_commit': 'a' * 40, 'schedule_sha256': self.module.router_updates.digest(self.schedule),
            'checks': [self.check, {**self.check, 'box': 'k002'}]}))
        self.preflight = self.stack.enter_context(mock.patch.object(self.module, 'preflight_replacement',
            return_value={'kind': 'klokast.router-replacement-source-preflight.v1', 'box': 'k001',
            'check_operation': self.check_operation, 'source_operation': 'd' * 24,
            'accepted_generation_sha256': 'e' * 64, 'report_sha256': 'c' * 64,
            'status': 'validated-input-source', 'replacement_authorized': False}))
        self.calls = []
        self.stages = {}
        for name in ('build_template', 'test_compatibility', 'prepare_replacement',
                     'stage_replacement_generation', 'stage_replacement_cutover'):
            self.stages[name] = self.stack.enter_context(mock.patch.object(self.module, name,
                side_effect=lambda *args, _name=name, **kwargs: self.stage(_name, args, kwargs)))
        self.cutover = self.stack.enter_context(mock.patch.object(self.module, 'run_replacement_cutover',
            side_effect=AssertionError('preparation cannot cut over')))

    def record(self):
        return self.module.transport.load(self.module.STATE / 'daily-preparation.json')

    def stage(self, name, args, kwargs):
        self.calls.append(name)
        record = self.record()
        self.assertEqual(record['status'], 'running')
        self.assertEqual(args[0], 'k001')
        if name == 'build_template':
            self.assertEqual(kwargs['operation'], record['template_operation'])
            return {'box': 'k001', 'operation': kwargs['operation'], 'engine_approved': True,
                    'release_sha256': 'f' * 64, 'status': 'generic-template-qualified'}
        if name == 'test_compatibility':
            self.assertEqual(kwargs['operation'], record['compatibility_operation'])
            self.assertEqual(args[2], record['template_operation'])
            return {'box': 'k001', 'operation_id': kwargs['operation'], 'engine_commit': 'a' * 40,
                    'success': True, 'replacement_authorized': False}
        if name == 'prepare_replacement':
            self.assertEqual(kwargs['operation'], record['operation_id'])
            return {'box': 'k001', 'operation': kwargs['operation'], 'status': 'replacement-prepared',
                    'router_started': False}
        if name == 'stage_replacement_generation':
            return {'box': 'k001', 'operation': args[1], 'status': 'proposed-generation-staged',
                    'router_started': False, 'cutover_authorized': False}
        return {'box': 'k001', 'operation_id': args[1], 'compatibility_operation': args[2],
                'status': 'staged-no-cutover'}

    def test_serial_pipeline_reserves_all_selectors_and_never_cuts_over(self):
        result = self.module.daily_prepare()
        self.assertEqual(result['status'], 'prepared')
        self.assertEqual(result['phase'], 'cutover-staged')
        self.assertEqual(self.calls, list(self.stages))
        self.assertEqual(result['box'], 'k001')
        self.cutover.assert_not_called()
        for key in ('template_operation', 'compatibility_operation', 'operation_id'):
            self.assertRegex(result[key], '^[0-9a-f]{24}$')

    def test_prepared_retry_checks_original_source_without_another_candidate(self):
        first = self.module.daily_prepare()
        second = self.module.daily_prepare()
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual({key: value for key, value in first.items() if key != 'reused'},
                         {key: value for key, value in second.items() if key != 'reused'})
        self.assertEqual(self.daily.call_count, 1)
        for stage in self.stages.values():
            self.assertEqual(stage.call_count, 1)
        self.assertEqual(self.preflight.call_count, 2)

    def test_unchanged_and_deferred_checks_allocate_nothing(self):
        for status in ('unchanged', 'deferred'):
            self.daily.return_value['checks'] = [{**self.check, 'status': status}]
            self.assertEqual(self.module.daily_prepare()['preparation_status'], 'no-eligible-update')
        self.assertFalse((self.state / 'daily-preparation.json').exists())
        self.preflight.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_failure_in_each_phase_stops_and_blocks_a_new_attempt(self):
        for name in self.stages:
            with self.subTest(stage=name), tempfile.TemporaryDirectory() as directory:
                self.module.STATE = Path(directory)
                self.check['evidence_directory'] = str(self.module.STATE / self.check_operation)
                stage = self.stages[name]
                original = stage.side_effect
                stage.side_effect = self.module.UpdateError('intentional failure')
                with self.assertRaisesRegex(self.module.UpdateError, 'intentional'):
                    self.module.daily_prepare()
                self.assertEqual(self.record()['status'], 'failed')
                calls = {key: value.call_count for key, value in self.stages.items()}
                with self.assertRaisesRegex(self.module.UpdateError, 'reconciliation'):
                    self.module.daily_prepare()
                self.assertEqual(calls, {key: value.call_count for key, value in self.stages.items()})
                stage.side_effect = original

    def test_process_loss_leaves_running_pointer_and_refuses_restart(self):
        self.stages['build_template'].side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.module.daily_prepare()
        self.assertEqual(self.record()['status'], 'running')
        with self.assertRaisesRegex(self.module.UpdateError, 'reconciliation'):
            self.module.daily_prepare()
        self.assertEqual(self.stages['build_template'].call_count, 1)

    def test_changed_policy_or_engine_prevents_allocations(self):
        for altered in ('policy', 'engine'):
            with self.subTest(altered=altered):
                if altered == 'policy':
                    self.schedule['activated'] = False
                else:
                    self.schedule['activated'] = True
                    self.module.transport.approved_engine.return_value = 'f' * 40
                with self.assertRaises(self.module.UpdateError):
                    self.module.daily_prepare()
        self.assertEqual(self.calls, [])

    def test_authority_change_mid_pipeline_stops_before_next_stage(self):
        original = self.stages['build_template'].side_effect
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.schedule['activated'] = False
            return result
        self.stages['build_template'].side_effect = changed
        with self.assertRaisesRegex(self.module.UpdateError, 'authority changed'):
            self.module.daily_prepare()
        self.stages['test_compatibility'].assert_not_called()
        self.assertEqual(self.record()['status'], 'failed')

    def test_foreign_preflight_or_unapproved_template_blocks_preparation(self):
        self.preflight.return_value['report_sha256'] = 'f' * 64
        with self.assertRaisesRegex(self.module.UpdateError, 'another checked source'):
            self.module.daily_prepare()
        self.assertEqual(self.calls, [])
        self.preflight.return_value['report_sha256'] = 'c' * 64
        self.stages['build_template'].side_effect = lambda *args, **kwargs: {
            'box': 'k001', 'operation': kwargs['operation'], 'status': 'generic-template-qualified',
            'engine_approved': False, 'release_sha256': 'f' * 64}
        with self.assertRaisesRegex(self.module.UpdateError, 'approved release'):
            self.module.daily_prepare()
        self.stages['test_compatibility'].assert_not_called()

    def test_pending_candidate_with_changed_source_requires_reconciliation(self):
        self.module.daily_prepare()
        self.preflight.return_value['accepted_generation_sha256'] = 'f' * 64
        with self.assertRaisesRegex(self.module.UpdateError, 'reconcile'):
            self.module.daily_prepare()
        self.assertEqual(self.stages['prepare_replacement'].call_count, 1)

    def test_invalid_reserved_build_and_diagnostic_ids_refuse_before_controller_work(self):
        module = cli()
        with mock.patch.object(module.transport, 'require_controller') as controller:
            for operation in ('../escape', 'f' * 23, 1):
                with self.subTest(operation=operation):
                    with self.assertRaises(module.UpdateError):
                        module.build_template('k001', module.CACHE / ('a' * 24), operation=operation)
                    with self.assertRaises(module.UpdateError):
                        module.test_compatibility('k001', module.CACHE / ('a' * 24),
                            'b' * 24, operation=operation)
            controller.assert_not_called()

    def test_driver_lock_refuses_concurrent_run_and_symlink(self):
        with self.module.transport.router_driver_lock(self.state):
            with self.assertRaisesRegex(self.module.UpdateError, 'driver lock'):
                self.module.daily_prepare()
        path = self.state / 'daily.lock'
        path.unlink()
        path.symlink_to(self.state / 'unrelated')
        with self.assertRaises(OSError):
            self.module.daily_prepare()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
