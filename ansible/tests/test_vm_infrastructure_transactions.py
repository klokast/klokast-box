"""Second controller and runner generations, including pre-boot recovery."""
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_vm_update_transaction as recovery
import vm_infrastructure_source


class InfrastructureGenerations(unittest.TestCase):
    def fixture(self, role):
        f=recovery.PersistentDataTransactions();f.role=role;f.setUp()
        self.addCleanup(f.doCleanups)
        f.request['image_profile']=role+'-alpine-v1';f.save()
        return f

    def accept(self, tx):
        t=recovery.t
        tx.step('stop');tx.step('start')
        t.store(tx.work/'checks.json',{'request_sha256':t.digest(tx.request),
            'checks':{k:True for k in ('boot','kernel_modules','identity','firewall',
                'retained_state','workloads_unchanged','service','reboot')}})
        tx.step('tested');tx.step('accept');tx.step('complete')

    def second(self, role):
        f=self.fixture(role);t=recovery.t
        first=f.tx();first.arm();self.accept(first)
        work=f.base/'operations'/('b'*24);work.mkdir()
        request=dict(f.request,operation_id=work.name,release_sha256='f'*64,
            old_uuid=f.request['new_uuid'],new_uuid='33333333-3333-4333-8333-333333333333')
        old=first.assignment_content('new')
        new=old.decode().replace(request['old_uuid'],request['new_uuid']).replace('new-','next-').encode()
        for side,content in (('old',old),('new',new)):
            (work/(side+'.cfg')).write_bytes(content)
            request[side+'_config_sha256']=hashlib.sha256(content).hexdigest()
            for kind in ('disks','artifacts'):
                request[side+'_'+kind]={(k.replace('new-','next-') if side=='new' else k):v
                    for k,v in f.request['new_'+kind].items()}
        t.store(work/'request.json',request)
        backend=recovery.Xen(request)
        tx=t.Transaction(work,backend,lambda:f.now);tx.arm()
        # Reload the protected journal, as the separate native commands do.
        return f,t.Transaction(work,backend,lambda:f.now),backend

    def test_second_generation_accepts_and_preserves_retained_disk(self):
        for role in ('ops','air'):
            with self.subTest(role=role):
                f,tx,backend=self.second(role)
                self.accept(tx)
                current=recovery.t.Transaction(tx.work,backend,lambda:f.now)
                self.assertEqual(current.journal['stage'],'complete')
                self.assertEqual(current.journal['previous_assignment']['profile'],role+'-alpine-v1')
                self.assertEqual(current.request['retained_disks'],f.request['retained_disks'])

    def test_second_generation_recovers_the_accepted_source_before_shutdown(self):
        for role in ('ops','air'):
            with self.subTest(role=role):
                f,tx,backend=self.second(role)
                self.assertEqual(tx.recover(),'recovered')
                current=recovery.t.Transaction(tx.work,backend,lambda:f.now)
                self.assertEqual(current.release_identity('old'),{
                    'profile':role+'-alpine-v1','release_sha256':f.request['release_sha256']})
                self.assertEqual(backend.running,'old')
                self.assertFalse(any(call[0] in ('stop','start') for call in backend.calls))

    def test_unknown_previous_profile_is_rejected_without_changing_the_guest(self):
        f,tx,backend=self.second('ops')
        tx.journal['previous_assignment']['profile']='unknown-alpine-v1'
        recovery.t.store(tx.work/'journal.json',tx.journal)
        before=list(backend.calls)
        with self.assertRaisesRegex(recovery.t.Refused,'previous accepted assignment identity'):
            recovery.t.Transaction(tx.work,backend,lambda:f.now)
        self.assertEqual(backend.calls,before)

    def test_recovered_source_survives_unused_candidate_disk_and_kernel_retirement(self):
        f,tx,backend=self.second('ops');tx.recover()
        old_lv,old_artifact=backend.lv,backend.artifact
        def lv(path):
            self.assertIn(path,tx.request['old_disks'])
            return old_lv(path)
        def artifact(path):
            self.assertIn(path,tx.request['old_artifacts'])
            return old_artifact(path)
        with patch.object(backend,'lv',side_effect=lv),patch.object(backend,'artifact',side_effect=artifact):
            current=recovery.t.Transaction(tx.work,backend,lambda:f.now)
            self.assertEqual(current.recover(),'recovered')
            self.assertEqual(recovery.t.assignment_report(current)['vm_uuid'],f.request['new_uuid'])
        self.assertEqual(backend.running,'old')

    def test_recovery_finds_the_exact_accepted_service_and_keeps_its_data(self):
        for role in ('ops','air'):
            with self.subTest(role=role):
                f,tx,_=self.second(role);tx.recover();t=recovery.t
                record={'kind':'klokast.vm-service-update.v1','box':'boxa','role':role,'operation_id':f.work.name,
                    'new_uuid':f.request['new_uuid'],'new_disks':f.request['new_disks'],
                    'reboot_verification':{'status':'complete'}}
                t.store(f.work/'service.json',record)
                assignment=t.assignment_report(tx)
                engine=SimpleNamespace(t=t,read=t.read)
                self.assertEqual(vm_infrastructure_source.recovered_service(engine,'boxa',role,assignment),record)
                for change in ({'new_disks':{}},{'reboot_verification':{'status':'required'}}):
                    t.store(f.work/'service.json',dict(record,**change))
                    with self.assertRaisesRegex(RuntimeError,'accepted service evidence'):
                        vm_infrastructure_source.recovered_service(engine,'boxa',role,assignment)
                (f.work/'service.json').unlink()
                with self.assertRaisesRegex(RuntimeError,'one exact accepted service'):
                    vm_infrastructure_source.recovered_service(engine,'boxa',role,assignment)

    def test_unmigrated_recovery_has_no_accepted_service(self):
        self.assertIsNone(vm_infrastructure_source.recovered_service(None,'boxa','ops',
            {'profile':None,'release_sha256':None}))
