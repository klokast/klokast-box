"""An uncertain cold launch cannot authorize a second outage attempt."""
from contextlib import nullcontext
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from test_router_cold_request_controller import cli


class LaunchTests(unittest.TestCase):
    def test_exact_approval_and_lost_launch_reply_do_not_allow_relaunch(self):
        module = cli()
        operation, engine = 'b' * 24, 'c' * 40
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            result = state / operation
            result.mkdir(mode=0o700)
            now = int(time.time())
            request = module.router_generations.seal({
                'kind': 'klokast.router-cold-supervised-request.v1',
                'box': 'k001', 'operation_id': operation, 'engine_commit': engine,
                'initial_operation': 'd' * 24,
                'initial_provision': module.router_generations.seal({
                    'kind': 'klokast.router-initial-provision-pointer.v1', 'box': 'k001',
                    'engine_commit': engine, 'operation_id': 'd' * 24,
                    'source_operation': 'a' * 24, 'template_operation': 'b' * 24,
                    'selection_sha256': 'c' * 64, 'release_sha256': 'd' * 64}),
                'issued_at': now, 'expires_at': now + 900})
            module.transport.write(result / 'cold-supervisor-request.json', request)
            module.transport.write(result / 'cold-supervisor-request-stage.json', {
                'kind': 'klokast.router-command-result.v1', 'box': 'k001',
                'action': 'cold-request-stage', 'engine_commit': engine, 'result': request})
            launches = []

            def command(argv, **kwargs):
                if 'status' in argv:
                    return ''
                if 'rev-parse' in argv:
                    return engine
                launches.append(argv)
                raise module.UpdateError('lost launch reply')

            with mock.patch.object(module, 'STATE', state), \
                 mock.patch.object(module.transport, 'require_controller'), \
                 mock.patch.object(module.transport, 'approved_engine', return_value=engine), \
                 mock.patch.object(module.transport, 'installation_lock', return_value=nullcontext()), \
                 mock.patch.object(module.transport, 'command', side_effect=command):
                with self.assertRaisesRegex(module.UpdateError, 'exact staged request'):
                    module.start_cold_supervisor('k001', operation, 'f' * 64)
                self.assertFalse((result / 'cold-supervisor-launch.json').exists())
                with self.assertRaisesRegex(module.UpdateError, 'lost launch reply'):
                    module.start_cold_supervisor('k001', operation, request['record_sha256'])
                self.assertTrue((result / 'cold-supervisor-launch.json').exists())
                with self.assertRaisesRegex(module.UpdateError, 'already attempted'):
                    module.start_cold_supervisor('k001', operation, request['record_sha256'])
                self.assertEqual(len(launches), 1)


if __name__ == '__main__':
    unittest.main()
