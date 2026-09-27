"""The dom0 transport cannot qualify an incomplete or wrong-direction copy."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_copy_contract as contract
import router_copy_native as c
import router_generations as g
from router_transaction import TransactionError
from test_router_generations import generation
from test_router_transaction import request


class CopyNativeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.work=Path(temporary.name); (self.work/'copy').mkdir()
        old,new=generation('legacy'),generation()
        req={**request(),'engine_commit':new['engine_commit'],'old_sha256':old['record_sha256'],
             'candidate_sha256':new['record_sha256']}
        self.adapter=SimpleNamespace(work=self.work,request=req,pair={'old':old,'candidate':new},host=mock.Mock())
        self.job=contract.job(req,old,new,'e'*64)
        self.capsule={'inputs_sha256':'e'*64,'job_sha256':g.digest(self.job)}
        self.copy=c.Copy()
        patch=mock.patch.object(self.copy,'inputs',return_value=(self.capsule,self.job)); patch.start(); self.addCleanup(patch.stop)
        self.receipt={'kind':'klokast.router-state-copy.v1','complete':True,'operation':req['operation_id'],
            'request_sha256':self.job['forward']['request_sha256'],'source':old['disk']['uuid'],
            'destination':new['disk']['uuid'],'files':{},'recovered_clone':False}
        self.publish(self.receipt)

    def publish(self, receipt):
        receipt={**receipt,'receipt_sha256':g.digest(receipt)}
        value={'kind':'klokast.router-copy-phase.v1','phase':'forward',
            'operation_id':self.adapter.request['operation_id'],**self.capsule,'success':True,
            'copy_receipt_sha256':receipt['receipt_sha256']}
        for kind,record in (('private',receipt),('result',value)):
            (self.work/'copy'/('forward.'+kind+'.slot')).write_bytes(json.dumps(record).encode()+b'\0')

    def test_exact_complete_receipt_is_required_after_both_disks_detach(self):
        self.copy.verify_copy(self.adapter,'old','candidate',deadline=100)
        self.adapter.host.detached.assert_called_once()
        for change in ({'complete':False},{'source':'other-uuid'}, {'request_sha256':'0'*64}, {'operation':'0'*24}):
            self.publish({**self.receipt,**change})
            with self.assertRaises(TransactionError):
                self.copy.verify_copy(self.adapter,'old','candidate',deadline=100)

    def test_private_corruption_is_not_hidden_by_a_public_success_record(self):
        path=self.work/'copy/forward.private.slot'
        value=json.loads(path.read_bytes().split(b'\0')[0]); value['files']={'changed':'private-data'}
        path.write_bytes(json.dumps(value).encode()+b'\0')
        with self.assertRaises(TransactionError):
            self.copy.verify_copy(self.adapter,'old','candidate',deadline=100)

    def test_attached_disk_blocks_receipt_use(self):
        self.adapter.host.detached.side_effect=TransactionError('still attached')
        with self.assertRaises(TransactionError):
            self.copy.verify_copy(self.adapter,'old','candidate',deadline=100)

    def test_wrong_phase_or_operation_cannot_signal_completion(self):
        path=self.work/'copy/forward.result.slot'
        value=json.loads(path.read_bytes().split(b'\0')[0])
        for change in ({'phase':'reverse'},{'operation_id':'0'*24},{'job_sha256':'0'*64},{'success':1}):
            path.write_bytes(json.dumps({**value,**change}).encode()+b'\0')
            with self.assertRaises(TransactionError):
                self.copy.result(self.adapter,'forward')


if __name__ == '__main__':
    unittest.main()
