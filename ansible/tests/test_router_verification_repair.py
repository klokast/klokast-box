"""A verifier repair cannot select new policy, a different operation, or pre-boot state."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import router_verification_repair as repair


class Eligibility(unittest.TestCase):
    def setUp(self):
        self.op='a'*24
        self.observed={'pending':[{'box':'boxa','role':'router','operation_id':self.op,
            'reboot_verification':{'status':'complete'},'requested_configuration':{'instance_commit':'b'*40}}],
            'assignment':{'operation_id':self.op,'stage':'booted'}}

    def test_exact_booted_operation_is_selected_without_changes(self):
        before=copy.deepcopy(self.observed)
        self.assertEqual(repair.eligible(self.observed,'boxa',self.op,'b'*40),self.observed['pending'][0])
        self.assertEqual(self.observed,before)

    def test_changed_policy_target_pending_set_or_boot_evidence_refuses(self):
        changes=[lambda d:d['pending'].clear(),lambda d:d['pending'].append(copy.deepcopy(d['pending'][0])),
            lambda d:d['pending'][0].update(role='bak'),lambda d:d['pending'][0].update(box='boxb'),
            lambda d:d['assignment'].update(operation_id='c'*24),lambda d:d['assignment'].update(stage='armed'),
            lambda d:d['pending'][0]['reboot_verification'].update(status='pending'),
            lambda d:d['pending'][0]['requested_configuration'].update(instance_commit='c'*40)]
        for change in changes:
            observed=copy.deepcopy(self.observed);change(observed)
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                repair.eligible(observed,'boxa',self.op,'b'*40)

    def test_production_gate_runs_before_operation_lock_or_native_steps(self):
        import platform_source
        import platform_resource_runtime
        with patch.object(platform_source,'require_controller',side_effect=RuntimeError('production refused')), \
                patch.object(platform_resource_runtime,'vm_update_installation_lock') as lock:
            with self.assertRaisesRegex(RuntimeError,'production refused'):
                repair.repair('boxa',self.op,Path('/unused'))
            lock.assert_not_called()
