import contextlib
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import air_maintenance_cycle as cycle


class MaintenanceCycle(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'record.json'
        self.record={'phase':'started'}
        lock=patch.object(cycle.runtime,'vm_update_installation_lock',contextlib.nullcontext)
        lock.start();self.addCleanup(lock.stop)

    def test_lost_replacement_reply_retains_exact_resume_id(self):
        before={'pending':[],'installed':None}
        def interrupted(box,role,**kwargs):
            kwargs['operation_started']('a'*24,Path('/private/frozen'),before)
            raise RuntimeError('connection ended after allocation')
        with patch.object(cycle.service,'step',return_value=before),patch.object(cycle.updates,'execute',side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError,'connection ended'):
                cycle.update('boxa','air','b'*24,self.path,self.record)
        record=json.loads(self.path.read_text())
        self.assertEqual(record['vm_operation'],'a'*24)
        pending={'pending':[{'operation_id':'a'*24}],'operation':{'operation_id':'a'*24},'installed':None}
        result={'state':'complete','image':'image','operation_id':'a'*24}
        with patch.object(cycle.service,'step',return_value=pending),patch.object(cycle.updates,'execute',return_value=result) as execute:
            cycle.update('boxa','air','b'*24,self.path,record)
            self.assertEqual(execute.call_args.kwargs['resume'],'a'*24)
            self.assertFalse(execute.call_args.kwargs['migrate_root_data'])
        self.assertEqual(json.loads(self.path.read_text())['phase'],'complete')

    def test_service_roles_update_without_a_runner_or_implicit_migration(self):
        view={'instance':{'airunners':['boxb-air']}}
        absent={'pending':[],'installed':None}
        with tempfile.TemporaryDirectory() as d,patch.object(cycle,'ROOT',Path(d)), \
                patch.object(cycle.source,'require_controller'),patch.object(cycle.source,'snapshot',return_value=view), \
                patch.object(cycle.service,'step',return_value=absent), \
                patch.object(cycle.updates,'execute',return_value={'state':'complete','image':'image'}) as execute:
            cycle.step('update','boxa','b'*24,'dmz')
            self.assertEqual(execute.call_args.args,('boxa','dmz'))
            self.assertFalse(execute.call_args.kwargs['migrate_root_data'])
            self.assertFalse(execute.call_args.kwargs['maintenance'])
            for action,role in (('update','air'),('reserve',None)):
                with self.assertRaisesRegex(RuntimeError,'declared native runner'):cycle.step(action,'boxa','c'*24,role)
            with self.assertRaisesRegex(RuntimeError,'common VM roles'):cycle.step('update','boxa','d'*24,'print')

    def test_unrelated_pending_operation_is_not_adopted(self):
        with patch.object(cycle.service,'step',return_value={'pending':[{'operation_id':'foreign'}]}), \
                patch.object(cycle.updates,'execute') as execute:
            with self.assertRaisesRegex(RuntimeError,'another VM transaction'):
                cycle.update('boxa','air','b'*24,self.path,self.record)
            execute.assert_not_called()

    def test_unallocated_retry_requires_the_same_previous_generation(self):
        self.record.update(vm_operation='a'*24,previous_generation='old')
        observed={'pending':[],'operation':None,'installed':{'operation_id':'new'}}
        with patch.object(cycle.service,'step',return_value=observed),patch.object(cycle.updates,'execute') as execute:
            with self.assertRaisesRegex(RuntimeError,'generation changed'):
                cycle.update('boxa','air','b'*24,self.path,self.record)
            execute.assert_not_called()

    def test_recovered_pre_boot_attempt_is_abandoned_and_needs_a_new_attempt(self):
        # The pre-boot deadline passed; dom0 recovery restored the old generation.
        self.record.update(vm_operation='a'*24,previous_generation='old',inputs='/private/frozen')
        observed={'pending':[{'operation_id':'a'*24,'stage':'prepared'}],'operation':None,
                  'assignment':{'operation_id':'a'*24,'stage':'recovered'},'installed':{'operation_id':'old'}}
        with patch.object(cycle.service,'step',return_value=observed), \
                patch.object(cycle.updates,'execute',return_value={'state':'abandoned'}) as execute:
            result=cycle.update('boxa','air','b'*24,self.path,self.record)
        execute.assert_called_once_with('boxa','air',resume='a'*24,lock_held=True,abandon=True)
        self.assertEqual((result['state'],result['operation_id']),('abandoned','a'*24))
        record=json.loads(self.path.read_text())
        self.assertNotIn('vm_operation',record);self.assertEqual(record['abandoned'],['a'*24])
        # The next attempt starts a new VM operation with the runner reservation.
        after={'pending':[],'operation':None,'installed':{'operation_id':'old'}}
        with patch.object(cycle.service,'step',return_value=after), \
                patch.object(cycle.air_maintenance,'remote',return_value={'reservation':None}), \
                patch.object(cycle.vm_service_execution,'release_runner') as release, \
                patch.object(cycle.updates,'execute',return_value={'state':'complete','image':'new'}) as execute:
            cycle.update('boxa','air','b'*24,self.path,record)
        self.assertIsNone(execute.call_args.kwargs['resume']);self.assertTrue(execute.call_args.kwargs['maintenance'])
        release.assert_not_called()

    def test_reservation_of_an_abandoned_attempt_is_released_before_the_next_attempt(self):
        # The abandoned attempt began runner maintenance, then dom0 kept the old runner.
        self.record.update(abandoned=['a'*24])
        held={'reservation':{'operation':'a'*24,'phase':'pending','image':True}}
        order=[]
        with patch.object(cycle.service,'step',return_value={'pending':[],'operation':None,'installed':{'operation_id':'old'}}), \
                patch.object(cycle.air_maintenance,'remote',return_value=held), \
                patch.object(cycle.vm_service_execution,'release_runner',side_effect=lambda *a:order.append(('release',*a))), \
                patch.object(cycle.updates,'execute',side_effect=lambda *a,**k:order.append('execute') or {'state':'complete'}):
            cycle.update('boxa','air','b'*24,self.path,self.record)
        self.assertEqual(order,[('release','boxa','a'*24),'execute'])

    def test_reservation_of_another_operation_is_kept(self):
        self.record.update(abandoned=['a'*24])
        held={'reservation':{'operation':'c'*24,'phase':'pending'}}
        with patch.object(cycle.service,'step',return_value={'pending':[],'operation':None,'installed':{'operation_id':'old'}}), \
                patch.object(cycle.air_maintenance,'remote',return_value=held), \
                patch.object(cycle.vm_service_execution,'release_runner') as release, \
                patch.object(cycle.updates,'execute',return_value={'state':'complete'}):
            cycle.update('boxa','air','b'*24,self.path,self.record)
        release.assert_not_called()

    def test_attempt_abandoned_by_an_operator_needs_a_new_attempt(self):
        self.record.update(vm_operation='a'*24,previous_generation='old')
        observed={'pending':[],'operation':{'operation_id':'a'*24,'stage':'abandoned'},
                  'assignment':{'operation_id':'a'*24,'stage':'recovered'},'installed':{'operation_id':'old'}}
        with patch.object(cycle.service,'step',return_value=observed),patch.object(cycle.updates,'execute') as execute:
            self.assertEqual(cycle.update('boxa','air','b'*24,self.path,self.record)['state'],'abandoned')
        execute.assert_not_called();self.assertNotIn('vm_operation',json.loads(self.path.read_text()))

    def test_recovered_assignment_of_another_operation_is_not_abandoned(self):
        self.record.update(vm_operation='a'*24,previous_generation='old')
        observed={'pending':[{'operation_id':'a'*24}],'operation':{'operation_id':'a'*24},
                  'assignment':{'operation_id':'c'*24,'stage':'recovered'},'installed':None}
        with patch.object(cycle.service,'step',return_value=observed), \
                patch.object(cycle.updates,'execute',return_value={'state':'complete','image':'x'}) as execute:
            cycle.update('boxa','air','b'*24,self.path,self.record)
        self.assertEqual(execute.call_args.kwargs['resume'],'a'*24);self.assertNotIn('abandon',execute.call_args.kwargs)

    def test_complete_receipt_rechecks_health_without_new_replacement(self):
        result={'state':'complete','image':'same'}
        self.record.update(phase='complete',result=result,vm_operation='a'*24)
        installed={'operation_id':'a'*24,'image':'same','requested_configuration':{'before':{'identity':'kept'}}}
        with patch.object(cycle.service,'step',return_value={'pending':[],'installed':installed}), \
                patch.object(cycle.updates,'execute') as execute, \
                patch.object(cycle.vm_service_probe,'probe',return_value={'identity':'kept'}), \
                patch.object(cycle.vm_service_probe,'verify') as verify:
            self.assertEqual(cycle.update('boxa','air','b'*24,self.path,self.record),result)
            execute.assert_not_called();verify.assert_called_once()


if __name__=='__main__':unittest.main()
