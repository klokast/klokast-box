"""Full-service proof selects the exact finalized B device and release."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import router_generations as generations
import router_replacement_service as service
from router_transaction import TransactionError


class ReplacementServiceTests(unittest.TestCase):
    def setUp(self):
        self.request={'box':'boxa','operation_id':'a'*24,'candidate_sha256':'1'*64}
        self.component={key:'2'*64 for key in ('sha256','tailscale_sha256',
            'tailscaled_sha256','openrc_sha256')}
        self.component['version']='1.2.3'
        self.candidate={'record_sha256':'1'*64,'generation_id':'a'*24,
            'packages':{'dnsmasq':'2-r0'},'kernel_release':'6.12.1-virt',
            'configuration_files':{'etc/hostname':'3'*64},'tailscale':self.component}
        self.release={'runtime_packages':self.candidate['packages'],
            'inputs':{'tailscale':self.component}}
        self.enrolled={'machine_id':'nNewRouter','hostname':'boxa-router-'+'a'*24,
            'addresses':['100.64.0.8']}
        self.finalized={'machine_id':'nNewRouter','state':{
            'var/lib/dhcpcd/duid':{'sha256':'4'*64},
            'var/lib/dhcpcd/secret':{'sha256':'5'*64},
            'etc/ssh/ssh_host_ed25519_key':{'sha256':'6'*64}}}
        for module,name in ((service.transaction,'validate'),
                            (service.generations,'generation'),
                            (service.finalization,'result')):
            patch=mock.patch.object(module,name)
            patch.start();self.addCleanup(patch.stop)

    def test_exact_expected_and_all_service_checks(self):
        anticipated=service.expected(self.request,self.candidate,self.enrolled,
            self.finalized,self.release,{})
        self.assertEqual(anticipated['machine_id'],'nNewRouter')
        self.assertEqual(anticipated['identity_files']['var/lib/dhcpcd/duid'],'4'*64)
        proof={'kind':'klokast.router-replacement-service-proof.v1',
            'box':'boxa','operation_id':'a'*24,
            'expected_sha256':anticipated['record_sha256'],
            'candidate_sha256':'1'*64,'machine_id':'nNewRouter',
            'hostname':'boxa-router-'+'a'*24,
            'tests':dict.fromkeys(service.TESTS,True)}
        self.assertEqual(service.proof(proof,anticipated),proof)
        with self.assertRaises(TransactionError):
            service.proof({**proof,'tests':{**proof['tests'],'management':False}},anticipated)
        with self.assertRaises(TransactionError):
            service.proof({**proof,'machine_id':'nOldRouter'},anticipated)

    def test_missing_identity_or_changed_release_refuses(self):
        self.finalized['state'].pop('var/lib/dhcpcd/duid')
        with self.assertRaises(TransactionError):
            service.expected(self.request,self.candidate,self.enrolled,
                self.finalized,self.release,{})
        self.finalized['state']['var/lib/dhcpcd/duid']={'sha256':'4'*64}
        self.release['runtime_packages']={'dnsmasq':'3-r0'}
        with self.assertRaises(TransactionError):
            service.expected(self.request,self.candidate,self.enrolled,
                self.finalized,self.release,{})


if __name__=='__main__':
    unittest.main()
