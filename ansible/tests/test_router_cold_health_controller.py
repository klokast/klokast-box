"""Controller health checks cannot run for another staged cold operation."""
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def cli():
    path = ROOT / 'ansible/bin/platform-router-update'
    loader = SourceFileLoader('router_cold_health_controller_test', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ControllerHealthTests(unittest.TestCase):
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
