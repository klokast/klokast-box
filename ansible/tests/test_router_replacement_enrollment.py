"""A replacement enrollment cannot reuse A's identity or another attempt."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'lib'))
import router_generations as generations
import router_replacement_enrollment as enrollment
from router_transaction import TransactionError
from test_router_generations import generation, reseal
from test_router_transaction import request


class ReplacementEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.request = request()
        self.candidate = generation()
        self.candidate['generation_id'] = self.request['operation_id']
        self.candidate['disk']['path'] = '/dev/vg0/routergen_' + self.request['operation_id']
        for name in ('kernel','initramfs'):
            self.candidate['boot'][name]['path'] = (
                '/mnt/dom0_data/klokast-router-updates/generations/' +
                self.request['operation_id'] + '/' + name)
        reseal(self.candidate)
        self.request['candidate_sha256'] = self.candidate['record_sha256']
        self.intent = enrollment.attempt(self.request,self.candidate,nonce='c'*24,
            old_machine_id='nOldRouter',host_keys={'ed25519':'a'*64})
        self.result = {'kind':'klokast.router-replacement-enrollment-result.v1',
            'box':'boxa','operation_id':self.request['operation_id'],
            'request_sha256':generations.digest(self.request),
            'attempt_sha256':self.intent['record_sha256'],
            'candidate_sha256':self.request['candidate_sha256'],
            'nonce':self.intent['nonce'],'machine_id':'nNewRouter',
            'hostname':self.intent['hostname'],'tags':['tag:vm'],'ssh':True,
            'state_sha256':'d'*64,'addresses':['100.64.0.8','fd7a:115c:a1e0::8'],
            'host_key_public_sha256':self.intent['host_key_public_sha256']}

    def test_exact_new_device_and_fixed_generation_name(self):
        self.assertEqual(enrollment.validate_attempt(self.intent,self.request),self.intent)
        self.assertEqual(enrollment.result(self.result,self.request,self.intent),self.result)
        self.assertEqual(self.result['hostname'],'boxa-router-' + 'a'*24)

    def test_same_device_and_changed_attempt_are_refused(self):
        for field,wrong in (('machine_id','nOldRouter'),('hostname','boxa-router'),
                            ('nonce','e'*24),('attempt_sha256','f'*64),
                            ('state_sha256','bad'),('tags',['tag:vm','tag:ops']),
                            ('ssh',False),('addresses',['127.0.0.1'])):
            changed = {**self.result,field:wrong}
            with self.subTest(field=field),self.assertRaises(TransactionError):
                enrollment.result(changed,self.request,self.intent)
        changed = copy.deepcopy(self.intent)
        changed['old_machine_id'] = 'nOtherRouter'
        with self.assertRaises(generations.GenerationError):
            enrollment.validate_attempt(changed,self.request)
        with self.assertRaises(TransactionError):
            enrollment.attempt(self.request,self.candidate,nonce='c'*24,
                old_machine_id='nOldRouter',host_keys={'ed25519':'bad'})
        with self.assertRaises(TransactionError):
            enrollment.attempt(self.request,generation('legacy'),nonce='c'*24,
                old_machine_id='nOldRouter',host_keys={'ed25519':'a'*64})

    def test_malformed_addresses_and_host_keys_are_refused(self):
        for addresses in ([],['100.64.0.8','100.64.0.8'],['not-an-ip'],
                          [['100.64.0.8']],['::1']):
            with self.subTest(addresses=addresses),self.assertRaises(TransactionError):
                enrollment.result({**self.result,'addresses':addresses},self.request,self.intent)
        with self.assertRaises(TransactionError):
            enrollment.result({**self.result,'host_key_public_sha256':{'ed25519':'b'*64}},
                              self.request,self.intent)


if __name__ == '__main__':
    unittest.main()
