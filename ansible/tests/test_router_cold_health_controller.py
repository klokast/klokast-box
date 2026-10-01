"""Controller health checks cannot run for another staged cold operation."""
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from contextlib import nullcontext

ROOT = Path(__file__).resolve().parents[2]


def cli():
    path = ROOT / 'ansible/bin/platform-router-update'
    loader = SourceFileLoader('router_cold_health_controller_test', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ControllerHealthTests(unittest.TestCase):
    def test_completion_rejects_changed_source_or_health_receipt(self):
        module = cli()
        operation, engine = 'b' * 24, 'c' * 40
        request = {name: value * 64 for name, value in (
            ('metadata_sha256', 'a'), ('generation_sha256', 'b'),
            ('identity_sha256', 'c'), ('baseline_sha256', 'd'))}
        health = {'record_sha256': 'e' * 64}
        completion = module.router_generations.seal({
            'kind': 'klokast.router-cold-completion.v1', 'box': 'k001',
            'operation_id': operation, 'engine_commit': engine, **request,
            'health_sha256': health['record_sha256']})
        receipt = {'kind': 'klokast.router-command-result.v1', 'box': 'k001',
            'action': 'cold-health-clear', 'engine_commit': engine, 'result': completion}
        self.assertEqual(module.validate_cold_completion(receipt, 'k001', operation,
            engine, request=request, health=health), completion)
        for key in (*request, 'health_sha256', 'operation_id', 'engine_commit'):
            with self.subTest(field=key):
                changed = {name: value for name, value in completion.items() if name != 'record_sha256'}
                changed[key] = 'f' * len(completion[key])
                changed = module.router_generations.seal(changed)
                with self.assertRaisesRegex(module.UpdateError, 'differs'):
                    module.validate_cold_completion({**receipt, 'result': changed},
                        'k001', operation, engine, request=request, health=health)

    def test_finish_reconciles_lost_reply_without_repeating_fenced_health_stage(self):
        module = cli()
        operation, engine = 'b' * 24, 'c' * 40
        request = {name: value * 64 for name, value in (
            ('metadata_sha256', 'a'), ('generation_sha256', 'b'),
            ('identity_sha256', 'c'), ('baseline_sha256', 'd'))}
        completion = module.router_generations.seal({
            'kind': 'klokast.router-cold-completion.v1', 'box': 'k001',
            'operation_id': operation, 'engine_commit': engine, **request,
            'health_sha256': 'e' * 64})
        receipt = {'kind': 'klokast.router-command-result.v1', 'box': 'k001',
            'action': 'cold-health-clear', 'engine_commit': engine, 'result': completion}
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary)
            def command(argv, **kwargs):
                self.assertIn(module.REPO / 'ansible/playbooks/74-router-cold-recovery-completion.yml', argv)
                module.transport.write(result / 'cold-health-clear.json', receipt)
            with mock.patch.object(module, 'cold_supervisor_context', return_value=(result, engine, request)), \
                 mock.patch.object(module, 'read_cold_supervisor', return_value={
                     'finished': True, 'rc': 0, 'pointer': {'result': {'phase': None}}}), \
                 mock.patch.object(module, 'check_cold_recovery_health') as health, \
                 mock.patch.object(module.transport, 'installation_lock', return_value=nullcontext()), \
                 mock.patch.object(module.transport, 'command', side_effect=command) as dispatch:
                answer = module.finish_cold_recovery('k001', operation)
                self.assertEqual(answer['completion_sha256'], completion['record_sha256'])
                self.assertEqual(module.finish_cold_recovery('k001', operation), answer)
                health.assert_not_called()
                self.assertEqual(dispatch.call_count, 2)

    def test_finish_requires_full_health_for_fenced_original_and_waits_for_worker(self):
        module = cli()
        operation, engine = 'b' * 24, 'c' * 40
        request = {name: value * 64 for name, value in (
            ('metadata_sha256', 'a'), ('generation_sha256', 'b'),
            ('identity_sha256', 'c'), ('baseline_sha256', 'd'))}
        completion = module.router_generations.seal({
            'kind': 'klokast.router-cold-completion.v1', 'box': 'k001',
            'operation_id': operation, 'engine_commit': engine, **request})
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary)
            def checked_health(*args, **kwargs):
                module.transport.write(result / 'cold-health-clear.json', {
                    'kind': 'klokast.router-command-result.v1', 'box': 'k001',
                    'action': 'cold-health-clear', 'engine_commit': engine, 'result': completion})
            with mock.patch.object(module, 'cold_supervisor_context', return_value=(result, engine, request)), \
                 mock.patch.object(module, 'read_cold_supervisor', return_value={
                     'finished': False, 'rc': None, 'pointer': {'result': {'phase': 'restoring'}}}) as progress, \
                 mock.patch.object(module, 'check_cold_recovery_health', side_effect=checked_health) as health:
                with self.assertRaisesRegex(module.UpdateError, 'wait for'):
                    module.finish_cold_recovery('k001', operation)
                health.assert_not_called()
                progress.return_value.update(finished=True, rc=0)
                self.assertEqual(module.finish_cold_recovery('k001', operation)['completion_sha256'],
                                 completion['record_sha256'])
                health.assert_called_once_with('k001', operation, finish=True)

    def test_changed_staged_engine_refuses_before_running_health_playbook(self):
        module = cli()
        operation, engine = 'b'*24, 'c'*40
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            result = state / operation
            result.mkdir(mode=0o700)
            module.transport.write(result / 'cold-original-identity-stage.json', {
                'kind': 'klokast.router-command-result.v1', 'box': 'k001',
                'action': 'cold-stage-identity', 'engine_commit': 'd'*40,
                'result': {}})
            module.transport.write(result / 'cold-dependent-baseline.json', {
                'kind': 'klokast.router-command-result.v1', 'box': 'k001',
                'action': 'cold-baseline-capture', 'engine_commit': engine,
                'result': {}})
            def command(argv, **kwargs):
                if 'status' in argv:
                    return ''
                if 'rev-parse' in argv:
                    return engine
                self.fail('controller ran health checks for a different staged engine')
            with mock.patch.object(module, 'STATE', state), \
                 mock.patch.object(module.transport, 'require_controller'), \
                 mock.patch.object(module.transport, 'approved_engine', return_value=engine), \
                 mock.patch.object(module.transport, 'command', side_effect=command):
                with self.assertRaisesRegex(Exception, 'differs from the staged original'):
                    module.check_cold_recovery_health('k001', operation)
            self.assertFalse((result / 'cold-recovery-arguments.json').exists())
            self.assertFalse((result / 'cold-recovery-health.json').exists())


if __name__ == '__main__':
    unittest.main()
