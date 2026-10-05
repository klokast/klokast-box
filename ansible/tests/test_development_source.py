import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))
import platform_source as source
import platform_maintenance as maintenance
import platform_network as network


def wrapper(name):
    loader = SourceFileLoader('development_' + name.replace('-', '_'), str(ROOT / 'ansible/bin' / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class SourceTests(unittest.TestCase):
    def test_duplicate_desired_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'instance.json'
            path.write_text('{"boxes":{},"boxes":{"other":{}}}')
            with self.assertRaisesRegex(source.SourceError, 'duplicate'): source.read_json(path)

    def test_production_or_unknown_lifecycle_cannot_use_development_tools(self):
        for value in ({'schema_version':1,'lifecycle':'production'}, {},
                      {'schema_version':1,'lifecycle':'development','override':True}):
            with patch.object(source, 'MODE') as mode, patch.object(source, 'read_json', return_value=value):
                mode.lstat.return_value = SimpleNamespace(st_uid=0, st_mode=0o644)
                with self.assertRaisesRegex(source.SourceError, 'development only'): source.require_development()

    def test_unprotected_deployment_property_is_rejected(self):
        with patch.object(source, 'MODE') as mode:
            mode.lstat.return_value = SimpleNamespace(st_uid=1000, st_mode=0o644)
            with self.assertRaisesRegex(source.SourceError, 'root-owned'): source.require_development()


    def test_source_change_during_rendering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'klokast-instance.json'
            path.write_text('{"boxes":{}}')
            sha = __import__('hashlib').sha256(path.read_bytes()).hexdigest()
            def render(*args):
                path.write_text('{"boxes":{"other":{}}}')
                return json.dumps({'valid':True, 'kind':'klokast.registry.v1',
                                   'inputs':[{'path':'klokast-instance.json','sha256':sha}]})
            with patch.object(source,'INSTANCE',Path(directory)), patch.object(source,'require_controller'), patch.object(source,'as_controller',side_effect=render):
                with self.assertRaisesRegex(source.SourceError,'changed during rendering'):source.snapshot()

    def test_apply_rejects_unsupported_or_absent_apps_before_commands(self):
        app = wrapper('platform-apply')
        view = {'instance':{'boxes':{'site-a':{}}, 'apps':{'music':{'desired-state':'present'}, 'nextcloud-v2':{'desired-state':'absent'}}}}
        for name in ('music','nextcloud-v2','unknown'):
            with self.assertRaises(source.SourceError):
                app.prepare(SimpleNamespace(operation='app', box=None, role=None, app=name),view)
        with self.assertRaisesRegex(source.SourceError,'not declared'):
            app.prepare(SimpleNamespace(operation='network',box='foreign',role=None,app=None),view)

    def test_single_controller_projection_has_no_fake_standby(self):
        result=source.controllers({'instance':{'controllers':{'active':'site-a'}},
            'instance_sha256':'a'*64,'implementation':{'commit':'b'*40}})
        self.assertEqual(set(result['controllers']),{'active'})


class NetworkRecoveryTests(unittest.TestCase):
    def test_recovery_refuses_foreign_policy_and_restores_only_our_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            state=root/'operations'/'a';state.mkdir(parents=True)
            work=root/'work';work.mkdir()
            (state/'preimage.body').write_bytes(b'old')
            (state/'candidate.body').write_bytes(b'candidate')
            for current,expected in ((b'foreign',None),(b'old','previous-policy-present'),(b'candidate','restored')):
                (work/'recovery.body').write_bytes(current)
                (work/'after.body').write_bytes(b'old')
                with patch.object(network,'ROOT',root/'operations'),patch.object(network,'call') as call:
                    if expected is None:
                        with self.assertRaisesRegex(source.SourceError,'changed externally'):network.recovery({'operation':'a'},work)
                        self.assertEqual([c.args[0] for c in call.call_args_list],['get-recovery'])
                    else:
                        self.assertEqual(network.recovery({'operation':'a'},work),expected)
                        self.assertEqual(call.call_count,3 if expected=='restored' else 1)

    def test_record_write_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'record.json'
            maintenance.write(path,{'stage':'pending'})
            self.assertEqual(source.read_json(path),{'stage':'pending'})
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual([p.name for p in path.parent.iterdir()],['record.json'])


if __name__ == '__main__':unittest.main()
