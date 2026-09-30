"""Offline cleanup binds one stopped disk before it removes first-contact access."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'lib'))
import router_initial_finalization as finalization
import router_records as records
from router_transaction import TransactionError
from test_router_updates import ENGINE, release


class InitialFinalizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name) / 'data'
        self.base.mkdir(mode=0o700)
        for name in ('records','generations','operations'):
            (self.base / name).mkdir(mode=0o700)
        self.operation = 'a'*24
        self.work = self.base / 'operations' / self.operation
        self.work.mkdir(mode=0o700)
        self.final = self.work / 'finalization'
        self.final.mkdir(mode=0o700)
        for name,value in (('ROOT_UID',os.geteuid()),('parents',lambda path:None)):
            handle=patch.object(records,name,value)
            handle.start(); self.addCleanup(handle.stop)
        self.storage = records.Records('boxa',self.base)
        self.disk = {'path':'/dev/vg0/routergen_'+self.operation,'uuid':'exact-uuid','bytes':2147483648}
        self.release = release()
        self.source = {'kind':'klokast.router-initial-preparation.v1','box':'boxa',
            'operation_id':self.operation,'engine_commit':ENGINE,
            'inputs_sha256':self.release['inputs']['inputs_sha256'],
            'source_operation':'b'*24,'release_sha256':self.release['receipt_sha256']}
        self.preparation_job = {'kind':'synthetic-preparation'}
        self.prepared = {'prepared':{'accounts':{'dnsmasq_uid':65,'dnsmasq_gid':65,'tailscale_gid':103}},
                         'first_contact':{'kind':'synthetic-host-keys'}}
        self.enrollment = {'kind':'klokast.router-initial-enrollment-guest.v1',
            'box':'boxa','operation_id':self.operation,'machine_id':'nExactMachine'}
        self.current = finalization.generations.seal({
            'kind':'klokast.router-initial-installation.v1','box':'boxa','role':'router',
            'operation_id':self.operation,'engine_commit':ENGINE,
            'selection_sha256':'1'*64,'release_sha256':self.release['receipt_sha256'],
            'disk':self.disk,'stage':'enrolled','preparation_sha256':'2'*64,
            'enrollment_sha256':finalization.generations.digest(self.enrollment),
            'machine_id':'nExactMachine','generation_sha256':None})
        self.xen = {'uuid':'8c681f14-92dd-484d-84cf-82b7ca8c6a3d','memory':512,'vcpus':1,
                    'vif':['bridge=br-bak,mac=00:16:3e:50:00:04']}
        self.boot_request = {'xen':self.xen}
        self.boot_intent = {'boot':{
            'kernel':{'path':'/tmp/fixture-kernel'},'initramfs':{'path':'/tmp/fixture-initramfs'}}}
        self.job = finalization.job_for('boxa',self.operation,self.source,self.preparation_job,
                                        self.prepared,self.release,self.enrollment)
        self.request = {'kind':'klokast.router-initial-finalization.v1','box':'boxa',
            'operation_id':self.operation,'engine_commit':ENGINE,
            'source_operation':self.source['source_operation'],
            'inputs_sha256':self.source['inputs_sha256'],
            'source_request_sha256':finalization.generations.digest(self.source),
            'release_sha256':self.release['receipt_sha256'],
            'enrollment_sha256':self.current['enrollment_sha256'],
            'job_sha256':finalization.generations.digest(self.job),
            'bootstrap':{name:{'bytes':100,'sha256':'3'*64} for name in ('kernel','initramfs')}}
        records.write(self.final / 'request.json',self.request)
        records.write(self.final / 'job.json',self.job)
        self.grant = {'kind':'klokast.router-initial-stop-grant.v1','engine_commit':ENGINE,
            'request_sha256':finalization.generations.digest(self.request),
            'installation_sha256':self.current['record_sha256'],
            'granted_at':1000,'expires_at':1900}
        records.write(self.final / 'stop-grant.json',self.grant)

    def test_request_binds_exact_frozen_source_and_enrolled_guest(self):
        self.assertEqual(finalization.request(self.request,self.job,self.source,self.release,
            self.current,'boxa',self.operation,ENGINE,self.job),self.request)
        changed = {**self.job,'enrolled_guest':{'machine_id':'nOther'}}
        with self.assertRaisesRegex(TransactionError,'enrolled installation'):
            finalization.request({**self.request,'job_sha256':finalization.generations.digest(changed)},
                changed,self.source,self.release,self.current,'boxa',self.operation,ENGINE,self.job)
        with self.assertRaisesRegex(TransactionError,'grant is stale'):
            finalization.grant({**self.grant,'expires_at':1000},
                (self.grant['request_sha256'],self.current['record_sha256']),'stop',ENGINE,1000)

    def test_stop_intent_is_durable_before_guest_shutdown_and_retry_is_exact(self):
        context = (self.work,self.current,self.source,self.preparation_job,self.prepared,
                   self.release,self.boot_request,self.boot_intent,self.enrollment)
        host = Mock()
        def stopped(*args,**kwargs):
            intent = records.read(self.final / 'stop-intent.json')
            self.assertEqual(intent['disk'],self.disk)
            self.assertEqual(intent['enrollment_sha256'],self.current['enrollment_sha256'])
        host.stop_initial.side_effect = stopped
        with patch.object(finalization,'context',return_value=context), \
                patch.object(finalization.native,'Native',return_value=host), \
                patch.object(finalization.disks,'verify',return_value=self.disk), \
                patch.object(finalization.time,'time',return_value=1001):
            first = finalization.stop(self.storage,self.operation,ENGINE)
            self.assertEqual(first['status'],'stopped-for-offline-finalization')
            self.assertEqual(finalization.stop(self.storage,self.operation,ENGINE),first)
        self.assertEqual(host.stop_initial.call_count,2)
        self.assertEqual(records.read(self.final / 'stop-result.json'),first)

    def test_stop_refuses_expired_grant_before_shutdown(self):
        context = (self.work,self.current,self.source,self.preparation_job,self.prepared,
                   self.release,self.boot_request,self.boot_intent,self.enrollment)
        records.write(self.final / 'stop-grant.json',{**self.grant,'expires_at':1000})
        host = Mock()
        with patch.object(finalization,'context',return_value=context), \
                patch.object(finalization.native,'Native',return_value=host), \
                patch.object(finalization.time,'time',return_value=1001):
            with self.assertRaisesRegex(TransactionError,'grant is stale'):
                finalization.stop(self.storage,self.operation,ENGINE)
        host.stop_initial.assert_not_called()
        self.assertFalse((self.final / 'stop-intent.json').exists())


if __name__ == '__main__':
    unittest.main()
