"""Router-only recovery preserves the observed non-router Xen guest set."""
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_identity as identity
import router_cold_recovery as recovery
from router_transaction import TransactionError
import test_router_cold_backup as fixtures


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.ColdBundleTests.setUp
    capture_bundle = fixtures.ColdBundleTests.capture

    def prepare(self):
        metadata = self.capture_bundle()
        guest = {'BackendState': 'Running', 'Self': {
            'ID': 'test-device', 'HostName': 'boxa-router', 'Online': True}}
        controller = {'BackendState': 'Running', 'Peer': {'peer': {
            'ID': 'test-device', 'HostName': 'boxa-router', 'Online': True}}}
        proof = identity.create('boxa', self.bundle.operation, self.bundle.engine,
            metadata['record_sha256'], self.generation['record_sha256'],
            guest, controller, int(time.time()))
        self.host.running = True
        identity.Identity(self.bundle).stage(proof)
        self.domains = [
            {'domid': 0},
            {'domid': 4, 'config': {'c_info': {'name': 'router',
                'uuid': self.generation['xen']['uuid']}}},
            {'domid': 1, 'config': {'c_info': {'name': 'bak',
                'uuid': '22222222-1111-4111-8111-111111111111'}}},
            {'domid': 3, 'config': {'c_info': {'name': 'ops',
                'uuid': '33333333-1111-4111-8111-111111111111'}}},
        ]
        self.host.inventory = Mock(side_effect=lambda **kwargs: self.domains)
        self.baseline = recovery.Baseline(self.bundle)

    def test_capture_and_restoration_accept_same_other_guests(self):
        self.prepare()
        value = self.baseline.capture()
        self.assertEqual(set(value['domains']), {'bak', 'ops'})
        self.assertEqual(self.baseline.capture(), value)
        with patch.object(self.storage, 'cold_test', return_value={
                'operation_id': self.bundle.operation, 'phase': 'restoring'}):
            self.assertEqual(self.baseline.verify_restored(), value)

    def test_changed_guest_uuid_or_new_guest_refuses_recovery(self):
        self.prepare()
        self.baseline.capture()
        with patch.object(self.storage, 'cold_test', return_value={
                'operation_id': self.bundle.operation, 'phase': 'restoring'}):
            self.domains[-1]['config']['c_info']['uuid'] = '44444444-1111-4111-8111-111111111111'
            with self.assertRaisesRegex(TransactionError, 'changed the running'):
                self.baseline.verify_restored()
            self.domains[-1]['config']['c_info']['uuid'] = '33333333-1111-4111-8111-111111111111'
            self.domains.append({'domid': 5, 'config': {'c_info': {
                'name': 'iot', 'uuid': '55555555-1111-4111-8111-111111111111'}}})
            with self.assertRaisesRegex(TransactionError, 'changed the running'):
                self.baseline.verify_restored()

    def test_guest_change_before_stop_refuses_baseline_retry(self):
        self.prepare()
        self.baseline.capture()
        self.domains.pop()
        with self.assertRaisesRegex(TransactionError, 'changed Xen guests'):
            self.baseline.capture()


if __name__ == '__main__':
    unittest.main()
