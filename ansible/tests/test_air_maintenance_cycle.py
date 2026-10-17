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
