"""The networkless B finalizer accepts only the recorded generation job."""
import copy
import hashlib
from pathlib import Path
import runpy
import unittest

import test_router_candidate as candidate_fixture


SCRIPT = Path(__file__).resolve().parents[1] / 'roles/router-replacement-finalization/files/router-replacement-finalize-guest'


class ReplacementFinalizerGuestTests(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))
        fixture = candidate_fixture.CandidateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.job = copy.deepcopy(fixture.job)
        self.job['first_contact'] = {'key':'ssh-ed25519 YQ==',
            'backend_address':'192.0.2.2','backend_prefix':24,
            'backend_source_address':'192.0.2.1'}
        public = 'ssh-ed25519 YQ==\n'
        self.value = {'kind':'klokast.router-replacement-finalization-job.v1',
            'box':'boxa','operation_id':self.job['operation_id'],
            'inputs_sha256':self.job['inputs_sha256'],
            'job_sha256':self.module['router_personalize'].digest(self.job),
            'preparation_job':self.job,'manifest':fixture.manifest,
            'prepared':{'mode':'replacement','packages':self.job['personalization']['packages'],
                        'accounts':{'dnsmasq_uid':65,'dnsmasq_gid':65,'tailscale_gid':103}},
            'first_contact':{'kind':'klokast.router-first-contact.v2',
                'host_key_public':{'ed25519':public},
                'host_key_public_sha256':{'ed25519':hashlib.sha256(public.encode()).hexdigest()}},
            'runtime_packages':self.job['runtime_packages'],
            'enrolled_guest':{'box':'boxa','operation_id':self.job['operation_id'],
                'machine_id':'nNewRouter','hostname':'boxa-router-'+self.job['operation_id'],
                'tags':['tag:vm'],'ssh':True,'state_sha256':'a'*64,
                'host_key_public_sha256':{'ed25519':hashlib.sha256(public.encode()).hexdigest()}}}
        self.args = {'klokast_operation':self.job['operation_id'],
            'klokast_inputs':self.job['inputs_sha256'],
            'klokast_job':self.module['router_personalize'].digest(self.value)}

    def test_exact_job_is_accepted(self):
        self.assertEqual(self.module['validate_job'](self.value,self.args),self.value)

    def test_identity_or_runtime_substitution_is_refused(self):
        changes = (
            lambda v:v['enrolled_guest'].update(hostname='boxa-router'),
            lambda v:v['enrolled_guest'].update(tags=['tag:ops']),
            lambda v:v['enrolled_guest'].update(ssh=False),
            lambda v:v['enrolled_guest'].update(state_sha256='bad'),
            lambda v:v['enrolled_guest']['host_key_public_sha256'].update(ed25519='b'*64),
            lambda v:v['prepared']['packages'].update(openssh='other'),
            lambda v:v['runtime_packages'].update(openssh='1-r0'),
        )
        for change in changes:
            value = copy.deepcopy(self.value)
            change(value)
            args = {**self.args,'klokast_job':self.module['router_personalize'].digest(value)}
            with self.subTest(change=change),self.assertRaises((RuntimeError,ValueError)):
                self.module['validate_job'](value,args)


if __name__ == '__main__':
    unittest.main()
