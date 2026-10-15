"""Controller baseline admission requires current policy and stable native evidence."""
import copy
import hashlib
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_router_baseline as fixtures
import vm_router_baseline as controller


class ControllerBaseline(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.BaselinePublication();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.value=fixture.value
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        replacements=((controller,'ROOT',Path(temporary.name)),
            (controller.platform_source,'require_controller',lambda:None),
            (controller.runtime,'require_active_controller',lambda:None),
            (controller.runtime,'vm_update_installation_lock',nullcontext),
            (controller.service,'authority',lambda *a:'running'),
            (controller.inputs,'revision',lambda p,*a:'d'*40 if p==controller.inputs.INSTANCE else 'c'*40),
            (controller,'guest_probe',lambda box:copy.deepcopy(self.value['guest'])),
            (controller,'verify_policy',lambda *a:None))
        for owner,name,value in replacements:
            p=patch.object(owner,name,value);p.start();self.addCleanup(p.stop)

    def step(self,baseline=None):
        return patch.object(controller.service,'step',side_effect=[
            {'baseline':baseline,'observation':self.value['observation']},{'state':'complete'}])

    def test_reconciliation_uses_existing_transport_and_exact_recorded_publication(self):
        with self.step(self.value) as step:
            self.assertEqual(controller.execute('boxa')['state'],'complete')
            self.assertEqual([c.args for c in step.call_args_list],[('boxa','router','baseline')]*2)
            self.assertEqual(step.call_args_list[1].kwargs['payload']['publication'],self.value)

    def test_failed_policy_or_changed_identity_cannot_publish(self):
        with self.step() as step,patch.object(controller,'verify_policy',side_effect=RuntimeError('policy differs')):
            with self.assertRaisesRegex(RuntimeError,'policy differs'):controller.execute('boxa')
            self.assertEqual(step.call_count,1)
        with self.step() as step,patch.object(controller,'guest_probe',side_effect=[self.value['guest'],{}]):
            with self.assertRaisesRegex(RuntimeError,'changed'):controller.execute('boxa')
            self.assertEqual(step.call_count,1)

    def test_resume_does_not_replace_old_identity_evidence(self):
        changed=copy.deepcopy(self.value['guest']);changed['identity']['machine_id']='invented'
        with self.step(self.value) as step,patch.object(controller,'guest_probe',return_value=changed):
            with self.assertRaisesRegex(RuntimeError,'interrupted'):controller.execute('boxa')
            self.assertEqual(step.call_count,1)

    def test_instance_change_cannot_publish_after_successful_verification(self):
        with self.step() as step,patch.object(controller.inputs,'revision',side_effect=['c'*40,'d'*40,'c'*40,'e'*40]):
            with self.assertRaisesRegex(RuntimeError,'Instance changed'):controller.execute('boxa')
            self.assertEqual(step.call_count,1)


class BaselinePolicy(unittest.TestCase):
    def test_only_native_verified_inert_extra_includes_are_permitted(self):
        required='etc/klokast/app-resources/router-forward.d/current.nft'
        retired='etc/klokast/app-resources/router-forward.d/retired.nft'
        content='accept comment "approved"\n'
        guest={'configuration_files':{required:hashlib.sha256(content.encode()).hexdigest(),retired:'a'*64},
               'inactive_includes':[retired]}
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(controller.subprocess,'check_output',return_value='{}'), \
                patch.object(controller.router_updates,'rendered_includes',return_value={required:content}), \
                patch.object(controller.router.resource_compiler,'box_access_router_vars',return_value={}), \
                patch.object(controller.subprocess,'run') as native:
            controller.verify_policy('boxa',guest,Path(temporary))
            native.assert_called_once();native.reset_mock()
            guest['inactive_includes']=[]
            with self.assertRaisesRegex(RuntimeError,'firewall includes differ'):
                controller.verify_policy('boxa',guest,Path(temporary))
            guest['inactive_includes']=[retired,required]
            guest['configuration_files'][required]='b'*64
            with self.assertRaisesRegex(RuntimeError,'firewall includes differ'):
                controller.verify_policy('boxa',guest,Path(temporary))
            native.assert_not_called()


if __name__=='__main__':unittest.main()
