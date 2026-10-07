import importlib.util
from importlib.machinery import SourceFileLoader
import io
import hashlib
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
    def test_implementation_hashes_regular_files_and_link_text_without_following(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target.py'
            target.write_text('first\n')
            link = root / 'link.py'
            link.symlink_to('target.py')
            dangling = root / 'dangling.py'
            dangling.symlink_to('absent.py')
            names = 'target.py\0link.py\0dangling.py\0'

            def git(argv):
                if 'ls-files' in argv:
                    return names
                if 'rev-parse' in argv:
                    return 'a' * 40 + '\n'
                return ' M target.py\n'

            with patch.object(source, 'REPO', root), patch.object(source, 'as_controller', side_effect=git):
                first = source.implementation()
                expected = hashlib.sha256()
                for name, kind, data in (('dangling.py', b'link\0', b'absent.py'),
                                          ('link.py', b'link\0', b'target.py'),
                                          ('target.py', b'file\0', b'first\n')):
                    expected.update(name.encode() + b'\0' + kind + hashlib.sha256(data).digest())
                self.assertEqual(first, {'commit': 'a' * 40, 'dirty': True,
                                         'source_sha256': expected.hexdigest()})
                target.write_text('second\n')
                changed_file = source.implementation()['source_sha256']
                self.assertNotEqual(first['source_sha256'], changed_file)
                link.unlink()
                link.symlink_to('absent.py')
                self.assertNotEqual(changed_file, source.implementation()['source_sha256'])

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

    def test_instance_placement_must_agree_with_active_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'klokast-instance.json'
            path.write_text('{"controllers":{"active":"boxb","standby":"boxa"}}')
            sha = __import__('hashlib').sha256(path.read_bytes()).hexdigest()
            rendered = json.dumps({'valid': True, 'kind': 'klokast.registry.v1',
                                   'inputs': [{'path': 'klokast-instance.json', 'sha256': sha}]})
            with patch.object(source, 'INSTANCE', Path(directory)), \
                    patch.object(source, 'require_controller', return_value={'hostname': 'boxa-ops'}), \
                    patch.object(source, 'as_controller', return_value=rendered), \
                    self.assertRaisesRegex(source.SourceError, 'placement differs'):
                source.snapshot()

    def test_removed_app_cli_is_rejected_before_source_or_execution(self):
        apply = wrapper('platform-apply')
        with patch.object(source, 'snapshot') as snapshot, patch.object(apply.subprocess, 'run') as run:
            for args in (['app', '--app', 'nextcloud-v2'],
                         ['network', '--box', 'site-a', '--app', 'nextcloud-v2']):
                with self.subTest(args=args), patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
                    apply.main(args)
                self.assertEqual(error.exception.code, 2)
            snapshot.assert_not_called()
            run.assert_not_called()

    def test_apply_network_and_guest_commands_keep_instance_suffix(self):
        apply = wrapper('platform-apply')
        view = {'instance': {'boxes': {'site-a': {}},
                            'tailscale': {'tailnet-dns-name': 'actual.ts.net'}}}
        base = [source.REPO / 'ansible/bin/platform-resources', '--box', 'site-a',
                '--magicdns-suffix', 'actual.ts.net']
        self.assertEqual(apply.prepare(SimpleNamespace(operation='network', box='site-a', role=None), view),
                         [['/usr/bin/doas', '/usr/local/sbin/platform-maintenance', 'network'],
                          base + ['apply-box-access']])
        for role in (None, 'bak', 'dmz', 'iot'):
            expected = base + (['--shared-guest-role', role] if role else []) + ['apply-shared-guests']
            self.assertEqual(apply.prepare(SimpleNamespace(operation='guests', box='site-a', role=role), view),
                             [expected])
        with self.assertRaisesRegex(source.SourceError,'not declared'):
            apply.prepare(SimpleNamespace(operation='network',box='foreign',role=None),view)
        with self.assertRaisesRegex(source.SourceError, 'require one --box'):
            apply.prepare(SimpleNamespace(operation='guests', box=None, role=None), view)
        with self.assertRaisesRegex(source.SourceError, 'does not accept --role'):
            apply.prepare(SimpleNamespace(operation='network', box='site-a', role='bak'), view)
        with self.assertRaisesRegex(source.SourceError, 'supported operations'):
            apply.prepare(SimpleNamespace(operation='app', box=None, role=None), view)

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
