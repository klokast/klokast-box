"""A cold supervisor request binds every pre-stop K001 recovery input."""
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_supervisor as supervisor
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_cold_window as fixtures


class RequestTests(unittest.TestCase):
    setUp = fixtures.WindowTests.setUp
    capture = fixtures.WindowTests.capture
    prepare_window = fixtures.WindowTests.prepare
    command = fixtures.WindowTests.command

    def prepare(self):
        self.prepare_window()
        self.window.marker.unlink()
        self.host.running = True
        self.window.backup.save(self.disk_record, stage='allocated', source_sha256=None)
        metadata, generation = self.bundle.verify()
        identity = records.read(self.bundle.directory / 'original-identity.json')
        baseline = records.read(self.bundle.directory / 'dependent-baseline.json')
        self.request = supervisor.Request(self.bundle)
        now = int(time.time())
        value = generations.seal({'kind': 'klokast.router-cold-supervised-request.v1',
            'box': 'boxa', 'operation_id': self.bundle.operation,
            'engine_commit': self.bundle.engine,
            'metadata_sha256': metadata['record_sha256'],
            'generation_sha256': generation['record_sha256'],
            'identity_sha256': identity['record_sha256'],
            'baseline_sha256': baseline['record_sha256'],
            'backup_uuid': 'backup-uuid',
            'bootstrap_sha256': self.capsule['record_sha256'],
            'original_xen_uuid': generation['xen']['uuid'],
            'initial_operation': self.initial, 'issued_at': now,
            'expires_at': now + 3600})
        records.write(self.request.path, value)
        return value, now

    def test_request_binds_prepared_live_source_without_arming_fence(self):
        value, now = self.prepare()
        self.assertEqual(self.request.verify(now=now), value)
        self.assertFalse(self.window.marker.exists())

    def test_stale_or_changed_backup_refuses_request(self):
        value, now = self.prepare()
        with self.assertRaisesRegex(TransactionError, 'stale'):
            self.request.verify(now=now + 901)
        self.backup_row['lv_uuid'] = 'foreign'
        with self.assertRaisesRegex(TransactionError, 'identity'):
            self.request.verify(now=now)

    def test_changed_running_guest_set_refuses_request(self):
        _, now = self.prepare()
        self.host.inventory.return_value.pop()
        with self.assertRaisesRegex(TransactionError, 'unchanged running original'):
            self.request.verify(now=now)


if __name__ == '__main__':
    unittest.main()
