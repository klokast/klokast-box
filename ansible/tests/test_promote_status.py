"""The operation list must show only the public fields of unfinished records."""
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
        self.switch=Path(temporary.name)/'controller-switch'
        self.proc=Path(temporary.name)/'proc';self.proc.mkdir()
        mock=patch.object(status,'SWITCH_ROOT',self.switch);mock.start();self.addCleanup(mock.stop)
        mock=patch.object(status.running,'__defaults__',(self.proc,));mock.start();self.addCleanup(mock.stop)

    def record(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));os.utime(path,(1000,1000))

    def test_lists_unfinished_records_without_private_fields(self):
        self.record(self.switch/('a'*24+'.json'),{'operation':'a'*24,'old':'boxa','new':'boxb','phase':'synchronized',
            'created_at':900,'error':'x'*600,'instance':{'private':True},'original_commit':'c'*40})
        self.record(self.switch/('b'*24+'.json'),{'operation':'b'*24,'old':'boxb','new':'boxa','phase':'complete'})
        # A record written before the start time and the cause were recorded.
        self.record(self.switch/('c'*24+'.json'),{'operation':'c'*24,'old':'boxb','new':'boxa','phase':'prepared'})
        (self.proc/'41').mkdir();(self.proc/'41'/'cmdline').write_bytes(b'python3\0ops-controller-ha\0switchover\0--operation-id\0'+b'c'*24+b'\0')
        (self.proc/str(os.getpid())).mkdir();(self.proc/str(os.getpid())/'cmdline').write_bytes(b'a'*24)
        (self.proc/'self').mkdir()
        self.assertEqual(status.unfinished(),[
            {'role':'ops','operation':'a'*24,'updated_at':1000,'target':'boxb-ops','phase':'synchronized','created_at':900,
             'error':'x'*500,'running':False},
            {'role':'ops','operation':'c'*24,'updated_at':1000,'target':'boxa-ops','phase':'prepared','created_at':None,
             'error':None,'running':True}])

    def test_last_switch_ignores_newer_unfinished_and_invalid_records(self):
        self.assertEqual(status.last_switch(),{'completed_at':None})
        for operation,phase,when in (('a','complete',1000),('b','complete',2000),('c','activated',3000),('d','abandoned',4000)):
            path=self.switch/(operation*24+'.json')
            self.record(path,{'old':'boxa','new':'boxb','phase':phase,'private':'not exposed'})
            os.utime(path,(when,when))
        self.record(self.switch/('e'*24+'.json'),{'old':'boxa','new':'../etc','phase':'complete'})
        (self.switch/('f'*24+'.json')).write_text('{')
        self.assertEqual(status.last_switch(),{'completed_at':2000})

    def test_reports_an_unreadable_record_and_keeps_the_others(self):
        self.record(self.switch/('a'*24+'.json'),{'operation':'a'*24,'old':'boxa','new':'../etc','phase':'prepared'})
        self.record(self.switch/('b'*24+'.json'),{'operation':'b'*24,'old':'boxa','new':'boxb','phase':'prepared'})
        (self.switch/('f'*24+'.json')).write_text('{')
        rows=status.unfinished()
        self.assertEqual([(r['target'],r['phase']) for r in rows],[(None,'unreadable'),('boxb-ops','prepared'),(None,'unreadable')])
        self.assertIn('unreadable record',rows[0]['error'])

class RecordRoots(unittest.TestCase):
    def test_record_roots_match_their_owners(self):
        self.assertEqual(status.SWITCH_ROOT,controller_switch.ROOT)


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
                if len(seen)==1:
                    record['phase']='synchronized';path.write_text(json.dumps(record))
                    raise RuntimeError(' standby demotion failed ')
            with patch.object(controller_switch,'ROOT',root), \
                    patch.object(controller_switch,'locks',side_effect=lambda c,h:contextlib.nullcontext(lambda:None)), \
                    patch('getpass.getuser',return_value='smith'),patch.object(controller_switch,'transition',side_effect=transition), \
                    patch('builtins.print'),patch.object(controller_switch.time,'time',return_value=1234.5):
                with self.assertRaisesRegex(RuntimeError,'demotion failed'):controller_switch.run({},args,ha)
                failed=json.loads(path.read_text())
                # The cause is added to the last checkpoint; the failed step changes no phase.
                self.assertEqual((failed['phase'],failed['created_at'],failed['error']),('synchronized',1234,'standby demotion failed'))
                controller_switch.run({},args,ha)
            self.assertNotIn('error',seen[1]);self.assertNotIn('error',json.loads(path.read_text()))
            self.assertEqual(seen[1]['created_at'],1234)

    def test_a_switch_that_fails_before_synchronization_is_abandoned(self):
        import contextlib
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'switch'
            ha=SimpleNamespace(is_local_controller=lambda c,b:True,require_development=lambda c,b:None)
            def arguments(operation,resume=None):
                return SimpleNamespace(old_active='boxa',new_active='boxb',resume=resume,operation_id=operation,dry_run_plan=False)
            def refused(*args):raise RuntimeError('boxb-ops Instance repository must match its live upstream main branch')
            with patch.object(controller_switch,'ROOT',root), \
                    patch.object(controller_switch,'locks',side_effect=lambda c,h:contextlib.nullcontext(lambda:None)), \
                    patch('getpass.getuser',return_value='smith'),patch('builtins.print'):
                with patch.object(controller_switch,'transition',side_effect=refused), self.assertRaises(RuntimeError):
                    controller_switch.run({},arguments('a'*24),ha)
                saved=json.loads((root/('a'*24+'.json')).read_text())
                self.assertEqual(saved['phase'],'abandoned')
                self.assertIn('live upstream',saved['error'])
                with self.assertRaisesRegex(RuntimeError,'abandoned before any marker changed'):
                    controller_switch.run({},arguments('a'*24,resume='a'*24),ha)
                # An abandoned switch is not listed as unfinished and permits a new switch.
                with patch.object(status,'SWITCH_ROOT',root):self.assertEqual(status.unfinished(),[])
                with patch.object(controller_switch,'transition') as transition:
                    controller_switch.run({},arguments('b'*24),ha)
                transition.assert_called_once()


if __name__=='__main__':unittest.main()
