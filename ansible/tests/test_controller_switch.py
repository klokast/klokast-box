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
        return {'operation':'a'*24,'old':'old','new':'new','phase':'prepared'}

    def test_resume_after_every_transition_never_has_two_active_controllers(self):
        for interruption in range(1,5):
            with self.subTest(interruption=interruption), tempfile.TemporaryDirectory() as temporary:
                record=self.setup_case(); path=Path(temporary)/'state.json'
                switch.write(path,record)
                original_write=switch.write; calls=[]
                def fail_write(path,value):
                    calls.append(value['phase'])
                    if len(calls)==interruption:raise RuntimeError('interrupted before checkpoint')
                    original_write(path,value)
                with patch.object(switch,'credential_identity',side_effect=lambda c,b,h:{'provider':b}), \
                        patch.object(switch,'write',side_effect=fail_write):
                    try: switch.transition({},record,path,self.ha)
                    except RuntimeError as error:self.assertIn('interrupted',str(error))
                record=json.loads(path.read_text())
                with patch.object(switch,'credential_identity',side_effect=lambda c,b,h:{'provider':b}):
                    result=switch.transition({},record,path,self.ha)
                self.assertEqual(result['phase'],'complete')
                self.assertEqual(self.events,[('old','standby'),('new','active')])

    def test_unavailable_or_conflicting_peer_does_not_mutate(self):
        for change in ({'reachable':False},{'active':True,'role':'active'}):
            record=self.setup_case();self.states['new'].update(change)
            with self.assertRaises(RuntimeError):switch.transition({},record,Path('/unused'),self.ha)
            self.assertEqual(self.events,[])

    def test_missing_or_shared_credentials_stop_before_any_marker_change(self):
        record=self.setup_case()
        self.ha.verify_credentials.side_effect=RuntimeError('credentials absent')
        with self.assertRaisesRegex(RuntimeError,'credentials absent'):switch.transition({},record,Path('/unused'),self.ha)
        record=self.setup_case()
        with patch.object(switch,'credential_identity',return_value={'provider':'shared'}):
            with self.assertRaisesRegex(RuntimeError,'independent'):switch.transition({},record,Path('/unused'),self.ha)
        self.assertEqual(self.events,[])
        self.assertEqual(record['phase'],'prepared')

    def test_readiness_checks_each_controller_against_its_peer(self):
        record=self.setup_case()
        with patch.object(switch,'credential_identity',side_effect=lambda c,b,h:{'provider':b}), \
                patch.object(switch,'write'):
            switch.transition({},record,Path('/unused'),self.ha)
        self.assertEqual([c.args[1:] for c in self.ha.require_readiness.call_args_list],[('old','new'),('new','old')])

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


if __name__=='__main__':unittest.main()
