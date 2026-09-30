"""A dead worker cannot leave native children running while recovery starts."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_executor as e
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_dom0 as dom0_tests
import test_router_generations as generation_tests


class WorkerTests(unittest.TestCase):
    def test_dead_worker_children_are_stopped_before_its_pid_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'child.pid'
            code=('import subprocess,sys; from pathlib import Path; '
                  'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                  'Path(sys.argv[1]).write_text(str(p.pid)); sys.exit(3)')
            process=subprocess.Popen([sys.executable,'-c',code,str(path)],start_new_session=True)
            self.assertEqual(e.wait_worker(process,5),3)
            identity=int(path.read_text())
            limit=time.monotonic()+2
            while time.monotonic()<limit:
                status=Path('/proc')/str(identity)/'stat'
                try:
                    state=status.read_text().rsplit(')',1)[1].split()[0]
                except (FileNotFoundError, ProcessLookupError):
                    break
                if state=='Z':
                    break
                time.sleep(.01)
            else:
                self.fail('native child remains running after worker reaping')

    def test_hung_worker_is_stopped_at_its_fixed_deadline(self):
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
        started=time.monotonic()
        self.assertEqual(e.wait_worker(process,.15),-signal.SIGKILL)
        self.assertLess(time.monotonic()-started,2)


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        dom0_tests.Dom0Tests.setUp(self)

    def test_worker_death_runs_durable_recovery_without_replaying_cutover(self):
        pending={**self.pending,'phase':'awaiting-acceptance','candidate_started':True}
        self.records.persist(pending)
        self.host.live='candidate'
        with mock.patch.object(e.subprocess,'Popen'),mock.patch.object(e,'wait_worker',return_value=-9), \
             mock.patch.object(e,'adapter',return_value=self.adapter):
            self.assertEqual(e.supervise(self.records,self.request['operation_id'],self.request['engine_commit']),'rolled-back')
        self.assertIn(('candidate','old'),self.copy.events)
        self.assertEqual(self.host.live,'old')
        self.assertIsNone(self.records.pending())

    def test_preflight_worker_failure_does_not_guess_a_recovery_generation(self):
        with mock.patch.object(e.subprocess,'Popen'),mock.patch.object(e,'wait_worker',return_value=1), \
             mock.patch.object(e,'adapter') as adapter,self.assertRaises(TransactionError):
            e.supervise(self.records,self.request['operation_id'],self.request['engine_commit'])
        adapter.assert_not_called()
        self.assertEqual(self.host.live,'old')

    def test_map_projects_generations_without_private_receipt_contents(self):
        import json
        initial=e.map_status(self.records)
        self.assertEqual(initial['current']['generation_id'],self.old['generation_id'])
        self.assertIsNone(initial['previous'])
        self.assertIsNone(initial['state_copy'])
        self.records.persist(self.pending)
        directory=self.adapter.work/'copy'
        directory.mkdir(exist_ok=True)
        for name in ('forward.result.slot','forward.private.slot'):
            (directory/name).write_text('synthetic private content')
        self.copy.verify_receipt=mock.Mock()
        with mock.patch.object(e,'adapter',return_value=self.adapter):
            value=e.map_status(self.records)
        self.assertEqual(value['pending']['operation_id'],self.request['operation_id'])
        self.assertEqual(value['state_copy']['forward'],'complete')
        self.assertEqual(value['state_copy']['reverse'],'absent')
        self.assertNotIn('synthetic private',json.dumps(value))
        self.copy.verify_receipt.side_effect=TransactionError('synthetic private parse error')
        with mock.patch.object(e,'adapter',return_value=self.adapter):
            value=e.map_status(self.records)
        self.assertEqual(value['state_copy']['forward'],'unverified')
        self.assertNotIn('parse error',json.dumps(value))

    def test_accepted_manifest_exposes_only_current_package_and_file_identity(self):
        value=e.accepted_manifest(self.records)
        self.assertEqual(value['generation_sha256'],self.old['record_sha256'])
        self.assertEqual(value['packages'],self.old['packages'])
        self.assertEqual(value['origin'],self.old['origin'])
        self.assertEqual(value['tailscale'],self.old.get('tailscale'))
        self.assertEqual(value['configuration_files'],self.old['configuration_files'])
        self.assertNotIn('disk',value)
        self.assertNotIn('accounts',value)
        self.records.persist(self.pending)
        with self.assertRaisesRegex(TransactionError,'pending'):
            e.accepted_manifest(self.records)

    def test_accepted_source_is_exact_and_unavailable_during_replacement(self):
        value=e.accepted_source(self.records)
        self.assertEqual(value['assignment']['current_sha256'],self.old['record_sha256'])
        self.assertEqual(value['generation'],self.old)
        self.records.persist(self.pending)
        with self.assertRaisesRegex(TransactionError,'pending'):
            e.accepted_source(self.records)

    def test_provisioning_status_exposes_protected_router_pointers(self):
        value=e.provisioning_status(self.records)
        self.assertEqual(value['assignment']['current_sha256'],self.old['record_sha256'])
        self.assertIsNone(value['pending'])
        self.assertIsNone(value['installation'])
        self.records.persist(self.pending)
        value=e.provisioning_status(self.records)
        self.assertEqual(value['pending']['request']['operation_id'],self.request['operation_id'])

    def test_enrollment_signal_requires_waiting_b_and_is_idempotent(self):
        self.adapter.arm(deadline=time.monotonic()+60)
        intent=records.read(self.adapter.work/'enrollment-attempt.json')
        pending={**self.pending,'phase':'awaiting-enrollment','candidate_started':True}
        self.records.persist(pending)
        self.host.live='candidate'
        value={'kind':'klokast.router-replacement-enrollment-result.v1',
            'box':'boxa','operation_id':self.request['operation_id'],
            'request_sha256':generations.digest(self.request),
            'attempt_sha256':intent['record_sha256'],
            'candidate_sha256':self.request['candidate_sha256'],
            'nonce':intent['nonce'],'machine_id':'nNewRouter',
            'hostname':intent['hostname'],'tags':['tag:vm'],'ssh':True,
            'state_sha256':'b'*64,'addresses':['100.64.0.8'],
            'host_key_public_sha256':intent['host_key_public_sha256']}
        records.write(self.adapter.work/'controller-enrollment.json',value)
        with mock.patch.object(e.native,'Native',return_value=self.host):
            self.assertEqual(e.signal_enrollment(self.records,self.request['operation_id'],
                self.request['engine_commit']),'exact-enrollment-published')
            self.assertEqual(e.signal_enrollment(self.records,self.request['operation_id'],
                self.request['engine_commit']),'exact-enrollment-published')
            records.write(self.adapter.work/'controller-enrollment.json',{**value,'machine_id':'nOldRouter'})
            with self.assertRaises(TransactionError):
                e.signal_enrollment(self.records,self.request['operation_id'],
                    self.request['engine_commit'])
            records.write(self.adapter.work/'controller-enrollment.json',value)
            self.records.persist({**pending,'phase':'checking-final-candidate'})
            with self.assertRaisesRegex(TransactionError,'not awaiting enrollment'):
                e.signal_enrollment(self.records,self.request['operation_id'],
                    self.request['engine_commit'])

    def test_final_candidate_status_requires_exact_running_generation(self):
        self.adapter.arm(deadline=time.monotonic()+60)
        intent=records.read(self.adapter.work/'enrollment-attempt.json')
        enrolled={'kind':'klokast.router-replacement-enrollment-result.v1',
            'box':'boxa','operation_id':self.request['operation_id'],
            'request_sha256':generations.digest(self.request),
            'attempt_sha256':intent['record_sha256'],
            'candidate_sha256':self.request['candidate_sha256'],
            'nonce':intent['nonce'],'machine_id':'nNewRouter',
            'hostname':intent['hostname'],'tags':['tag:vm'],'ssh':True,
            'state_sha256':'b'*64,'addresses':['100.64.0.8'],
            'host_key_public_sha256':intent['host_key_public_sha256']}
        records.write(self.adapter.work/'enrollment-result.json',enrolled)
        final=self.adapter.work/'finalization'
        final.mkdir(mode=0o700)
        records.write(final/'result.json',{'kind':'klokast.router-replacement-finalization-result.v1',
            'success':True,'machine_id':'nNewRouter'})
        self.records.persist({**self.pending,'phase':'awaiting-acceptance','candidate_started':True})
        self.host.live='candidate'
        with mock.patch.object(e.native,'Native',return_value=self.host):
            value=e.candidate_status(self.records,self.request['operation_id'],
                self.request['engine_commit'])
            self.assertEqual(value['status'],'running-final')
            self.assertEqual(value['machine_id'],'nNewRouter')
            self.host.live='old'
            with self.assertRaisesRegex(TransactionError,'not the exact running'):
                e.candidate_status(self.records,self.request['operation_id'],
                    self.request['engine_commit'])

    def test_map_refuses_changed_pointers_instead_of_joining_two_operations(self):
        with mock.patch.object(self.records,'pending',side_effect=[None,self.pending]):
            with self.assertRaisesRegex(TransactionError,'pointers changed'):
                e.map_status(self.records)

    def test_map_rejects_pending_operation_for_another_accepted_pointer(self):
        self.records.persist(self.pending)
        with mock.patch.object(self.records,'committed',side_effect=TransactionError('assignment differs')):
            with self.assertRaisesRegex(TransactionError,'assignment differs'):
                e.map_status(self.records)


class BaselineTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name)
        for name in ('records','generations','operations'):
            (self.base/name).mkdir(mode=0o700)
        for name,value in (('ROOT_UID',os.geteuid()),('parents',lambda path:None)):
            patch=mock.patch.object(records,name,value)
            patch.start();self.addCleanup(patch.stop)
        self.storage=records.Records('boxa',self.base)
        self.record=generation_tests.generation('legacy')
        self.operation=self.record['generation_id']
        self.work=self.base/'operations'/self.operation
        self.work.mkdir(mode=0o700)
        records.write(self.work/'generation.json',self.record)
        now=int(time.time())
        self.grant={'kind':'klokast.router-baseline-grant.v1','box':'boxa',
                    'operation_id':self.operation,'engine_commit':self.record['engine_commit'],
                    'generation_sha256':self.record['record_sha256'],
                    'inspection_sha256':self.record['evidence_sha256'],
                    'granted_at':now-1,'expires_at':now+180}
        records.write(self.work/'authorization.json',self.grant)
        self.xen=self.base/'xen'
        (self.xen/'auto').mkdir(parents=True)
        config=self.xen/'router.cfg'
        config.write_text(generations.configuration(self.record))
        config.chmod(0o600)
        (self.xen/'auto/router.cfg').symlink_to('../router.cfg')
        self.host=mock.Mock()
        self.host.monotonic.return_value=100.0
        self.host.guest.return_value=('legacy',{})
        patch=mock.patch.object(e.native,'Native',return_value=self.host)
        patch.start();self.addCleanup(patch.stop)

    def test_adoption_checks_live_source_then_publishes_one_baseline(self):
        with self.storage.lock():
            result=e.adopt_baseline(self.storage,self.operation,self.record['engine_commit'],xen=self.xen)
        self.assertEqual(result['status'],'baseline-adopted')
        self.assertEqual(self.storage.accepted()['current_sha256'],self.record['record_sha256'])
        self.assertEqual(records.read(self.work/'adoption.json')['assignment_sha256'],result['assignment_sha256'])
        self.host.guest.assert_called_once()
        with self.storage.lock(),self.assertRaisesRegex(TransactionError,'no accepted'):
            e.adopt_baseline(self.storage,self.operation,self.record['engine_commit'],xen=self.xen)

    def test_changed_boot_definition_or_stale_grant_never_publishes(self):
        (self.xen/'router.cfg').write_text('name = "router-other"\n')
        with self.storage.lock(),self.assertRaises(TransactionError):
            e.adopt_baseline(self.storage,self.operation,self.record['engine_commit'],xen=self.xen)
        self.assertFalse((self.base/'accepted.json').exists())
        (self.xen/'router.cfg').write_text(generations.configuration(self.record))
        records.write(self.work/'authorization.json',{**self.grant,'expires_at':self.grant['granted_at']})
        with self.storage.lock(),self.assertRaisesRegex(TransactionError,'grant'):
            e.adopt_baseline(self.storage,self.operation,self.record['engine_commit'],xen=self.xen)
        self.assertFalse((self.base/'accepted.json').exists())

    def test_changed_running_source_never_publishes(self):
        self.host.guest.return_value=None
        with self.storage.lock(),self.assertRaisesRegex(TransactionError,'not running'):
            e.adopt_baseline(self.storage,self.operation,self.record['engine_commit'],xen=self.xen)
        self.assertFalse((self.base/'accepted.json').exists())

    def test_grant_for_another_engine_never_publishes(self):
        records.write(self.work/'authorization.json',{**self.grant,'engine_commit':'f'*40})
        with self.storage.lock(),self.assertRaisesRegex(TransactionError,'grant'):
            e.adopt_baseline(self.storage,self.operation,self.record['engine_commit'],xen=self.xen)
        self.assertFalse((self.base/'accepted.json').exists())


if __name__ == '__main__':
    unittest.main()
