"""The copy capsule binds both directions to one exact router transaction."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_copy_inputs
import router_copy_contract
import router_generations
from test_router_generations import generation
from test_router_transaction import request
from test_router_copy_qualification import module


class CopyInputsTests(unittest.TestCase):
    def setUp(self):
        self.old, self.new = generation('legacy'), generation()
        self.request = {**request(), 'old_sha256': self.old['record_sha256'],
                        'candidate_sha256': self.new['record_sha256'], 'engine_commit': self.new['engine_commit']}
        self.job = router_copy_inputs.job(self.request, self.old, self.new, 'f' * 64)
        self.guest = module('router-copy-transaction-guest')

    def command(self, value, phase):
        return ('console=hvc0 klokast_operation=' + self.request['operation_id'] +
                ' klokast_inputs=' + 'f' * 64 + ' klokast_job=' + router_generations.digest(value) +
                ' klokast_phase=' + phase)

    def test_reverse_uses_the_same_generation_ids_and_account_translation(self):
        for phase, source, target in (('forward',self.old,self.new),('reverse',self.new,self.old)):
            actual, selected = self.guest.select(self.job, self.command(self.job, phase))
            self.assertEqual(actual, phase)
            self.assertEqual(selected['source_id'], source['disk']['uuid'])
            self.assertEqual(selected['destination_id'], target['disk']['uuid'])
            self.assertEqual(selected['source_accounts'], source['accounts'])
            self.assertEqual(selected['destination_accounts'], target['accounts'])
            self.assertEqual(selected['request_sha256'], router_generations.digest(
                {k:v for k,v in selected.items() if k!='request_sha256'}))
            self.assertLessEqual(selected['seconds'],self.request['recovery_seconds']-30)

    def test_wrong_operation_phase_or_capsule_fails_before_guest_devices(self):
        valid=self.command(self.job,'forward')
        for wrong in (valid.replace('klokast_phase=forward','klokast_phase=other'),
                      valid.replace(self.request['operation_id'],'0'*24),
                      valid+' klokast_phase=reverse', valid.replace('klokast_job=', 'unknown=')):
            with self.assertRaises(RuntimeError):
                self.guest.select(self.job,wrong)
        changed=copy.deepcopy(self.job)
        changed['forward']['source_accounts']['dnsmasq_uid']+=1
        with self.assertRaises(RuntimeError):
            self.guest.select(changed,valid)

    def test_mixed_pair_cannot_pass_even_with_a_recomputed_job_hash(self):
        for field, wrong in (('source_id','other-uuid'), ('source_accounts',{'dnsmasq_uid':1}),
                              ('role','dmz'), ('box','other')):
            changed=copy.deepcopy(self.job)
            changed['reverse'][field]=wrong
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                self.guest.select(changed,self.command(changed,'reverse'))

    def test_capsule_cannot_share_production_uuid_or_change_boot_inputs(self):
        value = {'kind':'klokast.router-copy-capsule.v1','operation_id':self.request['operation_id'],
            'engine_commit':self.request['engine_commit'],'transaction_sha256':router_generations.digest(self.request),
            'inputs_sha256':'f'*64,'job_sha256':router_generations.digest(self.job),
            'bootstrap':{name:{'bytes':1024,'sha256':'e'*64} for name in ('kernel','initramfs')},
            'domains':{'forward':'33333333-1111-4111-8111-111111111111',
                       'reverse':'44444444-1111-4111-8111-111111111111'}}
        router_copy_contract.capsule(value,self.request,self.old,self.new)
        for mutate in (lambda v:v['domains'].update(forward=self.old['xen']['uuid']),
                       lambda v:v['bootstrap']['kernel'].update(bytes=2**30),
                       lambda v:v.update(transaction_sha256='0'*64),
                       lambda v:v.update(inputs_sha256='0'*64)):
            changed=copy.deepcopy(value); mutate(changed)
            with self.assertRaises(ValueError):
                router_copy_contract.capsule(changed,self.request,self.old,self.new)


if __name__ == '__main__':
    unittest.main()
