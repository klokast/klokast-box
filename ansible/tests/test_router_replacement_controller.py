"""The controller can mint only for dom0's one waiting B attempt."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'lib'))
import router_replacement_controller as controller
import router_generations as generations
from router_transaction import TransactionError


class ControllerSignalTests(unittest.TestCase):
    def setUp(self):
        self.request={'box':'boxa','operation_id':'a'*24,'engine_commit':'e'*40,
            'old_sha256':'1'*64}
        self.prepared={'first_contact':{'host_key_public_sha256':{'ed25519':'2'*64}}}
        self.source={'kind':'klokast.router-replacement-enrollment-source.v1',
            'request_sha256':generations.digest(self.request),'old_sha256':'1'*64,
            'old_machine_id':'nOldRouter','preparation_sha256':generations.digest(self.prepared)}
        self.attempt={'old_machine_id':'nOldRouter',
            'host_key_public_sha256':{'ed25519':'2'*64},
            'record_sha256':'3'*64,'nonce':'4'*24}
        self.pending={'phase':'awaiting-enrollment','candidate_started':True,
            'old_started':False}
        self.preparation_request={'operation_id':'a'*24,'engine_commit':'e'*40}
        for module,name in ((controller.transaction,'validate'),
                            (controller.transaction,'validate_pending'),
                            (controller.enrollment,'validate_attempt'),
                            (controller.preparation,'validate_result')):
            patch=mock.patch.object(module,name)
            patch.start();self.addCleanup(patch.stop)

    def check(self):
        return controller.signal_source(self.request,self.pending,self.source,self.attempt,
            self.prepared,self.preparation_request,{})

    def test_exact_waiting_attempt_binds_one_mint_marker(self):
        self.assertEqual(self.check(),self.source)
        self.assertEqual(controller.mint_marker(self.request,self.attempt),{
            'kind':'klokast.router-replacement-mint-attempt.v1',
            'request_sha256':generations.digest(self.request),
            'attempt_sha256':'3'*64,'nonce':'4'*24})

    def test_old_device_pin_or_phase_change_refuses(self):
        for change in ('old_device','host_key','phase','old_started'):
            with self.subTest(change=change):
                saved=copy.deepcopy((self.source,self.attempt,self.pending))
                if change=='old_device':
                    self.attempt['old_machine_id']='nOtherRouter'
                elif change=='host_key':
                    self.attempt['host_key_public_sha256']['ed25519']='5'*64
                elif change=='phase':
                    self.pending['phase']='checking-final-candidate'
                else:
                    self.pending['old_started']=True
                with self.assertRaises(TransactionError):
                    self.check()
                self.source,self.attempt,self.pending=saved


if __name__=='__main__':
    unittest.main()
