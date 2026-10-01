"""The original legacy Tailnet identity needs two live views before stop."""
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_identity as identity
import router_generation_device as devices
import router_records as records
from router_transaction import TransactionError
import test_router_cold_backup as fixtures


class IdentityTests(unittest.TestCase):
    setUp = fixtures.ColdBundleTests.setUp
    capture = fixtures.ColdBundleTests.capture

    def statuses(self, machine='test-device'):
        guest = {'BackendState': 'Running', 'Self': {
            'ID': machine, 'HostName': 'boxa-router', 'Online': True}}
        controller = {'BackendState': 'Running', 'Peer': {'peer-key': {
            'ID': machine, 'HostName': 'boxa-router', 'Online': True}}}
        return guest, controller

    def proof(self, metadata, machine='test-device', observed_at=None):
        guest, controller = self.statuses(machine)
        return identity.create('boxa', self.bundle.operation, self.bundle.engine,
            metadata['record_sha256'], self.generation['record_sha256'], guest, controller,
            int(time.time()) if observed_at is None else observed_at)

    def test_two_live_views_and_existing_device_bind_original_identity(self):
        metadata = self.capture()
        value = self.proof(metadata)
        helper = identity.Identity(self.bundle)
        self.host.running = True
        self.assertEqual(helper.stage(value), value)
        self.host.running = False
        self.assertEqual(helper.verify(), value)
        self.assertEqual(records.read(helper.path)['machine_id'], 'test-device')

    def test_legacy_source_without_device_record_still_requires_live_peer_proof(self):
        devices.path(self.storage, self.generation['record_sha256']).unlink()
        metadata = self.capture()
        self.assertNotIn('device.json', metadata['files'])
        guest, controller = self.statuses('new-known-id')
        value = identity.create('boxa', self.bundle.operation, self.bundle.engine,
            metadata['record_sha256'], self.generation['record_sha256'], guest, controller,
            int(time.time()))
        self.host.running = True
        self.assertEqual(identity.Identity(self.bundle).stage(value), value)

    def test_mismatched_or_duplicate_controller_peer_refuses(self):
        metadata = self.capture()
        guest, controller = self.statuses()
        controller['Peer']['peer-key']['ID'] = 'other-id'
        with self.assertRaisesRegex(TransactionError, 'differs from the controller'):
            identity.create('boxa', self.bundle.operation, self.bundle.engine,
                metadata['record_sha256'], self.generation['record_sha256'], guest, controller,
                int(time.time()))
        controller['Peer']['peer-key']['ID'] = 'test-device'
        controller['Peer']['second'] = dict(controller['Peer']['peer-key'])
        with self.assertRaisesRegex(TransactionError, 'differs from the controller'):
            identity.create('boxa', self.bundle.operation, self.bundle.engine,
                metadata['record_sha256'], self.generation['record_sha256'], guest, controller,
                int(time.time()))

    def test_stage_requires_fresh_live_original_before_fence(self):
        metadata = self.capture()
        value = self.proof(metadata, observed_at=int(time.time()) - 901)
        helper = identity.Identity(self.bundle)
        self.host.running = True
        with self.assertRaisesRegex(TransactionError, 'live accepted source'):
            helper.stage(value)
        self.assertFalse(helper.path.exists())
        fresh = self.proof(metadata)
        self.host.running = False
        with self.assertRaisesRegex(TransactionError, 'live accepted source'):
            helper.stage(fresh)
        self.assertFalse(helper.path.exists())

    def test_protected_device_mismatch_refuses_stage(self):
        metadata = self.capture()
        value = self.proof(metadata, machine='other-device')
        self.host.running = True
        with self.assertRaisesRegex(TransactionError, 'differs from its protected device'):
            identity.Identity(self.bundle).stage(value)


if __name__ == '__main__':
    unittest.main()
