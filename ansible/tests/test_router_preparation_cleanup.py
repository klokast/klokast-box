"""Unused cleanup never selects live authority and reconciles exact removal intent."""
import copy
import hashlib
import os
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import router_preparation_cleanup as cleanup
import router_records as records
import router_generations as generations
import router_candidate_preparation as preparation
import router_executor as executor
from router_transaction import TransactionError
import test_router_dom0 as dom0_tests
import test_router_candidate as candidate_tests


class PreparationCleanupTests(unittest.TestCase):
    def setUp(self):
        dom0_tests.Dom0Tests.setUp(self)
        fixture = candidate_tests.CandidateTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.job = copy.deepcopy(fixture.job)
        self.operation,self.engine = self.request['operation_id'],self.request['engine_commit']
        self.job.update(operation_id=self.operation,engine_commit=self.engine)
        self.source = {'kind':'klokast.router-replacement-preparation.v1','mode':'replacement',
            'box':'boxa','operation_id':self.operation,'engine_commit':self.engine,
            'inputs_sha256':self.job['inputs_sha256'],'job_sha256':generations.digest(self.job),
            'accepted_assignment_sha256':self.records.accepted()['record_sha256'],
            'old_sha256':self.old['record_sha256'], 'bootstrap':{}}
        (self.work/'authorization.json').unlink()
        for name in ('kernel','initramfs'):
            path = self.work/('bootstrap-'+name)
            path.write_bytes(('unused-'+name).encode()); path.chmod(0o600)
            self.source['bootstrap'][name] = {'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        records.write(self.work/'request.json',self.source)
        records.write(self.work/'candidate-job.json',self.job)
        self.disk = {'path':'/dev/vg0/routergen_'+self.operation,'uuid':'unused-disk-uuid','bytes':cleanup.disks.BYTES}
        self.disk_record = {'kind':'klokast.router-candidate-disk.v1','operation_id':self.operation,
            'path':self.disk['path'],'tag':'routergen_'+self.operation,'uuid':self.disk['uuid'],
            'stage':'cloned','template_sha256':'a'*64}
        records.write(self.work/'candidate-disk.json',self.disk_record)
        loop = '/dev/loop77'
        name,cfg = preparation.configuration(self.work,self.source,self.disk,loop,
            '33333333-1111-4111-8111-111111111111')
        cfg.chmod(0o600)
        slot = self.work/'result.slot'
        with slot.open('wb') as stream:
            stream.truncate(cleanup.MIB)
        slot.chmod(0o600)
        self.ledger = {'kind':'klokast.router-preparation-run.v1','operation_id':self.operation,
            'request_sha256':generations.digest(self.source),'disk':self.disk,'domain':name,
            'stage':'booting','uuid':'33333333-1111-4111-8111-111111111111',
            'config_sha256':hashlib.sha256(cfg.read_bytes()).hexdigest(),'result_loop':loop,'result_sha256':None}
        records.write(self.work/'preparation.json',self.ledger)
        self.host.inventory = mock.Mock(return_value=[{'domid':0}])
        self.host.wait_detached = mock.Mock()
        self.host.device = mock.Mock(side_effect=lambda path:path)
        artifact = cleanup.native.Native.artifact
        self.host.artifact = lambda item,**kwargs:artifact(self.host,item,**kwargs)
        self.row = {'lv_path':self.disk['path'],'lv_uuid':self.disk['uuid'],'lv_size':str(self.disk['bytes']),
            'lv_attr':'-wi-a-----','origin':'','lv_tags':'routergen_'+self.operation}
        self.commands = []
        self.interrupt_remove = False
        for owner,name,value in ((cleanup.native,'Native',mock.Mock(return_value=self.host)),
            (cleanup.disks,'inventory',mock.Mock(side_effect=lambda:[self.row] if self.row else [])),
            (cleanup.copy_native,'loops',mock.Mock(return_value=[])),
            (preparation,'loop_devices',mock.Mock(return_value=[])),
            (preparation,'safe_file',lambda path,maximum:records.secure(path,maximum=maximum)),
            (cleanup.native,'command',mock.Mock(side_effect=self.command))):
            patch = mock.patch.object(owner,name,value); patch.start(); self.addCleanup(patch.stop)
        self.verify = mock.Mock()
        self.token = 'a'*12

    def command(self,argv,*args,**kwargs):
        self.assertEqual(argv,['/sbin/lvremove','--yes',self.disk['path']])
        self.assertEqual(records.read(self.work/'preparation-cleanup-progress.json')['phase'],'disk-removing')
        self.assertEqual(records.read(self.work/'candidate-disk.json')['stage'],'retiring')
        self.commands.append(argv); self.row = None
        if self.interrupt_remove:
            self.interrupt_remove = False
            raise KeyboardInterrupt
        return ''

    def plan(self):
        return cleanup.plan(self.records,self.operation,self.engine,verify=self.verify)

    def grant(self):
        plan = self.plan()
        now = int(time.time())
        self.authority = {'kind':'klokast.router-preparation-cleanup-authorization.v1',
            'plan_sha256':plan['record_sha256'],'assignment_sha256':plan['assignment_sha256'],
            'service_sha256':'b'*64,'registration_absent_sha256':'c'*64,
            'granted_at':now,'expires_at':now+600}
        records.write(self.work/('preparation-cleanup-grant-'+self.token+'.json'),self.authority)
        return plan

    def retire(self):
        return cleanup.retire(self.records,self.operation,self.engine,self.token,verify=self.verify)

    def test_exact_unused_retirement_keeps_accepted_router_and_metadata_and_retries(self):
        selected = self.grant()
        before = self.records.accepted()
        result = self.retire()
        self.assertEqual(result['status'],'unused-resources-retired')
        self.assertEqual(self.records.accepted(),before)
        self.assertEqual(self.retire(),result)
        self.assertEqual(len(self.commands),1)
        self.assertTrue((self.work/'preparation.json').exists())
        self.assertTrue((self.work/'candidate-job.json').exists())
        self.assertTrue(all(not Path(item['path']).exists() for item in selected['files']))

    def test_lost_lvremove_reply_uses_only_the_durable_intent(self):
        self.grant(); self.interrupt_remove = True
        with self.assertRaises(KeyboardInterrupt):
            self.retire()
        self.assertFalse((self.work/'preparation-cleanup-complete.json').exists())
        self.assertEqual(self.retire()['status'],'unused-resources-retired')
        self.assertEqual(len(self.commands),1)

    def test_loss_after_file_unlink_reconciles_only_recorded_inflight_absence(self):
        selected = self.grant()
        target = Path(selected['files'][0]['path'])
        unlink = Path.unlink
        def lost(path,*args,**kwargs):
            result = unlink(path,*args,**kwargs)
            if path == target:
                raise KeyboardInterrupt
            return result
        with mock.patch.object(Path,'unlink',lost),self.assertRaises(KeyboardInterrupt):
            self.retire()
        self.assertEqual(records.read(self.work/'preparation-cleanup-progress.json')['inflight'],str(target))
        self.assertEqual(self.retire()['status'],'unused-resources-retired')

    def test_missing_file_without_intent_changed_inode_and_reappeared_resources_refuse(self):
        selected = self.grant()
        target = Path(selected['files'][0]['path'])
        replacement = self.work/'replacement'
        replacement.write_bytes(target.read_bytes()); replacement.chmod(0o600); replacement.replace(target)
        with self.assertRaisesRegex(TransactionError,'file identity changed'):
            self.retire()
        self.assertFalse((self.work/'preparation-cleanup-complete.json').exists())
        target.unlink()
        with self.assertRaisesRegex(TransactionError,'without its exact removal intent'):
            self.retire()

    def test_launch_and_identity_markers_refuse_before_grant_or_removal(self):
        for name in ('worker.log','complete.json','enrollment-attempt.json','enrollment-result.json','copy/allocation.json'):
            path = self.work/name
            path.parent.mkdir(mode=0o700,exist_ok=True)
            records.write(path,{'marker':True})
            with self.subTest(name=name),self.assertRaisesRegex(TransactionError,'markers'):
                self.plan()
            path.unlink()
        records.write(self.work/'authorization.json',{'kind':'klokast.router-operation-authorization.v1'})
        with self.assertRaisesRegex(TransactionError,'cutover authority'):
            self.plan()
        self.assertEqual(self.commands,[])

    def test_changed_assignment_pending_transaction_and_expired_grant_refuse(self):
        self.grant()
        self.authority['expires_at'] = self.authority['granted_at']
        records.write(self.work/('preparation-cleanup-grant-'+self.token+'.json'),self.authority)
        with self.assertRaisesRegex(TransactionError,'grant expired'):
            self.retire()
        self.records.persist(self.pending)
        with self.assertRaisesRegex(TransactionError,'pending'):
            self.plan()
        self.assertEqual(self.commands,[])

    def test_renamed_or_unrecorded_lv_and_live_helper_refuse(self):
        self.row['lv_path'] = '/dev/vg0/renamed'
        with self.assertRaisesRegex(TransactionError,'UUID moved'):
            self.plan()
        self.row['lv_path'] = self.disk['path']
        (self.work/'candidate-disk.json').unlink()
        with self.assertRaisesRegex(TransactionError,'no protected allocation'):
            self.plan()
        records.write(self.work/'candidate-disk.json',self.disk_record)
        self.grant()
        self.host.inventory.return_value = [{'domid':3,'config':{'c_info':{'name':self.ledger['domain'],
            'uuid':self.ledger['uuid'],'type':'pvh'},'b_info':{},'disks':[]}}]
        with self.assertRaises(RuntimeError):
            self.retire()
        self.assertEqual(self.commands,[])

    def test_planned_allocation_uses_the_root_inspected_uuid_bound_into_the_plan(self):
        records.write(self.work/'candidate-disk.json',{**self.disk_record,'stage':'planned','uuid':None})
        selected = self.grant()
        self.assertEqual(selected['disk']['uuid'],self.disk['uuid'])
        self.assertEqual(selected['disk_record']['uuid'],None)
        self.assertEqual(self.retire()['status'],'unused-resources-retired')
        self.assertEqual(cleanup.disks.record(self.work,self.operation)['stage'],'retired')

    def test_supervisor_fences_worker_before_collecting_protected_completion(self):
        self.grant(); expected = self.retire()
        events = []
        with mock.patch.object(executor.subprocess,'Popen',side_effect=lambda *a,**k:events.append('start') or object()), \
                mock.patch.object(executor,'wait_worker',side_effect=lambda *a:events.append('fence') or 0), \
                mock.patch.object(executor,'boot_assignment',self.verify):
            self.assertEqual(executor.supervise_preparation_cleanup(self.records,self.operation,self.engine,self.token),expected)
        self.assertEqual(events,['start','fence'])

    def test_reappeared_lv_or_file_cannot_reuse_a_completed_receipt(self):
        selected = self.grant(); self.retire()
        self.row = {'lv_path':self.disk['path'],'lv_uuid':self.disk['uuid'],'lv_size':str(self.disk['bytes']),
            'lv_attr':'-wi-a-----','origin':'','lv_tags':'routergen_'+self.operation}
        with self.assertRaisesRegex(TransactionError,'reappeared'):
            self.retire()
        self.row = None
        path = Path(selected['files'][0]['path'])
        path.write_bytes(b'reappeared'); path.chmod(0o600)
        with self.assertRaisesRegex(TransactionError,'file reappeared'):
            self.retire()
        self.assertEqual(len(self.commands),1)

    def test_cached_plan_cannot_escape_scope_or_claim_partial_progress_complete(self):
        selected = self.grant()
        for stage, identity in (('planned',self.disk_record['uuid']),('allocated',None)):
            changed = copy.deepcopy(selected); changed.pop('record_sha256')
            changed['disk_record'].update(stage=stage,uuid=identity)
            records.write(self.work/'preparation-cleanup-plan.json',generations.seal(changed))
            with self.assertRaisesRegex(TransactionError,'allocation record changed'):
                self.retire()
        changed = copy.deepcopy(selected); changed.pop('record_sha256')
        changed['files'][0]['path'] = '/etc/shadow'
        records.write(self.work/'preparation-cleanup-plan.json',generations.seal(changed))
        with self.assertRaisesRegex(TransactionError,'escapes its exact operation'):
            self.retire()
        records.write(self.work/'preparation-cleanup-plan.json',selected)
        records.write(self.work/'preparation-cleanup-progress.json',generations.seal({
            'kind':'klokast.router-preparation-cleanup-progress.v1','plan_sha256':selected['record_sha256'],
            'phase':'complete','removed':[],'inflight':None}))
        with self.assertRaisesRegex(TransactionError,'progress changed'):
            self.retire()
        self.assertEqual(self.commands,[])

    def test_partial_transfer_selects_only_manifest_files_and_unknown_parts_refuse(self):
        directory = self.work/'parts'
        directory.mkdir(mode=0o700)
        parts = []
        for name in ('kernel','initramfs'):
            (directory/name).mkdir(mode=0o700)
            parts.append({'artifact':name,'name':'part-0000','bytes':3,'sha256':hashlib.sha256(b'raw').hexdigest()})
        records.write(self.work/'parts.json',parts)
        known = directory/'kernel/part-0000'
        known.write_bytes(b'raw'); known.chmod(0o600)
        unknown = directory/'kernel/part-9999'
        unknown.write_bytes(b'unknown'); unknown.chmod(0o600)
        with self.assertRaisesRegex(TransactionError,'unrecorded files'):
            self.plan()
        self.assertTrue(unknown.exists())
        unknown.unlink()
        selected = self.grant()
        self.assertIn(str(known),[item['path'] for item in selected['files']])
        self.retire()
        self.assertFalse(known.exists())


if __name__ == '__main__':
    unittest.main()
