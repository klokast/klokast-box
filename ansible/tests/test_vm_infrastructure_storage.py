"""State adoption must preserve ownership and never reseed an accepted volume."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ansible/roles/vm-template-builder/files'))
import vm_infrastructure_storage as storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.data=self.root/'data';self.data.mkdir()
        self.old=self.root/'old';self.old.mkdir()
        self.value={'box':'boxa','role':'air','accounts':{'neo':[1000,1000],'agent':[1004,1004]},
                    'filesystem_uuid':'11111111-2222-3333-4444-555555555555',
                    'operation':'a'*24,'machine_id':'b'*64,'migrate':True}
        for name,value in (('DATA',self.data),):
            p=patch.object(storage,name,value);p.start();self.addCleanup(p.stop)
        p=patch.object(storage.os,'chown');p.start();self.addCleanup(p.stop)
        p=patch.object(storage.os,'sync');p.start();self.addCleanup(p.stop)
        def transfer(source,target,kind):
            target.parent.mkdir(parents=True,exist_ok=True)
            if kind=='directory':shutil.copytree(source,target,dirs_exist_ok=True,symlinks=True)
            else:shutil.copy2(source,target)
        p=patch.object(storage,'copy_state',side_effect=transfer);self.transfer=p.start();self.addCleanup(p.stop)
        home=self.old/'home/agent';home.mkdir(parents=True);(home/'history.db').write_bytes(b'original')

    def test_first_seed_preserves_state_and_replacement_never_restores_old_content(self):
        storage.seed(self.old,self.value)
        target=self.data/'state/home/agent/history.db'
        self.assertEqual(target.read_bytes(),b'original')
        target.write_bytes(b'new state after boot')
        storage.seed(self.old,dict(self.value,migrate=False,operation='c'*24))
        self.assertEqual(target.read_bytes(),b'new state after boot')
        self.assertEqual(self.transfer.call_count,1)

    def test_unfinished_copy_is_bound_to_exact_source_and_operation(self):
        original=self.transfer.side_effect
        self.transfer.side_effect=RuntimeError('copy interrupted')
        with self.assertRaisesRegex(RuntimeError,'interrupted'):storage.seed(self.old,self.value)
        self.transfer.side_effect=original
        with self.assertRaisesRegex(RuntimeError,'another unfinished'):
            storage.seed(self.old,dict(self.value,operation='c'*24))
        storage.seed(self.old,self.value)
        self.assertFalse((self.data/'.infrastructure-migration.json').exists())

    def test_completed_seed_cannot_be_reused_by_another_first_migration(self):
        storage.seed(self.old,self.value)
        with self.assertRaisesRegex(RuntimeError,'another migration'):
            storage.seed(self.old,dict(self.value,operation='c'*24))

    def test_unassigned_content_and_missing_consent_are_refused(self):
        with self.assertRaisesRegex(RuntimeError,'explicit first'):
            storage.seed(self.old,dict(self.value,migrate=False))
        (self.data/'unowned').write_text('keep')
        with self.assertRaisesRegex(RuntimeError,'contains files'):storage.seed(self.old,self.value)
        self.transfer.assert_not_called()

    def test_symlink_parent_cannot_redirect_migration(self):
        (self.old/'home/agent').rename(self.old/'outside')
        (self.old/'home/agent').symlink_to(self.old/'outside')
        with self.assertRaisesRegex(RuntimeError,'symbolic-link parent'):storage.seed(self.old,self.value)
        self.transfer.assert_not_called()

    def test_reused_storage_identity_and_account_conflicts_are_refused(self):
        storage.seed(self.old,self.value)
        for key,value in (('box','boxb'),('filesystem_uuid','22222222-2222-3333-4444-555555555555'),
                          ('accounts',{'neo':[1000,1000],'agent':[1005,1005]})):
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'ownership differs'):
                storage.seed(self.old,dict(self.value,**{key:value}))
        for ids in ([1000,1004],[True,1004],[-1,1004],[1004,1000]):
            value=copy.deepcopy(self.value);value['accounts']['agent']=ids
            with self.assertRaisesRegex(RuntimeError,'numeric account'):storage.contract(value)

    def test_controller_only_seeds_allowlisted_files_but_binds_atomic_write_directories(self):
        mappings=storage.mappings('ops')
        self.assertIn('home/smith/private/klokast/dom0-console.yml',mappings)
        self.assertIn('home/smith/private/klokast/vm-replacement-inputs',mappings)
        self.assertNotIn('home/smith/private/klokast/instance',mappings)
        self.assertNotIn('home/smith/src',mappings)
        self.assertNotIn('etc/klokast/controller-ha.json',mappings)
        self.assertFalse(set(mappings)&set(storage.PROVIDER_FILES))
        bindings=storage.bind_mappings('ops')
        self.assertIn('home/smith/private/klokast',bindings)
        self.assertTrue(all(kind=='directory' for kind in bindings.values()))


if __name__=='__main__':unittest.main()
