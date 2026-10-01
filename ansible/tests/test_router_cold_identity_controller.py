"""Controller refuses a cold identity stage when its two live views disagree."""
from contextlib import nullcontext
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def cli():
    path = ROOT / 'ansible/bin/platform-router-update'
    loader = SourceFileLoader('router_cold_identity_controller_test', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ControllerTests(unittest.TestCase):
    def test_mismatched_live_peer_never_stages_original_identity(self):
        module = cli()
        operation, engine = 'b'*24, 'c'*40
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            result = state / operation
            result.mkdir(mode=0o700)
            module.transport.write(result / 'bootstrap-stage.json', {
                'kind': 'klokast.router-cold-filesystem-bootstrap-stage.v1',
                'box': 'k001', 'operation_id': operation,
                'engine_commit': engine, 'status': 'staged'})
            generation = module.router_generations.seal({
                'kind': 'klokast.router-generation.v1', 'box': 'k001',
                'role': 'router', 'generation_id': 'a'*24, 'origin': 'legacy',
                'engine_commit': engine})
            assignment = module.router_generations.seal({
                'kind': 'klokast.router-assignment.v1', 'box': 'k001', 'role': 'router',
                'current_sha256': generation['record_sha256'], 'previous_sha256': None,
                'operation_id': 'a'*24, 'engine_commit': engine,
                'policy_sha256': module.router_records.BASELINE_AUTHORITY_SHA256,
                'evidence_sha256': 'f'*64})
            source = {'kind': 'klokast.router-cold-identity-source.v1', 'box': 'k001',
                'operation_id': operation, 'engine_commit': engine,
                'metadata': module.router_generations.seal({
                    'kind': 'klokast.router-cold-metadata.v1', 'box': 'k001',
                    'operation_id': operation, 'engine_commit': engine,
                    'generation_sha256': generation['record_sha256']}),
                'accepted_source': {'kind': 'klokast.router-accepted-source.v1',
                    'box': 'k001', 'assignment': assignment, 'generation': generation},
                'guest_status': {'BackendState': 'Running', 'Self': {
                    'ID': 'original-id', 'HostName': 'k001-router', 'Online': True}},
                'controller_status': {'BackendState': 'Running', 'Peer': {'one': {
                    'ID': 'other-id', 'HostName': 'k001-router', 'Online': True}}}}
            module.transport.write(result / 'cold-identity-source.json', source)
            def command(argv, **kwargs):
                if 'status' in argv:
                    return ''
                if 'rev-parse' in argv:
                    return engine
                self.fail('controller tried to stage before checking peer identity')
            with mock.patch.object(module, 'STATE', state), \
                 mock.patch.object(module.transport, 'require_controller'), \
                 mock.patch.object(module.transport, 'approved_engine', return_value=engine), \
                 mock.patch.object(module.transport, 'installation_lock', return_value=nullcontext()), \
                 mock.patch.object(module.transport, 'command', side_effect=command), \
                 mock.patch.object(module.router_generations, 'generation', return_value=generation):
                with self.assertRaisesRegex(Exception, 'differs from the controller peer view'):
                    module.stage_cold_original_identity('k001', operation)
            self.assertFalse((result / 'cold-original-identity.json').exists())
            self.assertFalse((result / 'cold-identity-stage-arguments.json').exists())


if __name__ == '__main__':
    unittest.main()
