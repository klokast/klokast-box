"""Native cached copy evidence survives exact file retirement without new authority."""
import copy
import hashlib
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_copy_contract as contract
import router_copy_native as native_copy
import router_executor as executor
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_completion as completion_tests


class CopyCacheTests(unittest.TestCase):
    def setUp(self):
        completion_tests.CompletionTests.setUp(self)
        self.read = lambda: completion_tests.CompletionTests.read(self)
        self.complete = lambda *args, **kwargs: completion_tests.CompletionTests.complete(self, *args, **kwargs)
        self.directory = self.work / 'copy'
        self.directory.mkdir(mode=0o700)
        files = {}
        for name, size in native_copy.SLOTS.items():
            path = self.directory / name
            with path.open('wb') as stream:
                stream.truncate(size)  # Sparse isolated files; no 2 GiB physical allocation.
            path.chmod(0o600)
            info = path.stat()
            files[name] = {'device': info.st_dev, 'inode': info.st_ino, 'bytes': size}
        records.write(self.directory / 'allocation.json', {'kind': 'klokast.router-copy-allocation.v1',
            'transaction_sha256': generations.digest(self.request), 'files': files})
        boot = {}
        for name in ('kernel', 'initramfs'):
            path = self.directory / name
            path.write_bytes(('isolated-copy-' + name).encode())
            path.chmod(0o600)
            boot[name] = {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        job = contract.job(self.request, self.old, self.new, 'e' * 64)
        self.capsule = {'kind': 'klokast.router-copy-capsule.v2', 'operation_id': self.request['operation_id'],
            'engine_commit': self.request['engine_commit'], 'inputs_sha256': 'e' * 64,
            'transaction_sha256': generations.digest(self.request), 'job_sha256': generations.digest(job),
            'bootstrap': boot, 'domains': {'forward': '44444444-1111-4111-8111-111111111111',
                                          'reverse': '55555555-1111-4111-8111-111111111111'}}
        records.write(self.work / 'capsule.json', self.capsule)
        self.transport = native_copy.Copy()
        self.copy.slots = self.transport.slots
        self.host.inventory = mock.Mock(return_value=[{'domid': 0}])
        self.host.artifact = lambda item, **kwargs: executor.native.Native.artifact(self.host, item, **kwargs)
        self.cache = self.work / 'copy-proof.json'
        self.authorize = mock.Mock()

    def publish(self):
        return executor.cache_copy_proof(self.records, self.request['operation_id'], self.request['engine_commit'])

    def retire(self, proof):
        return self.transport.retire(self.adapter, proof=proof['record_sha256'],
            deadline=time.monotonic() + 30, authorize=self.authorize)

    def test_cached_forward_completion_and_map_survive_removal_of_all_raw_copy_files(self):
        before = self.complete()
        proof = self.publish()
        self.retire(proof)
        self.copy.verify_receipt.side_effect = AssertionError('cached proof must not read retired raw receipts')
        self.assertEqual(self.read(), before)
        status = executor.map_status(self.records)
        self.assertEqual(status['state_copy']['forward'], 'complete')
        self.assertEqual(status['state_copy']['reverse'], 'absent')
        self.assertEqual(self.publish(), proof)
        self.assertTrue((self.directory / 'allocation.json').exists())
        for name in [*native_copy.SLOTS, 'kernel', 'initramfs']:
            self.assertFalse((self.directory / name).exists())

    def test_changed_state_rollback_marker_survives_retirement(self):
        before = self.complete('rolled-back')
        proof = self.publish()
        self.assertTrue(proof['state_change_observed'])
        self.assertEqual(set(proof['copy_receipts']), {'forward', 'reverse'})
        self.retire(proof)
        self.copy.read_slot.side_effect = AssertionError('no private receipt bytes may be read after retirement')
        self.copy.verify_receipt.side_effect = AssertionError('no raw receipt read')
        self.assertEqual(self.read(), before)

    def test_corrupt_cache_changed_capsule_or_allocation_and_wrong_receipt_shape_refuse(self):
        self.complete()
        proof = self.publish()
        for change in ({'copy_receipts': {}}, {'state_change_observed': True},
                       {'request_sha256': '0' * 64}, {'completion_sha256': '0' * 64}):
            value = {key: item for key, item in proof.items() if key != 'record_sha256'}
            records.write(self.cache, generations.seal({**value, **change}))
            with self.assertRaisesRegex(TransactionError, 'cached copy proof differs'):
                self.read()
        records.write(self.cache, proof)
        capsule = records.read(self.work / 'capsule.json')
        records.write(self.work / 'capsule.json', {**capsule, 'inputs_sha256': '0' * 64})
        with self.assertRaisesRegex(TransactionError, 'cached copy proof differs'):
            self.read()
        records.write(self.work / 'capsule.json', capsule)
        allocation = records.read(self.directory / 'allocation.json')
        records.write(self.directory / 'allocation.json', {**allocation, 'transaction_sha256': '0' * 64})
        with self.assertRaisesRegex(TransactionError, 'cached copy proof differs'):
            self.read()

    def test_raw_copy_failure_or_missing_allocation_prevents_cache_publication(self):
        self.complete()
        self.copy.verify_receipt.side_effect = TransactionError('unverified native copy')
        with self.assertRaisesRegex(TransactionError, 'unverified native copy'):
            self.publish()
        self.assertFalse(self.cache.exists())
        self.copy.verify_receipt.side_effect = lambda backend, phase: '1' * 64
        (self.directory / 'scratch.slot').unlink()
        with self.assertRaises(FileNotFoundError):
            self.publish()
        self.assertFalse(self.cache.exists())

    def test_historical_cached_assignment_still_reports_the_live_current_pointer(self):
        before = self.complete()
        proof = self.publish()
        self.retire(proof)
        changed = {key: item for key, item in before['assignment'].items() if key != 'record_sha256'}
        changed['evidence_sha256'] = '7' * 64
        records.write(self.base / 'accepted.json', generations.seal(changed))
        read = self.read()
        self.assertEqual(read['assignment'], before['assignment'])
        self.assertNotEqual(read['current_assignment'], before['current_assignment'])
        self.assertEqual(read['copy_receipts'], before['copy_receipts'])


if __name__ == '__main__':
    unittest.main()
