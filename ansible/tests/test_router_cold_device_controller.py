"""Device deletion intent survives a lost broker reply without a second delete."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock

from test_router_cold_request_controller import cli


class ControllerDeviceTests(unittest.TestCase):
    def test_lost_delete_reply_reconciles_exact_absent_node(self):
        module = cli()
        operation, engine = 'a' * 24, 'b' * 40
        source = module.router_generations.seal({
            'kind': 'klokast.router-cold-device-source.v1', 'box': 'k001',
            'operation_id': operation, 'engine_commit': engine,
            'initial_operation': 'c' * 24, 'status': 'revocation-required',
            'machine_id': 'new-device', 'original_machine_id': 'old-device',
            'hostname': 'k001-router', 'completion_sha256': 'd' * 64})
        completion = module.router_generations.seal({
            'kind': 'klokast.router-cold-completion.v1', 'box': 'k001',
            'operation_id': operation, 'engine_commit': engine})
        status = {'BackendState': 'Running', 'Peer': {
            'old': {'ID': 'old-device', 'Online': True},
            'test': {'ID': 'new-device', 'Online': False, 'HostName': 'k001-router',
                     'TailscaleIPs': ['100.64.0.2']}}}
        original = {'id': '111', 'nodeId': 'old-device', 'hostname': 'k001-router',
                    'addresses': ['100.64.0.1']}
        test = {'id': '222', 'nodeId': 'new-device', 'hostname': 'k001-router',
                'addresses': ['100.64.0.2']}
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary)
            module.transport.write(result / 'cold-health-clear.json', {'result': completion})
            source['completion_sha256'] = completion['record_sha256']
            source = module.router_generations.seal({
                key: value for key, value in source.items() if key != 'record_sha256'})
            state = {'deleted': False, 'calls': 0}
            def command(argv, **kwargs):
                args = [str(item) for item in argv]
                if args[-1] == '--json':
                    return json.dumps(status)
                if args[-1].endswith('/ts-devices-list'):
                    return json.dumps({'devices': [original] if state['deleted'] else [original, test]})
                self.assertTrue(any(item.endswith('/ts-device-delete-stale') for item in args))
                self.assertTrue((result / 'cold-device-delete-intent.json').exists())
                self.assertEqual(args[-2:], ['--machine-id', 'new-device'])
                state['calls'] += 1
                state['deleted'] = True
                raise module.UpdateError('lost broker reply')
            with mock.patch.object(module, 'cold_supervisor_context', return_value=(result, engine,
                     {'initial_operation': 'c' * 24})), \
                 mock.patch.object(module, 'validate_cold_completion', return_value=completion), \
                 mock.patch.object(module, 'cold_device_source', return_value=source), \
                 mock.patch.object(module.transport, 'command', side_effect=command):
                with self.assertRaisesRegex(module.UpdateError, 'lost broker reply'):
                    module.cleanup_cold_test_device('k001', operation)
                self.assertFalse((result / 'cold-device-cleanup.json').exists())
                answer = module.cleanup_cold_test_device('k001', operation)
                self.assertEqual(answer['status'], 'already-absent')
                self.assertEqual(state['calls'], 1)
                self.assertEqual(module.cleanup_cold_test_device('k001', operation), answer)


if __name__ == '__main__':
    unittest.main()
