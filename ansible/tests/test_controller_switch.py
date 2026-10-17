import copy
import json
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import controller_switch as switch


class PlannedSwitch(unittest.TestCase):
    def setup_case(self):
        self.states={'old':dict(reachable=True,active=True,role='active',active_box='old'),
                     'new':dict(reachable=True,active=False,role='standby',active_box='old')}
        self.events=[]
        def marker(config,box,role,active):
            if role=='active':self.assertFalse(self.states['old']['active'])
            self.events.append((box,role))
            self.states[box].update(active=role=='active',role=role,active_box=active)
        self.ha=SimpleNamespace(controller_status=lambda c,b:copy.deepcopy(self.states[b]),
            require_readiness=Mock(),verify_credentials=Mock(),set_marker=marker,
            controller_sh=Mock(return_value=SimpleNamespace(returncode=78)),verify_promotion=Mock())
        return {'operation':'a'*24,'old':'old','new':'new','phase':'prepared','instance':{},'original_commit':'b'*40}

    def test_resume_after_every_transition_never_has_two_active_controllers(self):
        for interruption in range(1,8):
            with self.subTest(interruption=interruption), tempfile.TemporaryDirectory() as temporary:
                record=self.setup_case(); path=Path(temporary)/'state.json'
                switch.write(path,record)
                original_write=switch.write; calls=[]
                def fail_write(path,value):
                    calls.append(value['phase'])
                    if len(calls)==interruption:raise RuntimeError('interrupted before checkpoint')
                    original_write(path,value)
                with patch.object(switch,'credential_identity',side_effect=lambda c,b,h:{'provider':b}), \
                        patch.object(switch,'publish'),patch.object(switch,'write',side_effect=fail_write):
                    try: switch.transition({},record,path,self.ha)
                    except RuntimeError as error:self.assertIn('interrupted',str(error))
                record=json.loads(path.read_text())
                with patch.object(switch,'credential_identity',side_effect=lambda c,b,h:{'provider':b}),patch.object(switch,'publish'):
                    result=switch.transition({},record,path,self.ha)
                self.assertEqual(result['phase'],'complete')
                self.assertEqual(self.events,[('old','standby'),('new','active')])

    def test_unavailable_or_conflicting_peer_does_not_mutate(self):
        for change in ({'reachable':False},{'active':True,'role':'active'}):
            record=self.setup_case();self.states['new'].update(change)
            with self.assertRaises(RuntimeError):switch.transition({},record,Path('/unused'),self.ha)
            self.assertEqual(self.events,[])

    def test_missing_or_shared_credentials_stop_before_publication(self):
        record=self.setup_case()
        self.ha.verify_credentials.side_effect=RuntimeError('credentials absent')
        with patch.object(switch,'publish') as publish:
            with self.assertRaisesRegex(RuntimeError,'credentials absent'):switch.transition({},record,Path('/unused'),self.ha)
            publish.assert_not_called()
        record=self.setup_case()
        with patch.object(switch,'credential_identity',return_value={'provider':'shared'}),patch.object(switch,'publish') as publish:
            with self.assertRaisesRegex(RuntimeError,'independent'):switch.transition({},record,Path('/unused'),self.ha)
            publish.assert_not_called()
        self.assertEqual(self.events,[])

    def test_missing_demotion_proof_never_activates_destination(self):
        record=self.setup_case();record['phase']='synchronized'
        self.ha.controller_sh.return_value.returncode=0
        with self.assertRaisesRegex(RuntimeError,'still start'):switch.transition({},record,Path('/unused'),self.ha)
        self.assertEqual(self.events,[('old','standby')])

    def test_completed_record_does_not_override_current_authority(self):
        record=self.setup_case();record['phase']='complete'
        with self.assertRaisesRegex(RuntimeError,'no longer matches'):switch.transition({},record,Path('/unused'),self.ha)
        self.assertEqual(self.events,[])

    def test_lost_lock_prevents_activation_after_demotion(self):
        record=self.setup_case();record['phase']='demoted'
        self.states['old'].update(active=False,role='standby',active_box='new')
        def lost():raise RuntimeError('controller lock connection ended')
        with self.assertRaisesRegex(RuntimeError,'lock connection ended'):
            switch.transition({},record,Path('/unused'),self.ha,lost)
        self.assertFalse(self.states['new']['active'])
        self.assertEqual(self.events,[])


class Publication(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.instance=self.root/'instance';self.remote=self.root/'upstream.git'
        def command(*args):subprocess.run(['git',*args],check=True,capture_output=True)
        command('init','--bare','--initial-branch=main',str(self.remote))
        command('clone',str(self.remote),str(self.instance))
        command('-C',str(self.instance),'config','user.name','Fixture')
        command('-C',str(self.instance),'config','user.email','fixture@example.test')
        self.value={'controllers':{'active':'old','standby':'new'},'kept':['unchanged']}
        (self.instance/'klokast-instance.json').write_text(json.dumps(self.value,indent=2)+'\n')
        command('-C',str(self.instance),'add','klokast-instance.json')
        command('-C',str(self.instance),'commit','-m','Initial fixture')
        command('-C',str(self.instance),'push','-u','origin','main')
        p=patch.object(switch,'INSTANCE',self.instance);p.start();self.addCleanup(p.stop)
        run=subprocess.run
        def checked(argv,**kwargs):
            if argv[0]=='/usr/local/bin/klokast':return subprocess.CompletedProcess(argv,0)
            return run(argv,**kwargs)
        p=patch.object(switch.subprocess,'run',side_effect=checked);p.start();self.addCleanup(p.stop)
        self.record={'operation':'a'*24,'old':'old','new':'new','instance':self.value,
                     'original_commit':switch.git('rev-parse','HEAD'),'phase':'publishing'}
        self.path=self.root/'record.json'

    def test_lost_push_reply_is_idempotent_and_preserves_other_fields(self):
        original=switch.git
        def failed(*args):
            result=original(*args)
            if args[0]=='push':raise RuntimeError('lost push reply')
            return result
        with patch.object(switch,'git',side_effect=failed),self.assertRaisesRegex(RuntimeError,'lost push'):
            switch.publish(self.record,self.path)
        revision=original('rev-parse','HEAD')
        switch.publish(self.record,self.path)
        self.assertEqual(original('rev-parse','HEAD'),revision)
        self.assertEqual(original('rev-parse','@{upstream}'),revision)
        expected=dict(self.value,controllers={'active':'new','standby':'old'})
        self.assertEqual(json.loads((self.instance/'klokast-instance.json').read_text()),expected)

    def test_unrelated_edits_are_preserved(self):
        p=self.instance/'klokast-instance.json';changed=dict(self.value,kept=['user edit']);p.write_text(json.dumps(changed))
        with self.assertRaisesRegex(RuntimeError,'Instance changed'):
            switch.publish(self.record,self.path)
        self.assertEqual(json.loads(p.read_text()),changed)



if __name__=='__main__':unittest.main()
