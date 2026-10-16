"""State adoption must preserve ownership and never reseed an accepted volume."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
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

    def test_filesystem_check_stops_before_growth_or_mount_on_unresolved_errors(self):
        identity=subprocess.CompletedProcess([],0,'UUID="'+self.value['filesystem_uuid']+'" TYPE="ext4"','')
        for code in (2,3,4,8,16,32,128):
            with self.subTest(code=code), patch.object(storage.subprocess,'run',side_effect=[
                    identity,subprocess.CompletedProcess([],code,'','unresolved filesystem error')]), \
                    patch.object(storage.base,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'check requires intervention'):
                    storage.mount(self.value)
                run.assert_not_called()

    def test_checked_and_corrected_filesystems_are_grown_before_mount(self):
        identity=subprocess.CompletedProcess([],0,'UUID="'+self.value['filesystem_uuid']+'" TYPE="ext4"','')
        for code in (0,1):
            calls=[]
            with self.subTest(code=code), patch.object(storage.subprocess,'run',side_effect=[
                    identity,subprocess.CompletedProcess([],code,'','')]) as check, \
                    patch.object(storage.base,'run',side_effect=lambda argv:calls.append(argv)), \
                    patch.object(storage,'verify_capacity',side_effect=lambda device:calls.append(['capacity',device])):
                storage.mount(self.value)
                self.assertEqual(check.call_args.args[0],['e2fsck','-f','-p',storage.DEVICE])
                self.assertEqual(calls,[['resize2fs',storage.DEVICE],['capacity',storage.DEVICE],
                    ['mount','-t','ext4',storage.DEVICE,str(self.data)]])

    def test_capacity_refuses_partial_growth_and_ambiguous_headers(self):
        with patch.object(storage.Path,'read_text',return_value='524288\n'):
            for header in ('Block count: 32768\nBlock size: 4096',
                           'Block count: 65536\nBlock count: 65536\nBlock size: 4096',
                           'Block count: 65536\nBlock size: 8192',
                           'Block size: 4096'):
                with self.subTest(header=header),patch.object(storage.base,'run',return_value=header):
                    with self.assertRaises(RuntimeError):storage.verify_capacity('/dev/xvde')
            with patch.object(storage.base,'run',return_value='Block count: 65536\nBlock size: 4096'):
                storage.verify_capacity('/dev/xvde')


if __name__=='__main__':unittest.main()
