"""Actual Ansible/compiler/map/controller consumers in isolated filesystem views."""
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]


class StableConsumerTest(unittest.TestCase):
    def test_source_fixture_preserves_snapshot_validation_and_restores_guards(self):
        sys.path.insert(0, str(ROOT / 'ansible/lib'))
        import platform_source as source
        from consumer_absence_dispatch import source_fixture
        guard = source.require_controller
        with tempfile.TemporaryDirectory() as directory:
            view = Path(directory)
            instance = view / 'private/instance'
            instance.mkdir(parents=True)
            (instance / 'klokast-instance.json').write_text(json.dumps({
                'controllers': {'active': 'boxa'}}))
            for name, value in (
                ('registry', {'engine': {'commit': 'a' * 40}, 'projection': {'registry': {}}}),
                ('inventory', {}),
                ('controller', {'active': {'box': 'boxa', 'hostname': 'boxa-ops'}}),
            ):
                (view / (name + '.json')).write_text(json.dumps(value))
            with source_fixture(view):
                self.assertEqual(source.snapshot()['instance']['controllers']['active'], 'boxa')
                with patch.object(source, 'as_controller', return_value='{"valid":false}'):
                    with self.assertRaisesRegex(source.SourceError, 'Instance validation failed'):
                        source.snapshot()
                with self.assertRaisesRegex(AssertionError, 'unrecognized command'):
                    source.as_controller(['unexpected-command'])
            self.assertIs(source.require_controller, guard)

    def test_command_fixture_keeps_lock_and_runtime_inside_test_process(self):
        sys.path.insert(0, str(ROOT / 'ansible/lib'))
        from consumer_absence_commands import prepare
        with tempfile.TemporaryDirectory() as directory:
            view = Path(directory)
            (view / 'ansible/bin').mkdir(parents=True)
            shutil.copytree(ROOT / 'ansible/lib', view / 'ansible/lib')
            (view / 'private').mkdir()
            entrypoint = view / 'ansible/bin/platform-resources'
            # Use the real runtime. Access to its production lock would fail.
            original = ('import sys\n'
                'sys.path.insert(0, ' + repr(str(ROOT / 'ansible/lib')) + ')\n'
                'import platform_resource_runtime as runtime\n'
                'def main():\n'
                '    with runtime.vm_update_installation_lock():\n'
                '        print(runtime.RUN_ROOT)\n'
                'if __name__ == "__main__": main()\n')
            entrypoint.write_text(original)
            which = shutil.which
            # prepare only records this executable; this test invokes no Ansible.
            with patch('consumer_absence_commands.shutil.which', side_effect=lambda name:
                       sys.executable if name == 'ansible-inventory' else which(name)):
                env = prepare(view, {'engine': {'commit': 'a' * 40}}, {},
                              {'active': {'box': 'boxa', 'hostname': 'boxa-ops'}},
                              {'groups': [], 'magicdns_suffix': 'example.ts.net'})
            result = subprocess.run([sys.executable, str(entrypoint)], env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(view / 'runtime'))
            self.assertEqual(entrypoint.with_name('platform-resources-fixture-source').read_text(), original)

    def test_command_paths_keep_operation_and_unrelated_data_paths(self):
        import sys
        sys.path.insert(0, str(ROOT / 'ansible/lib'))
        from consumer_absence_commands import stable
        view = Path('/tmp/fixture-view')
        value = {'input': str(view / '.run/platform-resources/shared-guests-apply-abcdefgh/boxa.yml'),
                 'data': '/srv/app/abcdefgh',
                 'remote': '/home/neo/.cache/klokast-platform-resources/verify-12345678/desired.json'}
        result = stable(value, view)
        self.assertEqual(result['input'], '<view>/.run/platform-resources/shared-guests-apply-<temporary>/boxa.yml')
        self.assertEqual(result['data'], value['data'])
        self.assertIn('verify-<temporary>', result['remote'])

    def test_nested_paths_are_stable_across_runs_without_hiding_settings(self):
        loader = SourceFileLoader('stable_absence', str(ROOT/'ansible/bin/compare-instance-input-absence'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        results = []
        for root in (Path('/tmp/one/isolated-inputs-abc'), Path('/tmp/two/isolated-inputs-xyz')):
            value = {'manifests': [{'path': str(root/'view/apps/music/platform-resources.yml')}],
                     'enabled': False, 'other_path': '/srv/music'}
            results.append(module.stable_consumer(value, root))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]['manifests'][0]['path'], '<isolated-output>/view/apps/music/platform-resources.yml')
        self.assertEqual(results[0]['other_path'], '/srv/music')
        self.assertIs(results[0]['enabled'], False)


@unittest.skipUnless(shutil.which('ansible-inventory'), 'Ansible is required for the actual consumer check')
class InputAbsenceTest(unittest.TestCase):
    def test_effective_settings_are_equal_without_legacy_tree_or_yaml(self):
        self.check_fixture({'schema_version': 1, 'apps': {}, 'boxes': {'boxa': {}, 'boxb': {}}})

    def test_all_supported_wrappers_with_enabled_app_intent(self):
        single = {'active_master': 'boxa'}
        pair = dict(single, passive_backup='boxb')
        apps = {app: {'enabled': True, 'placement': dict(pair)}
                for app in ('nextcloud', 'nextcloud-v2', 'immich')}
        apps['nextcloud-v2']['runtime_state'] = 'stopped'
        apps.update({app: {'enabled': True, 'placement': dict(single)}
                     for app in ('household-vpn', 'local-ingress', 'torrent', 'static-site')})
        apps['household-vpn']['app_vms'] = {'gateway': {'boxa': {'vm_ipv4_address': '192.168.200.40'}}}
        apps['torrent']['app_vms'] = {'torrent': {'boxa': {'vm_ipv4_address': '192.168.200.30'}}}
        for app, resource, ip, mac in (
                ('music', 'local-audio-endpoint', '192.168.150.60', '02:00:00:00:00:01'),
                ('print-server', 'printer', '192.168.150.78', '02:00:00:00:00:02')):
            apps[app] = {'enabled': True, 'placement': {'boxes': ['boxa']},
                         'devices': {resource: {'boxa': {'ipv4_address': ip, 'mac': mac,
                                                       'hostname': 'boxa-' + resource}}}}
        capabilities = ['overlay', 'local-lan', 'vpn-egress', 'edge-ingress']
        value = {'schema_version': 1, 'apps': apps, 'boxes': {
            box: {'access': {'available_capabilities': capabilities, 'enabled_capabilities': capabilities}}
            for box in ('boxa', 'boxb')}}
        self.check_fixture(value, repeat=False)

    def check_fixture(self, value, repeat=True):
        path=ROOT/'ansible/bin/compare-instance-input-absence'
        loader=SourceFileLoader('absence',str(path));spec=importlib.util.spec_from_loader(loader.name,loader)
        module=importlib.util.module_from_spec(spec);loader.exec_module(module)
        graph={'all':{'children':['ops']},'ops':{'hosts':['boxa-ops','boxb-ops']},
               '_meta':{'hostvars':{box+'-ops':{'node_name':box,'ansible_memtotal_mb':123} for box in ('boxa','boxb')}}}
        digest=lambda v:hashlib.sha256(module.canonical(v).encode()).hexdigest()
        inventory={'inventory':graph,'inventory_sha256':digest(graph),'boxes':['boxa','boxb'],'airunners':['boxa-ops-airunner'],'scopes':['execution_inventory']}
        registry={'valid':True,'engine':{'commit':'a'*40},'projection':{'registry':value,'registry_sha256':digest(value)}}
        tailnet = {'magicdns_suffix': 'example.ts.net', 'groups': [
            {'name': 'operators', 'members': ['operator@example.test']},
            {'name': 'family', 'members': ['operator@example.test']}]}
        with tempfile.TemporaryDirectory() as tmp:
            try:
                result=module.compare(inventory,registry,Path(tmp),{'active':{'box':'boxb','hostname':'boxb-ops'},'standby':{'box':'boxa','hostname':'boxa-ops'}}, tailnet)
            except ValueError as error:
                logs='\n'.join(p.read_text() for p in Path(tmp).glob('*.stderr'))
                self.fail(str(error)+'\n'+logs)
            self.assertTrue(result['equal'])
            self.assertTrue(result['temporary_views_removed'])
            self.assertFalse(result['live_execution_authority'])
            self.assertEqual(result['command_matrix_contract'], 'controller-wrapper-commands-v2')
            data=json.loads((Path(tmp)/'absent.json').read_text())
            self.assertEqual(data['controller'],'boxb-ops')
            self.assertEqual(data['inventory']['hostvars']['boxa-ops']['ansible_memtotal_mb'],123)
            commands = data['controller-commands']['commands']
            for app in ('household-vpn', 'local-ingress', 'music', 'print-server', 'torrent',
                        'nextcloud', 'static-site', 'immich'):
                self.assertIn(app + '/verify', commands)
                self.assertIn(app + '/install', commands)
            self.assertIn('nextcloud-v2/infra-prepare', commands)
            if value['apps']:
                self.assertEqual(commands['nextcloud-v2/verify']['result'], 'success')
                self.assertTrue(commands['music/verify']['events'])
            if not repeat:
                return
            # A new temporary view must produce the same evidence hashes.
            with tempfile.TemporaryDirectory() as second:
                repeated = module.compare(inventory, registry, Path(second),
                    {'active': {'box': 'boxb', 'hostname': 'boxb-ops'},
                     'standby': {'box': 'boxa', 'hostname': 'boxa-ops'}}, tailnet)
                self.assertEqual(result, repeated)

if __name__=='__main__':unittest.main()
