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
        path=ROOT/'ansible/bin/compare-instance-input-absence'
        loader=SourceFileLoader('absence',str(path));spec=importlib.util.spec_from_loader(loader.name,loader)
        module=importlib.util.module_from_spec(spec);loader.exec_module(module)
        graph={'all':{'children':['ops']},'ops':{'hosts':['boxa-ops','boxb-ops']},
               '_meta':{'hostvars':{box+'-ops':{'node_name':box,'ansible_memtotal_mb':123} for box in ('boxa','boxb')}}}
        digest=lambda v:hashlib.sha256(module.canonical(v).encode()).hexdigest()
        inventory={'inventory':graph,'inventory_sha256':digest(graph),'boxes':['boxa','boxb'],'airunners':['boxa-ops-airunner'],'scopes':['execution_inventory']}
        value={'schema_version':1,'apps':{},'boxes':{'boxa':{},'boxb':{}}}
        registry={'valid':True,'engine':{'commit':'a'*40},'projection':{'registry':value,'registry_sha256':digest(value)}}
        with tempfile.TemporaryDirectory() as tmp:
            try:
                result=module.compare(inventory,registry,Path(tmp),{'active':{'box':'boxb','hostname':'boxb-ops'},'standby':{'box':'boxa','hostname':'boxa-ops'}})
            except ValueError as error:
                logs='\n'.join(p.read_text() for p in Path(tmp).glob('*.stderr'))
                self.fail(str(error)+'\n'+logs)
            self.assertTrue(result['equal'])
            self.assertTrue(result['temporary_views_removed'])
            self.assertFalse(result['live_execution_authority'])
            data=json.loads((Path(tmp)/'absent.json').read_text())
            self.assertEqual(data['controller'],'boxb-ops')
            self.assertEqual(data['inventory']['hostvars']['boxa-ops']['ansible_memtotal_mb'],123)
            # A new temporary view must produce the same signed matrix hash.
            with tempfile.TemporaryDirectory() as second:
                repeated = module.compare(inventory, registry, Path(second),
                    {'active': {'box': 'boxb', 'hostname': 'boxb-ops'},
                     'standby': {'box': 'boxa', 'hostname': 'boxa-ops'}})
                self.assertEqual(result, repeated)

if __name__=='__main__':unittest.main()
