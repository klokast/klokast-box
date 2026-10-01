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
                    module.stage_cold_supervisor_request('k001', operation)
            self.assertFalse((result / 'cold-supervisor-request.json').exists())


if __name__ == '__main__':
    unittest.main()
