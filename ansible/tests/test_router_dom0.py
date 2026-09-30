"""Exercise the real adapter and durable records across failures with fake Xen."""
import copy
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_dom0 as d
import router_generations as g
import router_native as n
import router_records as r
from router_transaction import Transaction, TransactionError
import test_router_records as records_tests
from test_router_transaction import PowerLoss


class Host:
    monotonic = staticmethod(time.monotonic)

    def __init__(self):
        self.live = 'old'
        self.events = []

    def guard(self, box, **kw):
        pass

    def disk(self, value, **kw):
        return value['uuid']

    def artifact(self, value, **kw):
        pass

    def guest(self, pair, **kw):
        return None if self.live is None else (self.live, {})

    def detached(self, paths, **kw):
        self.events.append(('detached', paths))

    def stop(self, pair, side, **kw):
        if self.live == side:
            self.live = None
        self.events.append(('stop', side))

    def start(self, pair, side, config, **kw):
        if self.live not in (None, side):
            raise AssertionError('two router identities would start')
        self.live = side
        self.events.append(('start', side))


class Copy:
    def __init__(self):
        self.events = []
        self.failure = None

    def verify(self, adapter, **kw):
        pass

    def fence(self, adapter, **kw):
        self.events.append('fenced')

    def copy(self, adapter, source, target, **kw):
        assert adapter.host.live is None
        self.events.append((source, target))
        if self.failure == source:
            raise TransactionError('synthetic copy failure')

    def verify_copy(self, adapter, source, target, **kw):
        assert (source, target) in self.events


class Dom0Tests(unittest.TestCase):
    def setUp(self):
        records_tests.RecordsTests.setUp(self)
        self.request['operation_id'] = 'f'*24
        (self.base/'operations'/self.request['operation_id']).mkdir(mode=0o700)
        candidate = copy.deepcopy(self.new)
        candidate.pop('record_sha256')
        candidate['generation_id'] = self.request['operation_id']
        candidate['disk']['path'] = '/dev/vg0/routergen_' + self.request['operation_id']
        for name in ('kernel','initramfs'):
            candidate['boot'][name]['path'] = (
                '/mnt/dom0_data/klokast-router-updates/generations/' +
                self.request['operation_id'] + '/' + name)
        self.new = g.seal(candidate)
        r.write(self.base/'records'/(self.new['record_sha256']+'.json'),self.new)
        self.request['candidate_sha256'] = self.new['record_sha256']
        self.work = self.records.operation(self.request['operation_id'])
        self.xen = self.base / 'xen'
        self.xen.mkdir(mode=0o700); (self.xen / 'auto').mkdir(mode=0o700)
        r.write(self.work / 'transaction-request.json', self.request)
        r.write(self.work / 'request.json', {'kind':'klokast.router-replacement-preparation.v1',
            'operation_id':self.request['operation_id'],'engine_commit':self.request['engine_commit']})
        for side, value in (('old', self.old), ('candidate', self.new)):
            r.atomic(self.work / (side + '.cfg'), g.configuration(value).encode())
        r.atomic(self.xen / 'router.cfg', g.configuration(self.old).encode())
        self.candidate_preflight = {
            'kind':'klokast.router-candidate-preflight.v2',
            'box':'boxa','operation_id':self.request['operation_id'],
            'engine_commit':self.request['engine_commit'],
            'candidate_sha256':self.new['record_sha256'],
            'disk':self.new['disk'],'boot':self.new['boot'],
            'status':'first-contact-ready-and-detached','candidate_booted':False,
            'production_identity':False,'temporary_access':True,
            'tests':dict.fromkeys(('boot_artifacts','packages','openrc',
                'configuration_syntax','rendered_files','identity_absent',
                'first_contact_pinned'),True)}
        common = {'box':'boxa','operation_id':self.request['operation_id'],
            'engine_commit':self.request['engine_commit'],
            'old_sha256':self.old['record_sha256'],
            'candidate_sha256':self.new['record_sha256']}
        self.compatibility = {'kind':'klokast.router-retained-compatibility.v2',
            **common,'success':True,'production_identity':False,
            'phases':dict.fromkeys(('forward','new','reverse','old'),'8'*64)}
        self.copy_qualification = {'kind':'klokast.router-retained-copy-qualification.v2',
            **common,'forward_receipt_sha256':'9'*64,
            'reverse_receipt_sha256':'a'*64,'copy_guest_detached':True}
        for name,value in (('candidate-preflight',self.candidate_preflight),
                           ('compatibility',self.compatibility),
                           ('copy-qualification',self.copy_qualification)):
            r.write(self.work / (name + '.json'),value)
        self.ready = {'kind':'klokast.router-readiness.v4', 'request_sha256':g.digest(self.request),
            'release_sha256':self.new['release_sha256'],
            'candidate_preflight_sha256':g.digest(self.candidate_preflight),
            'compatibility_sha256':g.digest(self.compatibility),
            'copy_qualification_sha256':g.digest(self.copy_qualification),
            'gateway':'10.1.1.1'}
        r.write(self.work / 'readiness.json', self.ready)
        r.write(self.work / 'authorization.json', {'kind':'klokast.router-operation-authorization.v1',
            'request_sha256':g.digest(self.request),'readiness_sha256':g.digest(self.ready),
            'granted_at':int(time.time()),'expires_at':int(time.time())+600})
        prepared = {'kind':'klokast.router-candidate-preparation-result.v1',
            'operation_id':self.request['operation_id'],'success':True,
            'first_contact':{'host_key_public_sha256':{'ed25519':'a'*64}}}
        r.write(self.work/'preparation-result.json',prepared)
        r.write(self.work/'enrollment-source.json',{
            'kind':'klokast.router-replacement-enrollment-source.v1',
            'request_sha256':g.digest(self.request),'old_sha256':self.request['old_sha256'],
            'old_machine_id':'nOldRouter','preparation_sha256':g.digest(prepared)})
        self.host, self.copy = Host(), Copy()
        self.adapter = d.Adapter(self.records, self.request['operation_id'], self.copy, host=self.host, xen=self.xen)
        self.link = self.xen / 'auto/router.cfg'
        self.link.symlink_to('../router.cfg')
        # Root link ownership and native lbu are covered separately. Keep the
        # real adapter's file updates, pending records and accepted pointer.
        patch = mock.patch.object(self.adapter, 'autostart_link', return_value=self.link)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(n, 'command', return_value='')
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(self.adapter,'wait_enrollment',return_value=True)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(self.adapter,'finalize_candidate',return_value=None)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(d.finalization,'fence',return_value=None)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(self.adapter,'acceptance_proof',
            side_effect=lambda value: Dom0Tests.fake_acceptance_proof(self,value))
        patch.start(); self.addCleanup(patch.stop)

    def fake_acceptance_proof(self, value):
        expected = {'enrollment_sha256':'4'*64,'finalization_sha256':'5'*64}
        proof = {'service':'complete'}
        return d.acceptance(value,self.request,expected,proof)

    def accept(self):
        identities = {'request_sha256':g.digest(self.request),
            'candidate_sha256':self.request['candidate_sha256'],
            'enrollment_sha256':'4'*64,'finalization_sha256':'5'*64,
            'service_sha256':g.digest({'service':'complete'})}
        r.write(self.work / 'acceptance.json', {'kind':'klokast.router-controller-acceptance.v2',
            **identities,'evidence_sha256':g.digest(identities)})

    def test_acceptance_requires_exact_final_service_evidence(self):
        self.accept()
        value = r.read(self.work/'acceptance.json')
        self.assertEqual(self.fake_acceptance_proof(value),value)
        for key,changed in (('enrollment_sha256','a'*64),
                            ('finalization_sha256','b'*64),
                            ('service_sha256','c'*64),
                            ('evidence_sha256','d'*64)):
            with self.subTest(key=key),self.assertRaises(TransactionError):
                self.fake_acceptance_proof({**value,key:changed})
        with self.assertRaises(TransactionError):
            self.fake_acceptance_proof({**value,'kind':'klokast.router-controller-acceptance.v1'})

    def test_adapter_recomputes_and_checks_staged_service_target(self):
        self.accept()
        value = r.read(self.work/'acceptance.json')
        expected = g.seal({'kind':'test-service','box':'boxa',
            'operation_id':self.request['operation_id'],
            'candidate_sha256':self.request['candidate_sha256'],
            'machine_id':'nNewRouter','hostname':'boxa-router-' + self.request['operation_id'],
            'enrollment_sha256':'4'*64,'finalization_sha256':'5'*64})
        proof = {'kind':'klokast.router-replacement-service-proof.v1',
            'box':'boxa','operation_id':self.request['operation_id'],
            'expected_sha256':expected['record_sha256'],
            'candidate_sha256':self.request['candidate_sha256'],
            'machine_id':'nNewRouter','hostname':'boxa-router-' + self.request['operation_id'],
            'tests':dict.fromkeys(d.service.TESTS,True)}
        identities = {key:value[key] for key in ('request_sha256','candidate_sha256',
            'enrollment_sha256','finalization_sha256')}
        identities['service_sha256'] = g.digest(proof)
        value = {'kind':'klokast.router-controller-acceptance.v2',**identities,
                 'evidence_sha256':g.digest(identities)}
        # The source validator has its own tests. This adapter test checks
        # exact reconstruction, staged target comparison and proof binding.
        for name in ('candidate-job','release','enrollment-attempt','enrollment-result'):
            r.write(self.work/(name+'.json'),{'name':name})
        (self.work/'finalization').mkdir(mode=0o700)
        r.write(self.work/'finalization/result.json',{'name':'finalized'})
        r.write(self.work/'controller-service-expected.json',expected)
        r.write(self.work/'controller-service-proof.json',proof)
        d.devices.remember(self.records,self.old['record_sha256'],'nOldRouter',
            'boxa-router','7'*64)
        d.devices.remember(self.records,self.new['record_sha256'],'nNewRouter',
            'boxa-router-'+self.request['operation_id'],'8'*64)
        with mock.patch.object(d.finalization,'job_for',return_value={'job':'exact'}) as job, \
             mock.patch.object(d.service,'expected',return_value=expected) as target:
            self.assertEqual(d.Adapter.acceptance_proof(self.adapter,value),value)
            job.assert_called_once()
            target.assert_called_once()
            r.write(self.work/'controller-service-expected.json',
                {**expected,'machine_id':'wrong'})
            with self.assertRaises(TransactionError):
                d.Adapter.acceptance_proof(self.adapter,value)
            r.write(self.work/'controller-service-expected.json',expected)
            r.write(self.work/'controller-service-proof.json',
                {**proof,'tests':{**proof['tests'],'management':False}})
            with self.assertRaises(TransactionError):
                d.Adapter.acceptance_proof(self.adapter,value)
            r.write(self.work/'controller-service-proof.json',proof)
            d.devices.path(self.records,self.old['record_sha256']).unlink()
            with self.assertRaisesRegex(TransactionError,'distinct protected A/B devices'):
                d.Adapter.acceptance_proof(self.adapter,value)

    def test_arm_persists_one_attempt_and_accepts_only_a_distinct_device(self):
        self.assertEqual(self.adapter.request,self.request)
        self.adapter.arm(deadline=time.monotonic()+60)
        intent = r.read(self.work/'enrollment-attempt.json')
        self.assertEqual(intent['hostname'],'boxa-router-' + self.request['operation_id'])
        self.assertEqual(intent['old_machine_id'],'nOldRouter')
        self.host.live = 'candidate'
        result = {'kind':'klokast.router-replacement-enrollment-result.v1',
            'box':'boxa','operation_id':self.request['operation_id'],
            'request_sha256':g.digest(self.request),
            'attempt_sha256':intent['record_sha256'],
            'candidate_sha256':self.request['candidate_sha256'],
            'nonce':intent['nonce'],'machine_id':'nNewRouter',
            'hostname':intent['hostname'],'tags':['tag:vm'],'ssh':True,
            'state_sha256':'b'*64,'addresses':['100.64.0.8'],
            'host_key_public_sha256':intent['host_key_public_sha256']}
        r.write(self.work/'enrollment-result.json',result)
        self.assertTrue(d.Adapter.wait_enrollment(self.adapter,deadline=time.monotonic()+30))
        r.write(self.work/'enrollment-result.json',{**result,'machine_id':'nOldRouter'})
        with self.assertRaises(TransactionError):
            d.Adapter.wait_enrollment(self.adapter,deadline=time.monotonic()+30)

    def test_changed_enrollment_source_refuses_before_stopping_a(self):
        source = r.read(self.work/'enrollment-source.json')
        r.write(self.work/'enrollment-source.json',{**source,'old_machine_id':'bad device id'})
        with self.assertRaises(TransactionError):
            Transaction(self.request,self.adapter).cutover()
        self.assertIsNone(self.records.pending())
        self.assertEqual(self.host.live,'old')
        self.assertTrue(self.link.is_symlink())

    def test_full_acceptance_changes_durable_pointer_and_autostart_config(self):
        self.accept()
        self.assertEqual(Transaction(self.request,self.adapter).cutover(),'accepted')
        self.assertTrue(self.records.committed(self.request))
        self.assertIsNone(self.records.pending())
        self.assertEqual((self.xen/'router.cfg').read_text(),g.configuration(self.new))
        self.assertEqual(self.host.live,'candidate')
        self.assertTrue(self.link.is_symlink())

    def test_failed_forward_copy_restores_old_without_reverse_copy(self):
        self.copy.failure='old'
        self.assertEqual(Transaction(self.request,self.adapter).cutover(),'rolled-back')
        self.assertFalse(self.records.committed(self.request))
        self.assertNotIn(('candidate','old'),self.copy.events)
        self.assertEqual(self.host.live,'old')

    def test_rejected_candidate_copies_latest_state_before_restoring_old(self):
        with mock.patch.object(self.adapter,'wait_acceptance',return_value=False):
            self.assertEqual(Transaction(self.request,self.adapter).cutover(),'rolled-back')
        self.assertIn(('candidate','old'),self.copy.events)
        self.assertEqual(self.host.live,'old')
        self.assertEqual((self.xen/'router.cfg').read_text(),g.configuration(self.old))

    def test_reverse_copy_failure_fences_and_retains_pending_for_reconciliation(self):
        self.copy.failure='candidate'
        with mock.patch.object(self.adapter,'wait_acceptance',return_value=False),self.assertRaises(TransactionError):
            Transaction(self.request,self.adapter).cutover()
        self.assertIsNone(self.host.live)
        self.assertFalse(self.link.exists())
        self.assertEqual(self.records.pending()['phase'],'recovery-failed')
        self.assertFalse(self.records.committed(self.request))

    def test_acceptance_pointer_survives_power_loss_before_pending_checkpoint(self):
        self.accept()
        original=self.adapter.commit
        def crash(*args,**kw):
            original(*args,**kw)
            raise PowerLoss()
        with mock.patch.object(self.adapter,'commit',side_effect=crash),self.assertRaises(PowerLoss):
            Transaction(self.request,self.adapter).cutover()
        self.assertEqual(self.records.pending()['phase'],'committing')
        self.host.live=None
        self.assertEqual(Transaction(self.request,self.adapter,self.records.pending()).recover(),'accepted')
        self.assertNotIn(('candidate','old'),self.copy.events)
        self.assertEqual(self.host.live,'candidate')

    def test_expired_or_changed_authority_cannot_create_pending_or_stop_old(self):
        original=r.read(self.work/'authorization.json')
        for change in ({'expires_at':int(time.time())-1},{'request_sha256':'0'*64},{'readiness_sha256':'0'*64}):
            r.write(self.work/'authorization.json',{**original,**change})
            with self.assertRaises(TransactionError):
                Transaction(self.request,self.adapter).cutover()
            self.assertIsNone(self.records.pending())
            self.assertEqual(self.host.live,'old')
            self.assertEqual(self.host.events,[])

    def test_disposable_booted_or_changed_preflight_cannot_stop_old(self):
        path = self.work / 'candidate-preflight.json'
        for changed in ({'kind':'klokast.router-candidate-preparation-test-result.v1'},
                        {'kind':'klokast.router-retained-candidate-qualification.v1'},
                        {'kind':'klokast.router-candidate-preflight.v1'},
                        {'disk':{**self.new['disk'],'uuid':'other'}},
                        {'status':'running'},
                        {'candidate_booted':True},
                        {'production_identity':True},
                        {'temporary_access':False},
                        {'tests':{**self.candidate_preflight['tests'],
                                  'first_contact_pinned':False}}):
            value = {**self.candidate_preflight,**changed}
            r.write(path,value)
            # Rebind the checksums to prove refusal of the record's meaning,
            # not just a stale checksum or controller grant.
            self.ready['candidate_preflight_sha256'] = g.digest(value)
            self.adapter.ready = dict(self.ready)
            authorization = r.read(self.work/'authorization.json')
            r.write(self.work/'authorization.json',{
                **authorization,'readiness_sha256':g.digest(self.ready)})
            with self.subTest(changed=changed),self.assertRaises(TransactionError):
                Transaction(self.request,self.adapter).cutover()
            self.assertIsNone(self.records.pending())
            self.assertEqual(self.host.live,'old')
        r.write(path,self.candidate_preflight)
        self.ready['candidate_preflight_sha256'] = g.digest(self.candidate_preflight)
        self.adapter.ready = dict(self.ready)
        r.write(self.work/'authorization.json',{
            **authorization,'readiness_sha256':g.digest(self.ready)})
        r.write(self.work/'compatibility.json',{
            **self.compatibility,'candidate_sha256':'f'*64})
        with self.assertRaises(TransactionError):
            Transaction(self.request,self.adapter).cutover()
        r.write(self.work/'compatibility.json',self.compatibility)
        r.write(self.work/'copy-qualification.json',{
            **self.copy_qualification,'copy_guest_detached':False})
        with self.assertRaises(TransactionError):
            Transaction(self.request,self.adapter).cutover()
        self.assertIsNone(self.records.pending())
        self.assertEqual(self.host.live,'old')

    def test_previous_live_candidate_readiness_contract_is_rejected(self):
        with self.assertRaises(TransactionError):
            d.readiness({**self.ready,'kind':'klokast.router-readiness.v3'},self.request)
        previous = {**self.ready,'kind':'klokast.router-readiness.v1'}
        previous['candidate_qualification_sha256'] = previous.pop('candidate_preflight_sha256')
        with self.assertRaises(TransactionError):
            d.readiness(previous,self.request)
        self.assertIsNone(self.records.pending())
        self.assertEqual(self.host.live,'old')

    def test_boot_recovery_does_not_require_controller_grant(self):
        self.records.persist(self.pending)
        (self.work/'authorization.json').unlink()
        self.host.live=None
        self.assertEqual(Transaction(self.request,self.adapter,self.records.pending()).recover(),'rolled-back')
        self.assertEqual(self.host.live,'old')


if __name__ == '__main__':
    unittest.main()
