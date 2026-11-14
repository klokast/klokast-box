"""The operation list must show only the public fields of unfinished records."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import controller_switch
import promote_status as status


class UnfinishedOperations(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.switch=Path(temporary.name)/'controller-switch';self.workspace=Path(temporary.name)/'air-workspace'
        self.proc=Path(temporary.name)/'proc';self.proc.mkdir()
        for name,value in (('SWITCH_ROOT',self.switch),('WORKSPACE_ROOT',self.workspace)):
            mock=patch.object(status,name,value);mock.start();self.addCleanup(mock.stop)
        mock=patch.object(status.running,'__defaults__',(self.proc,));mock.start();self.addCleanup(mock.stop)

    def record(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));os.utime(path,(1000,1000))

    def test_lists_unfinished_records_without_private_fields(self):
        self.record(self.switch/('a'*24+'.json'),{'operation':'a'*24,'old':'boxa','new':'boxb','phase':'synchronized',
            'created_at':900,'error':'x'*600,'instance':{'private':True},'original_commit':'c'*40})
        self.record(self.switch/('b'*24+'.json'),{'operation':'b'*24,'old':'boxb','new':'boxa','phase':'complete'})
        # A record written before the start time and the cause were recorded.
        self.record(self.workspace/('c'*24)/'operation.json',{'operation':'c'*24,'origin':'boxa','destination':'boxb',
            'phase':'closing','workspace':None,'manifest':{'private':True}})
        self.record(self.workspace/('d'*24)/'operation.json',{'origin':'boxa','destination':'boxb','phase':'closing',
            'workspace':'qualification-one'})
        self.record(self.workspace/('e'*24)/'operation.json',{'phase':'complete'})
        self.record(self.workspace/'placement.json',{'phase':'ready'})
        (self.proc/'41').mkdir();(self.proc/'41'/'cmdline').write_bytes(b'python3\0air-workspace\0move\0--operation\0'+b'c'*24+b'\0')
        (self.proc/str(os.getpid())).mkdir();(self.proc/str(os.getpid())/'cmdline').write_bytes(b'a'*24)
        (self.proc/'self').mkdir()
        self.assertEqual(status.unfinished(),[
            {'role':'ops','operation':'a'*24,'updated_at':1000,'target':'boxb-ops','phase':'synchronized','created_at':900,
             'error':'x'*500,'running':False},
            {'role':'air','operation':'c'*24,'updated_at':1000,'target':'boxb-air','phase':'closing','created_at':None,
             'error':None,'running':True}])

    def test_reports_an_unreadable_record_and_keeps_the_others(self):
        self.record(self.switch/('a'*24+'.json'),{'operation':'a'*24,'old':'boxa','new':'../etc','phase':'prepared'})
        self.record(self.switch/('b'*24+'.json'),{'operation':'b'*24,'old':'boxa','new':'boxb','phase':'prepared'})
        (self.switch/('f'*24+'.json')).write_text('{')
        rows=status.unfinished()
        self.assertEqual([(r['target'],r['phase']) for r in rows],[(None,'unreadable'),('boxb-ops','prepared'),(None,'unreadable')])
        self.assertIn('unreadable record',rows[0]['error'])

class RecordRoots(unittest.TestCase):
    def test_record_roots_match_their_owners(self):
        loader=importlib.machinery.SourceFileLoader('workspace_mover',str(Path(__file__).resolve().parents[1]/'bin/air-workspace'))
        mover=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(mover)
        self.assertEqual(status.SWITCH_ROOT,controller_switch.ROOT)
        self.assertEqual(status.WORKSPACE_ROOT,mover.ROOT)


class RecordedCause(unittest.TestCase):
    def test_switch_records_its_start_and_cause_and_clears_the_cause_on_resume(self):
        import contextlib
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'switch'
            ha=SimpleNamespace(is_local_controller=lambda c,b:True,require_development=lambda c,b:None)
            args=SimpleNamespace(old_active='boxa',new_active='boxb',resume=None,operation_id='a'*24,dry_run_plan=False)
            path=root/('a'*24+'.json');seen=[]
            def transition(config,record,path,ha,check):
                seen.append(json.loads(path.read_text()))
                if len(seen)==1:record['phase']='synchronized';raise RuntimeError(' standby demotion failed ')
            with patch.object(controller_switch,'ROOT',root), \
                    patch.object(controller_switch,'locks',side_effect=lambda c,h:contextlib.nullcontext(lambda:None)), \
                    patch('getpass.getuser',return_value='smith'),patch.object(controller_switch,'transition',side_effect=transition), \
                    patch('builtins.print'),patch.object(controller_switch.time,'time',return_value=1234.5):
                with self.assertRaisesRegex(RuntimeError,'demotion failed'):controller_switch.run({},args,ha)
                failed=json.loads(path.read_text())
                # The cause is added to the last checkpoint; the failed step changes no phase.
                self.assertEqual((failed['phase'],failed['created_at'],failed['error']),('prepared',1234,'standby demotion failed'))
                controller_switch.run({},args,ha)
            self.assertNotIn('error',seen[1]);self.assertNotIn('error',json.loads(path.read_text()))
            self.assertEqual(seen[1]['created_at'],1234)


if __name__=='__main__':unittest.main()
