"""Daily checks select router evidence and never dispatch replacement actions."""
from contextlib import ExitStack, nullcontext
from importlib.machinery import SourceFileLoader
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def cli():
    loader = SourceFileLoader('router_daily_test', str(ROOT / 'ansible/bin/platform-router-update'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def schedule():
    return {'kind': 'klokast.vm-update-schedule.v1', 'activated': True,
            'replacement_ready': False, 'policy': {'enabled': True,
            'targets': {'k002': ['router'], 'k001': ['dmz', 'router'], 'k003': ['iot']},
            'exclusions': []}}


def check(box, status='unchanged'):
    return {'kind': 'klokast.router-check-result.v1', 'box': box, 'status': status,
            'report_sha256': 'a' * 64, 'replacement_authorized': False,
            'diagnostic_only': False}


class RouterDailyTests(unittest.TestCase):
    def setUp(self):
        self.module = cli()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.state = Path(directory)
        self.stack.enter_context(mock.patch.object(self.module, 'STATE', self.state))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'require_controller'))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'command',
            side_effect=lambda argv: '' if 'status' in argv else 'b' * 40))
        self.stack.enter_context(mock.patch.object(self.module.transport, 'approved_engine', return_value='b' * 40))
        self.schedule = schedule()
        self.reader = self.stack.enter_context(mock.patch.object(self.module, 'schedule_source',
            side_effect=lambda **kwargs: self.schedule))
        self.checker = self.stack.enter_context(mock.patch.object(self.module, 'check_current', side_effect=check))
        self.mutations = [self.stack.enter_context(mock.patch.object(self.module, name,
            side_effect=AssertionError('daily checks must not mutate routers')))
            for name in ('build_template', 'prepare_replacement', 'run_replacement_cutover')]

    def test_only_declared_nonexcluded_routers_checked_serially(self):
        self.schedule['policy']['exclusions'] = [
            {'box': 'k002', 'role': 'router', 'reason': 'retained rollback test'}]
        result = self.module.daily_check()
        self.assertEqual(result['targets'], ['k001'])
        self.checker.assert_called_once_with('k001')
        self.assertEqual(result['status'], 'unchanged')
        self.assertFalse(result['replacement_authorized'])
        self.assertTrue((Path(result['evidence_directory']) / 'daily-check.json').is_file())

    def test_unchanged_repeated_checks_never_build_or_cut_over(self):
        for _ in range(2):
            self.assertEqual(self.module.daily_check()['status'], 'unchanged')
        self.assertEqual(self.checker.call_args_list,
            [mock.call('k001'), mock.call('k002')] * 2)
        for mutation in self.mutations:
            mutation.assert_not_called()

    def test_disabled_absent_or_shared_only_policy_has_no_router_check(self):
        for policy in (None, {'enabled': False, 'targets': {'k001': ['router']}, 'exclusions': []},
                {'enabled': True, 'targets': {'k001': ['dmz', 'iot']}, 'exclusions': []}):
            with self.subTest(policy=policy):
                self.schedule['policy'] = policy
                self.assertEqual(self.module.daily_check()['status'], 'deferred')
        self.checker.assert_not_called()

    def test_failure_stops_before_next_box_and_retains_failed_report(self):
        self.checker.side_effect = lambda box: check(box, 'failed')
        with self.assertRaises(self.module.UpdateError):
            self.module.daily_check()
        self.checker.assert_called_once_with('k001')
        reports = list(self.state.glob('*/daily-check.json'))
        self.assertEqual(len(reports), 1)
        self.assertEqual(json.loads(reports[0].read_text())['status'], 'failed')

    def test_deferred_metadata_cannot_report_unchanged(self):
        self.checker.side_effect = lambda box: check(box, 'deferred')
        self.assertEqual(self.module.daily_check()['status'], 'deferred')

    def test_changed_schedule_stops_before_dispatch(self):
        changed = {**self.schedule, 'activated': False}
        self.reader.side_effect = [self.schedule, changed]
        with self.assertRaisesRegex(self.module.UpdateError, 'changed'):
            self.module.daily_check()
        self.checker.assert_not_called()

    def test_foreign_authoritative_or_diagnostic_result_refused(self):
        for fields in ({'box': 'other'}, {'replacement_authorized': True},
                {'status': 'accepted'}, {'report_sha256': None}, {'diagnostic_only': True}):
            with self.subTest(fields=fields):
                self.checker.side_effect = lambda box: {**check(box), **fields}
                with self.assertRaisesRegex(self.module.UpdateError, 'evidence'):
                    self.module.daily_check()

    def test_unactivated_schedule_cannot_report_update_required(self):
        self.schedule['activated'] = False
        self.checker.side_effect = lambda box: check(box, 'update-required')
        with self.assertRaisesRegex(self.module.UpdateError, 'evidence'):
            self.module.daily_check()

    def test_malformed_duplicate_and_undeclared_exclusions_refuse(self):
        row = {'box': 'k001', 'role': 'router', 'reason': 'test'}
        for rows in ([{'box': 'k001', 'role': 'router'}], [row, row],
                [{**row, 'reason': ' '}], [{**row, 'box': 'k003'}], [{**row, 'role': []}]):
            with self.subTest(rows=rows):
                self.schedule['policy']['exclusions'] = rows
                with self.assertRaises(self.module.UpdateError):
                    self.module.daily_check()
        self.checker.assert_not_called()

    def test_current_generation_origin_controls_dispatch_and_hash_mismatch_refuses(self):
        self.stack.close()
        module = cli()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(mock.patch.object(module, 'STATE', Path(directory)))
            stack.enter_context(mock.patch.object(module.transport, 'require_controller'))
            stack.enter_context(mock.patch.object(module.transport, 'command',
                side_effect=lambda argv: '' if 'status' in argv else 'b' * 40))
            stack.enter_context(mock.patch.object(module.transport, 'approved_engine', return_value='b' * 40))
            stack.enter_context(mock.patch.object(module.transport, 'installation_lock', side_effect=nullcontext))
            stack.enter_context(mock.patch.object(module.router_records, 'assignment', side_effect=lambda value, box: value))
            stack.enter_context(mock.patch.object(module.router_generations, 'generation', side_effect=lambda value, box: value))
            source = {'box': 'k001', 'assignment': {'current_sha256': 'a' * 64},
                      'generation': {'record_sha256': 'a' * 64, 'origin': 'legacy'}}
            stack.enter_context(mock.patch.object(module, 'accepted_source_at', return_value=source))
            legacy = stack.enter_context(mock.patch.object(module, 'check_legacy', return_value=check('k001')))
            template = stack.enter_context(mock.patch.object(module, 'check_template', return_value=check('k001')))
            module.check_current('k001')
            legacy.assert_called_once_with('k001')
            source['generation']['origin'] = 'template'
            module.check_current('k001')
            template.assert_called_once_with('k001')
            source['assignment']['current_sha256'] = 'c' * 64
            with self.assertRaisesRegex(module.UpdateError, 'different protected'):
                module.check_current('k001')
            self.assertEqual(template.call_count, 1)
            self.assertEqual(legacy.call_count, 1)


if __name__ == '__main__':
    unittest.main()
