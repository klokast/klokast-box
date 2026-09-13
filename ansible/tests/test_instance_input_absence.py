"""Actual Ansible/compiler/map/controller consumers in isolated filesystem views."""
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]


class StableConsumerTest(unittest.TestCase):
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
            self.assertEqual(result['command_matrix_contract'], 'controller-wrapper-commands-v1')
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
            # A new temporary view must produce the same signed matrix hash.
            with tempfile.TemporaryDirectory() as second:
                repeated = module.compare(inventory, registry, Path(second),
                    {'active': {'box': 'boxb', 'hostname': 'boxb-ops'},
                     'standby': {'box': 'boxa', 'hostname': 'boxa-ops'}}, tailnet)
                self.assertEqual(result, repeated)

if __name__=='__main__':unittest.main()
