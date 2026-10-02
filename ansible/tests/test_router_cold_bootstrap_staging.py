"""Interrupted native receivers are fenced before exact staging retirement."""
import base64
import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock

import router_cold_filesystem as cold
import router_generations as generations
import router_records as records
import router_executor as executor
from router_transaction import TransactionError
import test_router_cold_backup as fixtures


class BootstrapStagingTests(unittest.TestCase):
    setUp = fixtures.ColdBundleTests.setUp

    def prepare(self, *, large=False):
        self.host.running = True
        self.host.inventory = mock.Mock(return_value=[])
        self.inventory = self.enterContext(mock.patch.object(cold.disks.disks, 'inventory', return_value=[]))
        self.loop = self.enterContext(mock.patch.object(cold, 'loops', return_value=[]))
        self.staging = cold.BootstrapStaging(self.bundle)
        self.payloads = {'kernel': b'kernel' if not large else b'x' * (cold.CHUNK + 3), 'initramfs': b'initramfs'}
        capsule = generations.seal({'kind': 'klokast.router-cold-filesystem-bootstrap.v1',
            'box': self.storage.box, 'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
            'inputs_sha256': 'a'*64, 'guest_sha256': 'b'*64,
            'boot': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in self.payloads.items()}})
        parts = {name: [{'name': f'part-{index:04d}', 'bytes': len(data[offset:offset+cold.CHUNK]),
            'sha256': hashlib.sha256(data[offset:offset+cold.CHUNK]).hexdigest()}
            for index, offset in enumerate(range(0, len(data), cold.CHUNK))] for name, data in self.payloads.items()}
        self.request = generations.seal({'kind': 'klokast.router-cold-bootstrap-transfer.v1', 'capsule': capsule, 'parts': parts})
        self.staging.stage(self.request)

    def body(self, name='kernel', index=0):
        return {'artifact': name, 'part': index, 'data': base64.b64encode(
            self.payloads[name][index*cold.CHUNK:(index+1)*cold.CHUNK]).decode()}

    def retire(self):
        return self.staging.retire('d'*40)

    def test_complete_transfer_closes_receiver_and_preserves_legacy_capsule(self):
        self.prepare(large=True)
        with self.assertRaisesRegex(TransactionError, 'incomplete'): self.staging.finish()
        for name in ('kernel', 'initramfs'):
            for index in range(len(self.request['parts'][name])):
                self.staging.receive(self.body(name, index))
        self.assertEqual(self.staging.finish()['status'], 'staged')
        self.assertEqual(records.read(self.bundle.directory/'filesystem-bootstrap.json'), self.request['capsule'])
        with self.assertRaisesRegex(TransactionError, 'phase is closed'): self.staging.receive(self.body())
        result = self.retire()
        self.assertEqual(result['bytes_reclaimed'], sum(map(len, self.payloads.values())))
        self.assertEqual(self.retire(), result)
        self.assertEqual(records.read(self.staging.state_path)['phase'], 'retired')
        with self.assertRaisesRegex(TransactionError, 'phase is closed'): self.staging.receive(self.body())
        self.assertTrue(self.staging.intent_path.exists())

    def test_partial_payload_is_owned_and_retired_after_writer_lock_released(self):
        self.prepare(); real = os.fdopen
        class BrokenWriter:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def write(self, data):
                self.stream.write(data[:3]); self.stream.flush()
                raise KeyboardInterrupt
        def opened(fd, mode, **kwargs):
            stream = real(fd, mode, **kwargs)
            target = os.readlink('/proc/self/fd/' + str(fd))
            return BrokenWriter(stream) if mode == 'wb' and target.endswith('/bootstrap-kernel') else stream
        with mock.patch.object(cold.os, 'fdopen', side_effect=opened), self.assertRaises(KeyboardInterrupt):
            self.staging.receive(self.body())
        self.assertEqual(records.read(self.staging.state_path)['files']['kernel']['inflight'], 0)
        with self.assertRaisesRegex(TransactionError, 'interrupted'): self.staging.receive(self.body())
        self.assertEqual(self.retire()['bytes_reclaimed'], 3)
        with self.assertRaisesRegex(TransactionError, 'phase is closed'): self.staging.receive(self.body('initramfs'))

    def test_creation_intent_empty_orphan_and_uninitialized_ledger_can_retire(self):
        self.prepare(); real = self.staging.save
        def interrupted(state):
            if state['files'].get('kernel', {}).get('inode') is not None: raise KeyboardInterrupt
            real(state)
        with mock.patch.object(self.staging, 'save', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.staging.receive(self.body())
        self.assertEqual((self.staging.work/'bootstrap-kernel').stat().st_size, 0)
        self.assertEqual(self.retire()['bytes_reclaimed'], 0)

    def test_intent_before_ledger_interruption_and_unknown_bytes_refuse(self):
        self.prepare(); self.staging.state_path.unlink(); self.staging.work.rmdir()
        self.assertEqual(self.retire()['bytes_reclaimed'], 0)
        self.assertEqual(records.read(self.staging.state_path)['phase'], 'retired')

    def test_missing_owned_file_reappeared_file_and_changed_plan_refuse(self):
        self.prepare(); self.staging.receive(self.body())
        path = self.staging.work/'bootstrap-kernel'; path.unlink()
        with self.assertRaises(FileNotFoundError): self.retire()
        self.assertEqual(records.read(self.staging.state_path)['phase'], 'retiring')
        with self.assertRaisesRegex(TransactionError, 'phase is closed'): self.staging.receive(self.body())

    def test_lost_unlink_result_recovers_but_reappearance_refuses(self):
        self.prepare(); self.staging.receive(self.body()); real = records.syncdir
        def interrupted(path):
            if path == self.staging.work: raise KeyboardInterrupt
            real(path)
        with mock.patch.object(records, 'syncdir', side_effect=interrupted), self.assertRaises(KeyboardInterrupt): self.retire()
        self.assertEqual(self.retire()['bytes_reclaimed'], len(self.payloads['kernel']))
        (self.staging.work/'bootstrap-kernel').write_bytes(b'kernel')
        with self.assertRaisesRegex(TransactionError, 'reappeared'): self.retire()

    def test_changed_bytes_after_removal_intent_refuse(self):
        self.prepare(); self.staging.receive(self.body())
        path = self.staging.work/'bootstrap-kernel'; real = Path.unlink
        def refused(target, *args, **kwargs):
            if target == path: raise KeyboardInterrupt
            return real(target, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', refused), self.assertRaises(KeyboardInterrupt): self.retire()
        path.write_bytes(b'xxxxxx')
        with self.assertRaisesRegex(TransactionError, 'changed after retirement plan'): self.retire()
        self.assertTrue(path.exists())

    def test_unknown_resources_backups_guests_loops_and_metadata_refuse(self):
        self.prepare(); self.staging.receive(self.body()); path = self.staging.work/'bootstrap-kernel'
        unknown = self.staging.work/'unknown'; unknown.write_bytes(b'keep')
        with self.assertRaisesRegex(TransactionError, 'unknown resources'): self.retire()
        unknown.unlink()
        backup = cold.disks.DiskBackup(self.bundle)
        self.inventory.return_value = [{'lv_path': '/dev/vg0/renamed', 'lv_tags': backup.tag}]
        with self.assertRaisesRegex(TransactionError, 'backup allocation'): self.retire()
        self.inventory.return_value = []
        self.host.inventory.return_value = [{'config': {'c_info': {'name': 'renamed'}, 'b_info': {'kernel': str(path)}}}]
        with self.assertRaisesRegex(TransactionError, 'live inspector'): self.retire()
        self.host.inventory.return_value = []; self.loop.return_value = ['/dev/loop0']
        with self.assertRaisesRegex(TransactionError, 'loop attachment'): self.retire()
        self.loop.return_value = []; records.write(self.bundle.directory/'manifest.json', {'keep': True})
        with self.assertRaisesRegex(TransactionError, 'used or unknown'): self.retire()
        self.assertTrue(path.exists())

    def test_bad_parts_unknown_destination_and_changed_original_refuse(self):
        self.prepare()
        body = self.body(); body['artifact'] = '../../bad'
        with self.assertRaisesRegex(TransactionError, 'bounded part'): self.staging.receive(body)
        body = self.body(); body['data'] = base64.b64encode(b'changed').decode()
        with self.assertRaisesRegex(TransactionError, 'differs'): self.staging.receive(body)
        self.host.running = False
        with self.assertRaisesRegex(TransactionError, 'running legacy'): self.retire()
        self.assertFalse((self.staging.work/'bootstrap-kernel').exists())

    def test_native_lock_blocks_retirement_and_payload_writer(self):
        self.prepare()
        with self.storage.lock():
            with self.assertRaises(records.LockBusy): self.retire()
            with self.assertRaises(records.LockBusy): self.staging.receive(self.body())
        self.assertEqual(self.retire()['bytes_reclaimed'], 0)

    def test_nonempty_file_without_recorded_inode_is_not_owned(self):
        self.prepare()
        state = records.read(self.staging.state_path)
        state['files']['kernel'] = {'device': None, 'inode': None, 'next_part': 0, 'inflight': None}
        self.staging.save(state)
        path = self.staging.work/'bootstrap-kernel'; path.write_bytes(b'unknown'); path.chmod(0o600)
        with self.assertRaisesRegex(TransactionError, 'unsafe type, size'): self.retire()
        self.assertTrue(path.exists())

    def test_metadata_capture_cannot_use_partial_or_retired_staging(self):
        self.prepare()
        with self.assertRaisesRegex(TransactionError, 'completed bootstrap receiver'):
            self.bundle.capture(self.generation['record_sha256'])
        self.staging.receive(self.body()); self.staging.receive(self.body('initramfs'))
        self.staging.finish()
        self.assertEqual(self.bundle.capture(self.generation['record_sha256'])['disk_included'], False)
        with self.assertRaisesRegex(TransactionError, 'used or unknown'): self.retire()

    def test_changed_whole_hash_refuses_publication_even_with_valid_parts(self):
        self.prepare()
        intent = records.read(self.staging.intent_path)
        intent['request']['capsule']['boot']['kernel']['sha256'] = 'f'*64
        intent['request']['capsule'] = generations.seal({k: v for k, v in intent['request']['capsule'].items() if k != 'record_sha256'})
        intent['request'] = generations.seal({k: v for k, v in intent['request'].items() if k != 'record_sha256'})
        intent = generations.seal({k: v for k, v in intent.items() if k != 'record_sha256'})
        records.write(self.staging.intent_path, intent)
        state = records.read(self.staging.state_path); state['intent_sha256'] = intent['record_sha256']; self.staging.save(state)
        self.staging.receive(self.body()); self.staging.receive(self.body('initramfs'))
        with self.assertRaisesRegex(TransactionError, 'assembled bytes differ'): self.staging.finish()
        self.assertFalse((self.bundle.directory/'filesystem-bootstrap.json').exists())

    def test_root_dispatch_cleanup_selects_saved_source_and_current_engine(self):
        self.prepare(); staging = mock.Mock(); staging.retire.return_value = {'status': 'unused-staging-retired'}
        output = io.StringIO()
        with mock.patch.object(executor.records, 'Records', return_value=self.storage), \
                mock.patch.object(executor.native, 'Native'), \
                mock.patch.object(executor.cold_backup, 'Bundle', return_value=self.bundle) as bundle, \
                mock.patch.object(executor.cold_filesystem, 'BootstrapStaging', return_value=staging), contextlib.redirect_stdout(output):
            executor.main(['cold-bootstrap-retire-staging', '--box', 'k001', '--operation-id', self.bundle.operation], 'd'*40)
        bundle.assert_called_once_with(self.storage, self.bundle.operation, self.bundle.engine)
        staging.retire.assert_called_once_with('d'*40)
        self.assertEqual(json.loads(output.getvalue())['engine_commit'], 'd'*40)

    def test_root_receiver_consumes_stdin_without_printing_payload(self):
        self.prepare(); staging = mock.Mock(); staging.receive.return_value = {'status': 'part-received'}
        body = self.body(); output = io.StringIO()
        with mock.patch.object(executor.records, 'Records', return_value=self.storage), \
                mock.patch.object(executor.native, 'Native'), \
                mock.patch.object(executor.cold_backup, 'Bundle', return_value=self.bundle), \
                mock.patch.object(executor.cold_filesystem, 'BootstrapStaging', return_value=staging), \
                mock.patch.object(executor.sys, 'stdin', io.StringIO(json.dumps(body))), contextlib.redirect_stdout(output):
            executor.main(['cold-bootstrap-receive', '--box', 'k001', '--operation-id', self.bundle.operation], self.bundle.engine)
        staging.receive.assert_called_once_with(body)
        self.assertNotIn(body['data'], output.getvalue())


if __name__ == '__main__': unittest.main()
