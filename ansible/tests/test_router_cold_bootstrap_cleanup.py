"""Unused cold inspector retirement must preserve live and uncertain resources."""
import hashlib
import contextlib
import io
import json
import unittest
from unittest import mock

import router_cold_filesystem as cold
import router_generations as generations
import router_records as records
import router_executor as executor
from router_transaction import TransactionError
import test_router_cold_backup as fixtures
import test_router_cold_window as window


class ColdBootstrapCleanupTests(unittest.TestCase):
    setUp=fixtures.ColdBundleTests.setUp
    capture=fixtures.ColdBundleTests.capture

    def prepare(self):
        self.capture();window.WindowTests.stage_capsule(self)
        self.host.running=True
        self.host.inventory=mock.Mock(return_value=[{'domid':0,'config':{'c_info':{'name':'Domain-0'}}}])
        def artifact(value,**kwargs):
            path=cold.Path(value['path']);records.secure(path,maximum=value['bytes'])
            if path.stat().st_size != value['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != value['sha256']:
                raise TransactionError('test artifact bytes changed')
        self.host.artifact=artifact
        metadata,generation=self.bundle.verify()
        disk=generations.seal({'kind':'klokast.router-cold-disk.v1','box':'boxa',
            'operation_id':self.bundle.operation,'engine_commit':self.bundle.engine,
            'metadata_sha256':metadata['record_sha256'],'source':generation['disk'],
            'backup':{'path':self.inspector.backup.path,'uuid':'backup-uuid','bytes':2147483648},
            'stage':'allocated','source_sha256':None})
        records.write(self.inspector.backup.record,disk)
        intent=generations.seal({'kind':'klokast.router-cold-prepared-abort-intent.v1','box':'boxa',
            'operation_id':self.bundle.operation,'source_engine_commit':self.bundle.engine,
            'cleanup_engine_commit':'d'*40,'metadata_sha256':metadata['record_sha256'],'backup_uuid':'backup-uuid'})
        records.write(self.bundle.directory/'prepared-abort-intent.json',intent)
        records.write(self.bundle.directory/'prepared-abort-completion.json',generations.seal({
            'kind':'klokast.router-cold-prepared-abort.v1','box':'boxa','operation_id':self.bundle.operation,
            'source_engine_commit':self.bundle.engine,'cleanup_engine_commit':'d'*40,
            'intent_sha256':intent['record_sha256'],'status':'retired'}))
        self.inventory=self.enterContext(mock.patch.object(cold.disks.disks,'inventory',return_value=[]))
        self.loop=self.enterContext(mock.patch.object(cold,'loops',return_value=[]))

    def retire(self):return self.inspector.retire_prepared_bootstrap('e'*40)

    def test_exact_retirement_and_retry_keep_small_records(self):
        self.prepare();result=self.retire()
        self.assertEqual(result['status'],'unused-bootstrap-retired')
        self.assertFalse(list(self.inspector.work.iterdir()))
        self.assertTrue((self.bundle.directory/'filesystem-bootstrap.json').is_file())
        self.assertEqual(self.retire(),result)

    def test_interrupted_unlink_resumes_exact_recorded_intent(self):
        self.prepare();real=records.syncdir;lost=True
        def interrupted(path):
            nonlocal lost
            if path==self.inspector.work and lost:
                lost=False;raise KeyboardInterrupt
            real(path)
        with mock.patch.object(records,'syncdir',side_effect=interrupted),self.assertRaises(KeyboardInterrupt):self.retire()
        self.assertFalse((self.inspector.work/'bootstrap-kernel').exists())
        self.assertEqual(records.read(self.bundle.directory/'bootstrap-cleanup-progress.json')['inflight'],'bootstrap-kernel')
        self.assertEqual(self.retire()['status'],'unused-bootstrap-retired')

    def test_live_or_renamed_inspector_boot_use_and_loops_refuse(self):
        self.prepare()
        for row in ({'domid':4,'config':{'c_info':{'name':'renamed','uuid':self.inspector.identity}}},
                    {'domid':4,'config':{'c_info':{'name':'other'},'b_info':{'kernel':str(self.inspector.work/'bootstrap-kernel')}}}):
            self.host.inventory.return_value=[row]
            with self.assertRaisesRegex(TransactionError,'live inspector'):self.retire()
        self.host.inventory.return_value=[];self.loop.return_value=['/dev/loop9']
        with self.assertRaisesRegex(TransactionError,'loop attachment'):self.retire()
        self.assertTrue((self.inspector.work/'bootstrap-kernel').exists())

    def test_renamed_backup_uuid_outage_marker_and_unknown_file_refuse(self):
        self.prepare();self.inventory.return_value=[{'lv_path':'/dev/vg0/renamed','lv_uuid':'backup-uuid'}]
        with self.assertRaisesRegex(TransactionError,'backup LV again'):self.retire()
        self.inventory.return_value=[]
        marker=self.bundle.directory/'outage-authorization.json';marker.write_text('{}')
        with self.assertRaisesRegex(TransactionError,'no outage'):self.retire()
        marker.unlink();(self.inspector.work/'unknown').write_text('keep')
        with self.assertRaisesRegex(TransactionError,'unknown inspector'):self.retire()

    def test_changed_bytes_and_reappeared_file_refuse(self):
        self.prepare();path=self.inspector.work/'bootstrap-kernel';original=path.read_bytes()
        path.write_bytes(b'x'*len(original))
        with self.assertRaisesRegex(TransactionError,'bytes changed'):self.retire()
        path.write_bytes(original);self.retire();path.write_bytes(original)
        with self.assertRaisesRegex(TransactionError,'reappeared'):self.retire()

    def test_unrecorded_missing_file_after_interruption_refuses_completion(self):
        self.prepare();real=records.syncdir
        def interrupted(path):
            if path==self.inspector.work:raise KeyboardInterrupt
            real(path)
        with mock.patch.object(records,'syncdir',side_effect=interrupted),self.assertRaises(KeyboardInterrupt):self.retire()
        (self.inspector.work/'bootstrap-initramfs').unlink()
        with self.assertRaisesRegex(TransactionError,'without removal intent'):self.retire()
        self.assertFalse((self.bundle.directory/'bootstrap-cleanup-complete.json').exists())

    def test_deleted_loop_backing_is_still_an_attachment(self):
        target=self.root/'bootstrap-kernel'
        backing=self.root/'loop7/loop/backing_file';backing.parent.mkdir(parents=True)
        backing.write_text(str(target)+' (deleted)\n')
        with mock.patch.object(cold.Path,'glob',return_value=[backing]):
            self.assertEqual(cold.loops(target),['/dev/loop7'])

    def test_native_dispatch_uses_the_saved_source_and_current_cleanup_engine(self):
        self.prepare()
        records.write(self.bundle.directory/'manifest.json',generations.seal({
            'kind':'klokast.router-cold-metadata.v1','box':'k001','operation_id':self.bundle.operation,
            'engine_commit':self.bundle.engine}))
        storage=mock.Mock(box='k001',base=self.storage.base)
        inspector=mock.Mock();inspector.retire_prepared_bootstrap.return_value={'status':'unused-bootstrap-retired'}
        output=io.StringIO()
        with mock.patch.object(executor.records,'Records',return_value=storage),\
                mock.patch.object(executor.native,'Native'),\
                mock.patch.object(executor.cold_backup,'Bundle',return_value=self.bundle) as selected,\
                mock.patch.object(executor.cold_filesystem,'Inspector',return_value=inspector),\
                contextlib.redirect_stdout(output):
            executor.main(['cold-retire-prepared-bootstrap','--box','k001','--operation-id',self.bundle.operation],'e'*40)
        selected.assert_called_once_with(storage,self.bundle.operation,self.bundle.engine)
        inspector.retire_prepared_bootstrap.assert_called_once_with('e'*40)
        self.assertEqual(json.loads(output.getvalue())['action'],'cold-retire-prepared-bootstrap')


if __name__=='__main__':unittest.main()
