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
            result=module.compare(inventory,registry,Path(tmp))
            self.assertTrue(result['equal'])
            self.assertTrue(result['temporary_views_removed'])
            self.assertFalse(result['live_execution_authority'])
            data=json.loads((Path(tmp)/'absent.json').read_text())
            self.assertEqual(data['inventory']['hostvars']['boxa-ops']['ansible_memtotal_mb'],123)

if __name__=='__main__':unittest.main()
