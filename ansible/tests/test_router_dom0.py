"""Exercise the real adapter and durable records across failures with fake Xen."""
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
        self.work = self.records.operation(self.request['operation_id'])
        self.xen = self.base / 'xen'
        self.xen.mkdir(mode=0o700); (self.xen / 'auto').mkdir(mode=0o700)
        r.write(self.work / 'request.json', self.request)
        for side, value in (('old', self.old), ('candidate', self.new)):
            r.atomic(self.work / (side + '.cfg'), g.configuration(value).encode())
        r.atomic(self.xen / 'router.cfg', g.configuration(self.old).encode())
        self.ready = {'kind':'klokast.router-readiness.v1', 'request_sha256':g.digest(self.request),
            'release_sha256':'4'*64, 'candidate_qualification_sha256':'5'*64,
            'compatibility_sha256':'6'*64, 'copy_qualification_sha256':'7'*64, 'gateway':'10.1.1.1'}
        r.write(self.work / 'readiness.json', self.ready)
        r.write(self.work / 'authorization.json', {'kind':'klokast.router-operation-authorization.v1',
            'request_sha256':g.digest(self.request),'readiness_sha256':g.digest(self.ready),
            'granted_at':int(time.time()),'expires_at':int(time.time())+600})
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

    def accept(self):
        r.write(self.work / 'acceptance.json', {'kind':'klokast.router-controller-acceptance.v1',
            'request_sha256':g.digest(self.request), 'candidate_sha256':self.request['candidate_sha256'],
            'evidence_sha256':'3'*64})

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

    def test_boot_recovery_does_not_require_controller_grant(self):
        self.records.persist(self.pending)
        (self.work/'authorization.json').unlink()
        self.host.live=None
        self.assertEqual(Transaction(self.request,self.adapter,self.records.pending()).recover(),'rolled-back')
        self.assertEqual(self.host.live,'old')


if __name__ == '__main__':
    unittest.main()
