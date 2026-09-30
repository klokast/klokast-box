"""Check that B cleanup cannot use another enrollment or package release."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'lib'))
import router_replacement_finalization as finalization
import router_generations as generations
from router_transaction import TransactionError


class FinalizationBindingTests(unittest.TestCase):
    def setUp(self):
        self.preparation_job = {'operation_id':'a'*24,'engine_commit':'e'*40,
            'inputs_sha256':'1'*64,'runtime_packages':{'dnsmasq':'2-r0'}}
        self.preparation_request = {'box':'boxa','operation_id':'a'*24,
            'engine_commit':'e'*40,'inputs_sha256':'1'*64,
            'job_sha256':generations.digest(self.preparation_job)}
        self.prepared = {'prepared':{'accounts':{'dnsmasq_uid':65},
            'tailscale':{'sha256':'2'*64}},
            'first_contact':{'host_key_public_sha256':{'ed25519':'3'*64}}}
        self.release = {'receipt_sha256':'4'*64,'inputs':{'inputs_sha256':'1'*64},
            'runtime_packages':{'dnsmasq':'2-r0'},'runtime_tests':{'pinned_world':True}}
        self.candidate = {'record_sha256':'5'*64,'accounts':copy.deepcopy(self.prepared['prepared']['accounts']),
            'tailscale':copy.deepcopy(self.prepared['prepared']['tailscale']),
            'release_sha256':'4'*64,'packages':{'dnsmasq':'2-r0'}}
        self.request = {'box':'boxa','operation_id':'a'*24,'engine_commit':'e'*40,
            'candidate_sha256':'5'*64}
        self.attempt = {'record_sha256':'6'*64}
        self.enrolled = {'machine_id':'nNewRouter','state_sha256':'7'*64,
            'host_key_public_sha256':{'ed25519':'3'*64}}
        for module,method in ((finalization.transaction,'validate'),
                              (finalization.generations,'generation'),
                              (finalization.enrollment,'validate_attempt'),
                              (finalization.enrollment,'result'),
                              (finalization.router_candidate,'validate'),
                              (finalization.preparation,'validate_result')):
            patch = mock.patch.object(module,method)
            patch.start()
            self.addCleanup(patch.stop)

    def job(self):
        return finalization.job_for(self.request,self.candidate,self.preparation_request,
            self.preparation_job,self.prepared,self.release,self.attempt,self.enrolled)

    def test_exact_enrollment_and_release_bind_to_cleanup(self):
        job = self.job()
        self.assertEqual(job['enrolled_guest'],self.enrolled)
        self.assertEqual(job['runtime_packages'],self.candidate['packages'])

    def test_changed_generation_key_or_runtime_refuses(self):
        for field in ('candidate_key','prepared_key','package','receipt'):
            with self.subTest(field=field):
                saved = copy.deepcopy((self.candidate,self.prepared,self.release))
                if field == 'candidate_key':
                    self.candidate['accounts']['dnsmasq_uid'] = 66
                elif field == 'prepared_key':
                    self.prepared['first_contact']['host_key_public_sha256']['ed25519'] = '8'*64
                elif field == 'package':
                    self.release['runtime_packages']['dnsmasq'] = '3-r0'
                else:
                    self.release['receipt_sha256'] = '9'*64
                with self.assertRaises(TransactionError):
                    self.job()
                self.candidate,self.prepared,self.release = saved

    def test_result_requires_enrolled_state_and_final_runtime(self):
        job = self.job()
        state = {'var/lib/tailscale/tailscaled.state':{'sha256':'7'*64}}
        value = {'kind':'klokast.router-replacement-finalization-result.v1',
            'operation_id':job['operation_id'],'inputs_sha256':job['inputs_sha256'],
            'job_sha256':generations.digest(job),'success':True,
            'machine_id':'nNewRouter','state':state,'state_sha256':generations.digest(state),
            'finalized':{'packages':self.release['runtime_packages'],
                         'tests':self.release['runtime_tests'],
                         'enrolled_state_preserved':True}}
        self.assertEqual(finalization.result(value,job,self.release,self.enrolled),value)
        for change in ({'machine_id':'nOldRouter'}, {'state_sha256':'0'*64},
                       {'finalized':{'packages':{},'tests':{},'enrolled_state_preserved':True}}):
            with self.subTest(change=change),self.assertRaises(TransactionError):
                finalization.result({**value,**change},job,self.release,self.enrolled)


class FinalizationRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.work/'finalization').mkdir()
        self.waited = []
        self.adapter = SimpleNamespace(work=self.work,
            request={'operation_id':'a'*24},
            pair={'candidate':{'disk':{'path':'/dev/vg0/routergen_'+'a'*24}}},
            host=SimpleNamespace(wait_detached=lambda paths,**kw:self.waited.append(paths)))

    def test_cleanup_config_has_no_network_and_a_read_only_job_slot(self):
        with mock.patch.object(finalization.uuid,'uuid4',return_value='irrelevant'):
            name,path = finalization.configuration(self.work/'finalization',
                {'operation_id':'a'*24,'inputs_sha256':'1'*64},
                self.adapter.pair['candidate']['disk'],'/dev/loop1','/dev/loop2',
                '00000000-0000-0000-0000-000000000001')
        self.assertEqual(name,'router-replacement-finalize-'+'a'*24)
        text = path.read_text()
        self.assertIn("'phy:/dev/loop2,xvdc,r'",text)
        self.assertIn('vif = []',text)

    def test_recovery_fences_only_recorded_cleanup_loops(self):
        final = self.work/'finalization'
        (final/'run.json').write_text('{}')
        (final/'finalize.cfg').write_text('config')
        ledger = {'kind':'klokast.router-replacement-finalizer-run.v1',
            'operation_id':'a'*24,
            'request_sha256':generations.digest(self.adapter.request),
            'job_sha256':generations.digest({'job':True}),
            'disk':self.adapter.pair['candidate']['disk'],
            'uuid':'00000000-0000-0000-0000-000000000001',
            'config_sha256':'c'*64,'result_loop':'/dev/loop1','job_loop':'/dev/loop2'}
        def read(path):
            return ledger if path.name == 'run.json' else {'job':True}
        def loops(path):
            return ['/dev/loop2'] if path.name == 'job.slot' else ['/dev/loop1']
        with mock.patch.object(finalization,'domain',return_value=None), \
             mock.patch.object(finalization.records,'read',side_effect=read), \
             mock.patch.object(finalization,'safe_file'), \
             mock.patch.object(finalization,'checksum',return_value='c'*64), \
             mock.patch.object(finalization,'loop_devices',side_effect=loops), \
             mock.patch.object(finalization,'detach_loop') as detach:
            finalization.fence(self.adapter,deadline=1000)
        self.assertEqual(self.waited,[['/dev/loop2'],['/dev/loop1']])
        self.assertEqual(detach.call_count,2)

    def test_unrecorded_cleanup_guest_refuses_recovery(self):
        with mock.patch.object(finalization,'domain',return_value={'domid':9}):
            with self.assertRaisesRegex(TransactionError,'unrecorded'):
                finalization.fence(self.adapter,deadline=1000)

    def test_stopped_candidate_uses_one_networkless_guest_and_checks_result(self):
        final = self.work/'finalization'
        self.adapter.request.update(box='boxa',engine_commit='e'*40)
        self.adapter.pair['old'] = {'disk':{'path':'/dev/vg0/lv_router'}}
        self.adapter.host.guest = lambda pair,**kw:None
        self.adapter.host.detached = lambda paths,**kw:self.waited.append(paths)
        self.adapter.host.disk = lambda disk,**kw:None
        preparation_request = {'inputs_sha256':'1'*64}
        release = {'runtime_packages':{'dnsmasq':'2-r0'},
                   'runtime_tests':{'pinned_world':True}}
        enrolled = {'machine_id':'nNewRouter','state_sha256':'7'*64}
        job = {'operation_id':'a'*24,'inputs_sha256':'1'*64}
        state = {'var/lib/tailscale/tailscaled.state':{'sha256':'7'*64}}
        output = {'kind':'klokast.router-replacement-finalization-result.v1',
            'operation_id':'a'*24,'inputs_sha256':'1'*64,
            'job_sha256':generations.digest(job),'success':True,
            'machine_id':'nNewRouter','state':state,'state_sha256':generations.digest(state),
            'finalized':{'packages':release['runtime_packages'],
                         'tests':release['runtime_tests'],'enrolled_state_preserved':True}}
        sources = {'request.json':preparation_request,'candidate-job.json':{},
            'preparation-result.json':{},'release.json':release,
            'enrollment-attempt.json':{},'enrollment-result.json':enrolled}
        def read(path):
            return sources[path.name]
        def write(path,value):
            path.write_text(__import__('json').dumps(value))
        def boot(work,name,identity,config,request,**kw):
            self.assertTrue((final/'run.json').is_file())
            self.assertIn('vif = []',config.read_text())
            self.assertIn("'phy:/dev/loop2,xvdc,r'",config.read_text())
        with mock.patch.object(finalization.records,'read',side_effect=read), \
             mock.patch.object(finalization.records,'write',side_effect=write), \
             mock.patch.object(finalization,'job_for',return_value=job), \
             mock.patch.object(finalization,'assets'), \
             mock.patch.object(finalization,'domain',return_value=None), \
             mock.patch.object(finalization,'attach_loop',side_effect=['/dev/loop1','/dev/loop2']), \
             mock.patch.object(finalization,'boot_guest',side_effect=boot) as booted, \
             mock.patch.object(finalization,'read_slot',return_value=output), \
             mock.patch.object(finalization,'detach_loop') as detached:
            result = finalization.run_candidate(self.adapter,deadline=finalization.time.monotonic()+120)
        self.assertEqual(result,output)
        self.assertEqual(booted.call_count,1)
        self.assertEqual(detached.call_count,2)
        self.assertEqual(set(self.waited[0]),{'/dev/vg0/lv_router',
            '/dev/vg0/routergen_'+'a'*24})


if __name__ == '__main__':
    unittest.main()
