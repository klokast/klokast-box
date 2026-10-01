"""A mismatched backup receipt cannot become a staged outage selector."""
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
    loader = SourceFileLoader('router_cold_request_controller_test', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ControllerTests(unittest.TestCase):
    def test_backup_for_another_metadata_bundle_never_stages(self):
        module = cli()
        operation, engine = 'b' * 24, 'c' * 40
        seal = module.router_generations.seal
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            result = state / operation
            result.mkdir(mode=0o700)
            cache = state / ('cold-bootstrap-' + operation)
            cache.mkdir(mode=0o700)
            common = {'box': 'k001', 'operation_id': operation, 'engine_commit': engine}
            metadata = seal({'kind': 'klokast.router-cold-metadata.v1', **common,
                             'generation_sha256': 'a' * 64})
            disk = seal({'kind': 'klokast.router-cold-disk.v1', **common,
                         'metadata_sha256': 'd' * 64, 'stage': 'allocated'})
            identity = seal({'kind': 'klokast.router-cold-original-identity.v1', **common,
                             'metadata_sha256': metadata['record_sha256'],
                             'generation_sha256': metadata['generation_sha256']})
            baseline = seal({'kind': 'klokast.router-cold-dependent-baseline.v1', **common,
                             'identity_sha256': identity['record_sha256']})
            capsule = seal({'kind': 'klokast.router-cold-filesystem-bootstrap.v1', **common})
            for name, action, value in (
                    ('cold-metadata-capture.json', 'cold-capture-metadata', metadata),
                    ('cold-backup-allocation.json', 'cold-allocate-backup', disk),
                    ('cold-original-identity-stage.json', 'cold-stage-identity', identity),
                    ('cold-dependent-baseline.json', 'cold-baseline-capture', baseline)):
                module.transport.write(result / name, {
                    'kind': 'klokast.router-command-result.v1', 'box': 'k001',
                    'engine_commit': engine, 'action': action, 'result': value})
            module.transport.write(cache / 'filesystem-bootstrap.json', capsule)

            def command(argv, **kwargs):
                if 'status' in argv:
                    return ''
                if 'rev-parse' in argv:
                    return engine
                self.fail('controller tried to stage a mismatched backup')

            with mock.patch.object(module, 'STATE', state), \
                 mock.patch.object(module, 'CACHE', state), \
                 mock.patch.object(module.transport, 'require_controller'), \
                 mock.patch.object(module.transport, 'approved_engine', return_value=engine), \
                 mock.patch.object(module.transport, 'installation_lock', return_value=nullcontext()), \
                 mock.patch.object(module.transport, 'command', side_effect=command):
                with self.assertRaisesRegex(Exception, 'one prepared original'):
                    module.stage_cold_supervisor_request('k001', operation, state / ('a' * 24), 'e' * 24)
            self.assertFalse((result / 'cold-supervisor-request.json').exists())

    def test_stage_and_retry_bind_the_same_prebuilt_initial_operation(self):
        module = cli()
        operation, engine = 'b' * 24, 'c' * 40
        seal = module.router_generations.seal
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            result = state / operation
            result.mkdir(mode=0o700)
            capsule_dir = state / ('cold-bootstrap-' + operation)
            capsule_dir.mkdir(mode=0o700)
            common = {'box': 'k001', 'operation_id': operation, 'engine_commit': engine}
            generation = {'record_sha256': 'a' * 64, 'xen': {'uuid': 'saved-xen-uuid'}}
            metadata = seal({'kind': 'klokast.router-cold-metadata.v1', **common,
                             'generation_sha256': generation['record_sha256']})
            identity = seal({'kind': 'klokast.router-cold-original-identity.v1', **common,
                'metadata_sha256': metadata['record_sha256'],
                'generation_sha256': generation['record_sha256']})
            values = (
                ('cold-metadata-capture', 'cold-capture-metadata', metadata),
                ('cold-backup-allocation', 'cold-allocate-backup', seal({
                    'kind': 'klokast.router-cold-disk.v1', **common,
                    'metadata_sha256': metadata['record_sha256'], 'stage': 'allocated',
                    'backup': {'uuid': 'backup-uuid'}})),
                ('cold-original-identity-stage', 'cold-stage-identity', identity),
                ('cold-dependent-baseline', 'cold-baseline-capture', seal({
                    'kind': 'klokast.router-cold-dependent-baseline.v1', **common,
                    'identity_sha256': identity['record_sha256']})))
            for name, action, value in values:
                module.transport.write(result / (name + '.json'), {
                    'kind': 'klokast.router-command-result.v1', 'box': 'k001',
                    'engine_commit': engine, 'action': action, 'result': value})
            module.transport.write(capsule_dir / 'filesystem-bootstrap.json', seal({
                'kind': 'klokast.router-cold-filesystem-bootstrap.v1', **common}))
            module.transport.write(result / 'cold-accepted-source.json', {'result': {'generation': generation}})
            def command(argv, **kwargs):
                if 'status' in argv:
                    return ''
                if 'rev-parse' in argv:
                    return engine
                self.assertIn(module.REPO / 'ansible/playbooks/74-router-cold-supervisor-request-stage.yml', argv)
                request = module.transport.load(result / 'cold-supervisor-request.json')
                module.transport.write(result / 'cold-supervisor-request-stage.json', {
                    'kind': 'klokast.router-command-result.v1', 'box': 'k001',
                    'engine_commit': engine, 'action': 'cold-request-stage', 'result': request})
            selected = {'selection_sha256': 'd' * 64, 'release': {'receipt_sha256': 'e' * 64}}
            with mock.patch.object(module, 'STATE', state), \
                 mock.patch.object(module, 'CACHE', state), \
                 mock.patch.object(module.transport, 'require_controller'), \
                 mock.patch.object(module.transport, 'approved_engine', return_value=engine), \
                 mock.patch.object(module.transport, 'installation_lock', side_effect=nullcontext), \
                 mock.patch.object(module.transport, 'command', side_effect=command), \
                 mock.patch.object(module.router_generations, 'generation', return_value=generation), \
                 mock.patch.object(module, 'initial_template_source', return_value=selected) as template:
                inputs, template_operation = state / ('f' * 24), 'a' * 24
                answer = module.stage_cold_supervisor_request('k001', operation, inputs, template_operation)
                self.assertEqual(module.stage_cold_supervisor_request('k001', operation, inputs, template_operation), answer)
                template.assert_called_with('k001', inputs, template_operation, engine)
                request = module.transport.load(result / 'cold-supervisor-request.json')
                pointer = request['initial_provision']
                self.assertEqual(pointer['operation_id'], answer['initial_operation'])
                self.assertEqual(pointer['source_operation'], inputs.name)
                self.assertEqual(pointer['release_sha256'], selected['release']['receipt_sha256'])
                self.assertFalse((state / 'initial-provision-k001.json').exists())
                selected['release']['receipt_sha256'] = 'f' * 64
                with self.assertRaisesRegex(module.UpdateError, 'retry changed'):
                    module.stage_cold_supervisor_request('k001', operation, inputs, template_operation)


if __name__ == '__main__':
    unittest.main()
