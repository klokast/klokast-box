"""Exact copy-file retirement refuses live mappings and survives partial unlink."""
from pathlib import Path
import unittest
from unittest import mock

import test_router_copy_cache as cache_tests
import router_copy_native as native_copy
import router_records as records
from router_transaction import TransactionError


class CopyRetirementTests(unittest.TestCase):
    publish = cache_tests.CopyCacheTests.publish
    retire = cache_tests.CopyCacheTests.retire

    def setUp(self):
        cache_tests.CopyCacheTests.setUp(self)
        self.complete()
        self.proof = self.publish()

    def test_loss_after_unlink_reconciles_only_the_durable_inflight_file(self):
        target = self.directory / 'scratch.slot'
        unlink = Path.unlink
        def interrupted(path, *args, **kwargs):
            result = unlink(path, *args, **kwargs)
            if path == target:
                raise KeyboardInterrupt
            return result
        with mock.patch.object(Path, 'unlink', interrupted), self.assertRaises(KeyboardInterrupt):
            self.retire(self.proof)
        marker = records.read(self.directory / 'retirement.json')
        self.assertEqual(marker['inflight'], 'scratch.slot')
        self.assertEqual(marker['removed'], [])
        result = self.retire(self.proof)
        self.assertEqual(result['phase'], 'retired')
        self.assertEqual(result['removed'], [*native_copy.SLOTS, 'kernel', 'initramfs'])
        self.assertEqual(self.retire(self.proof), result)

    def test_missing_file_without_intent_and_changed_inode_refuse(self):
        path = self.directory / 'forward.private.slot'
        replacement = self.base / 'replacement'
        with replacement.open('wb') as stream:
            stream.truncate(native_copy.MIB)
        replacement.chmod(0o600)
        replacement.replace(path)
        with self.assertRaisesRegex(TransactionError, 'identity'):
            self.retire(self.proof)
        self.assertTrue((self.directory / 'scratch.slot').exists())
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.retire(self.proof)
        self.assertFalse((self.directory / 'retirement.json').exists())

    def test_live_copy_guest_or_loop_mapping_prevents_all_unlinks(self):
        self.host.inventory.return_value = [{'domid': 3, 'config': {'c_info': {
            'name': 'router-copy-' + self.request['operation_id'] + '-forward',
            'uuid': self.capsule['domains']['forward']}, 'b_info': {}}}]
        with self.assertRaisesRegex(TransactionError, 'not fenced'):
            self.retire(self.proof)
        self.host.inventory.return_value = [{'domid': 0}]
        with mock.patch.object(native_copy, 'loops', return_value=['/dev/loop23']):
            with self.assertRaisesRegex(TransactionError, 'loop mapping'):
                self.retire(self.proof)
        self.assertTrue((self.directory / 'scratch.slot').exists())
        self.assertFalse((self.directory / 'retirement.json').exists())

    def test_reappeared_file_and_changed_retirement_binding_refuse(self):
        result = self.retire(self.proof)
        (self.directory / 'scratch.slot').write_bytes(b'reappeared')
        with self.assertRaisesRegex(TransactionError, 'reappeared'):
            self.retire(self.proof)
        (self.directory / 'scratch.slot').unlink()
        result = {key: value for key, value in result.items() if key != 'record_sha256'}
        result['proof_sha256'] = '0' * 64
        records.write(self.directory / 'retirement.json', native_copy.generations.seal(result))
        with self.assertRaisesRegex(TransactionError, 'marker changed'):
            self.retire(self.proof)

    def test_unknown_namespace_and_boot_corruption_refuse_before_removal(self):
        unknown = self.directory / 'unknown.slot'
        unknown.write_bytes(b'unknown')
        with self.assertRaisesRegex(TransactionError, 'unexpected files'):
            self.retire(self.proof)
        unknown.unlink()
        (self.directory / 'kernel').write_bytes(b'changed')
        with self.assertRaisesRegex(TransactionError, 'size changed'):
            self.retire(self.proof)
        self.assertTrue((self.directory / 'scratch.slot').exists())

    def test_expired_action_and_corrupt_root_cache_do_not_delete_files(self):
        self.authorize.side_effect = TransactionError('grant expired')
        with self.assertRaisesRegex(TransactionError, 'grant expired'):
            self.retire(self.proof)
        self.assertTrue((self.directory / 'scratch.slot').exists())
        self.authorize.side_effect = None
        cached = records.read(self.cache)
        cached['record_sha256'] = '0' * 64
        records.write(self.cache, cached)
        with self.assertRaises(RuntimeError):
            self.retire(self.proof)

    def test_deleted_loop_backing_name_is_still_detected(self):
        backing = self.base / 'loop23/loop/backing_file'
        backing.parent.mkdir(parents=True)
        backing.write_text(str(self.directory / 'scratch.slot') + ' (deleted)\n')
        with mock.patch.object(native_copy, 'Path') as path:
            path.return_value.glob.return_value = [backing]
            self.assertEqual(native_copy.loops(self.directory / 'scratch.slot'), ['/dev/loop23'])


if __name__ == '__main__':
    unittest.main()
