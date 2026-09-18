"""Independent backup allocation, identity checks, and failure containment."""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('vm_disk_backup', ROOT / 'ansible/roles/vm-retained-data/files/vm_disk_backup.py')
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)


class LVM:
    def __init__(self, root):
        self.root, self.items, self.files = root, {}, {}
        self.free = 4 * 1024**3
        self.failure = None
        self.calls = []
        self.create('/dev/vg0/source', 16 * b.MIB, '')
        self.files['/dev/vg0/source'].write_bytes(b'synthetic old disk' + b'\0' * (16 * b.MIB - 18))
        # Keep the exact LV size even when fixture text changes.
        with self.files['/dev/vg0/source'].open('r+b') as stream: stream.truncate(16 * b.MIB)
        self.calls = []

    def volumes(self):
        return [{'lv_path': p, 'lv_tags': ','.join(v['tags'])} for p, v in self.items.items()]

    def volume(self, path):
        value = copy.deepcopy(self.items[path])
        if path.endswith('_snapshot'):
            if self.failure == 'overflow': value['data_percent'] = '80.01'
            if self.failure == 'invalid': value['attr'] = 'sri-I-s---'
            if self.failure == 'changed-uuid': value['uuid'] = '999999-9999-9999-9999-9999-9999-999999'
        return value

    def capacity(self, vg): return self.free, 4 * b.MIB

    def unmounted(self, volumes):
        if self.failure == 'mounted': raise b.BackupError('mounted')

    def unattached(self, volumes):
        if self.failure == 'attached': raise b.BackupError('attached')

    def create(self, path, size, tag, origin=None):
        self.calls.append(('create', path))
        n = len(self.items) + 1
        self.items[path] = {'path': path, 'uuid': str(n) * 6 + ('-' + str(n) * 4) * 5 + '-' + str(n) * 6,
                            'bytes': size, 'device': n, 'tags': [tag] if tag else [],
                            'attr': 'sri-a-s---' if origin else '-wi-a-----',
                            'origin_uuid': self.items[origin]['uuid'] if origin else '',
                            'segtype': 'snapshot' if origin else 'linear', 'data_percent': '0.01' if origin else ''}
        target = self.root / Path(path).name
        self.files[path] = target
        if origin:
            shutil.copyfile(self.files[origin], target)
            self.items[origin]['attr'] = 'owi-a-s---'
        else:
            with target.open('wb') as stream: stream.truncate(size)

    def readonly(self, path):
        self.calls.append(('readonly', path)); self.items[path]['attr'] = '-ri-a-----'

    def remove(self, path):
        self.calls.append(('remove', path)); self.files[path].unlink(); del self.items[path]
        self.items['/dev/vg0/source']['attr'] = '-wi-a-----'

    def open(self, volume, writable=False):
        if not writable and volume['path'].endswith('_snapshot'):
            # Mutate the source after the snapshot: backup must retain old data.
            with self.files['/dev/vg0/source'].open('r+b') as stream: stream.write(b'new live source')
            if self.failure == 'read-error': raise OSError('synthetic read failure')
            if self.failure == 'late-overflow': self.failure = 'overflow'
            if self.failure == 'late-uuid': self.failure = 'changed-uuid'
        if not writable and volume['path'].endswith('_disk') and self.failure == 'readback':
            with self.files[volume['path']].open('r+b') as stream: stream.write(b'corrupt')
        return self.files[volume['path']].open('r+b' if writable else 'rb', buffering=0)


class DiskBackup(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.work = self.root / 'records'; self.work.mkdir()
        self.native = LVM(self.root)
        self.request = {'kind': 'klokast.vm-disk-backup.v1', 'operation_id': 'a' * 24, 'box': 'boxa',
                        'engine_commit': 'b' * 40, 'source_evidence_sha256': 'c' * 64,
                        'source': {k: self.native.volume('/dev/vg0/source')[k] for k in ('path', 'uuid', 'bytes')},
                        'cow_bytes': 16 * b.MIB, 'requested_at': int(time.time())}
        self.old_hash = hashlib.sha256(self.native.files['/dev/vg0/source'].read_bytes()).hexdigest()
        p = patch.object(b, 'secure'); p.start(); self.addCleanup(p.stop)

    def execute(self): return b.make_backup(self.work, self.request, self.native)

    def test_snapshot_is_independent_sealed_and_not_adoption_evidence(self):
        result = self.execute()
        self.assertEqual(result['disk_sha256'], self.old_hash)
        self.assertEqual(hashlib.sha256(self.native.files[result['backup']['path']].read_bytes()).hexdigest(), self.old_hash)
        self.assertTrue(self.native.files['/dev/vg0/source'].read_bytes().startswith(b'new live source'))
        self.assertEqual(self.native.volume(result['backup']['path'])['attr'], '-ri-a-----')
        self.assertEqual(len(self.native.items), 2)
        self.assertTrue(result['independent_copy']); self.assertTrue(result['backup_readonly'])
        for key in ('restore_verified', 'source_freshness_verified', 'application_consistency_verified', 'adoption_accepted'):
            self.assertFalse(result[key])
        self.assertEqual(result['receipt_sha256'], b.digest({k: v for k, v in result.items() if k != 'receipt_sha256'}))
        with self.assertRaisesRegex(b.BackupError, 'already used'): self.execute()

    def test_closed_request_rejects_unsafe_values(self):
        for key, value in (('cow_bytes', True), ('cow_bytes', 15 * b.MIB), ('requested_at', 1.1),
                           ('engine_commit', 'main'), ('extra', 'unused')):
            request = dict(self.request); request[key] = value
            with self.subTest(key=key), self.assertRaises(b.BackupError): b.validate(request)
        for key, value in (('path', '/dev/vg0/../source'), ('bytes', True), ('uuid', 'unrecorded')):
            request = copy.deepcopy(self.request); request['source'][key] = value
            with self.subTest(key=key), self.assertRaises(b.BackupError): b.validate(request)

    def test_stale_source_mount_identity_capacity_fail_before_allocation(self):
        for failure in ('stale', 'future', 'mounted', 'source-uuid', 'capacity', 'extent', 'nonlinear'):
            with self.subTest(failure=failure):
                request = copy.deepcopy(self.request)
                native = copy.deepcopy(self.native)
                if failure == 'stale': self.request['requested_at'] -= 301
                if failure == 'future': self.request['requested_at'] += 60
                if failure == 'mounted': self.native.failure = 'mounted'
                if failure == 'source-uuid': self.native.items['/dev/vg0/source']['uuid'] = '2' * 6 + ('-' + '2' * 4) * 5 + '-' + '2' * 6
                if failure == 'capacity': self.native.free = 1024 * b.MIB
                if failure == 'extent': self.request['cow_bytes'] -= 512
                if failure == 'nonlinear': self.native.items['/dev/vg0/source']['segtype'] = 'thin'
                with self.assertRaises(b.BackupError): self.execute()
                self.assertEqual(self.native.calls, [])
                self.request, self.native = request, native

    def test_copy_failure_preserves_destination_and_removes_only_snapshot(self):
        self.native.failure = 'read-error'
        with self.assertRaises(OSError): self.execute()
        self.assertEqual(len(self.native.items), 2)
        self.assertFalse((self.work / 'result.json').exists())
        self.assertEqual([p for action, p in self.native.calls if action == 'remove'],
                         ['/dev/vg0/vmbackup_' + 'a' * 24 + '_snapshot'])

    def test_late_snapshot_exhaustion_blocks_copy_and_never_accepts(self):
        self.native.failure = 'late-overflow'
        with self.assertRaisesRegex(b.BackupError, 'capacity'): self.execute()
        self.assertFalse((self.work / 'result.json').exists())
        self.assertEqual(len(self.native.items), 2)

    def test_changed_snapshot_identity_cannot_be_removed(self):
        self.native.failure = 'late-uuid'
        with self.assertRaisesRegex(b.BackupError, 'identity changed'): self.execute()
        self.assertFalse(any(action == 'remove' for action, _ in self.native.calls))
        self.assertEqual(len(self.native.items), 3)

    def test_readback_corruption_preserves_unaccepted_backup(self):
        self.native.failure = 'readback'
        with self.assertRaisesRegex(b.BackupError, 'read-back differs'): self.execute()
        self.assertEqual(len(self.native.items), 2)
        self.assertFalse((self.work / 'result.json').exists())

    def test_existing_operation_allocation_is_not_reused(self):
        path = '/dev/vg0/vmbackup_' + 'a' * 24 + '_disk'
        self.native.create(path, 16 * b.MIB, 'unknown')
        self.native.calls.clear()
        with self.assertRaisesRegex(b.BackupError, 'already exist'): self.execute()
        self.assertEqual(self.native.calls, [])


if __name__ == '__main__': unittest.main()
