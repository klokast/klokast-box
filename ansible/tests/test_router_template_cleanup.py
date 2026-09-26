"""Failed template cleanup leaves unknown and live Xen resources untouched."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]


def helper():
    path = REPO / 'ansible/roles/router-alpine-rootfs/files/router-template-cleanup-dom0'
    loader = importlib.machinery.SourceFileLoader('router_template_cleanup_dom0', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    value = importlib.util.module_from_spec(spec)
    loader.exec_module(value)
    return value


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.module = helper()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.operation = 'a' * 24
        self.work = self.base / self.operation
        self.work.mkdir(mode=0o700)
        self.record = {'stage': 'detached', 'operation_id': self.operation,
                       'domains': {mode: 'router-' + mode + '-' + self.operation
                                   for mode in ('build', 'test', 'openrc')},
                       'uuids': {mode: '11111111-1111-4111-8111-111111111111'
                                 for mode in ('build', 'test', 'openrc')}}
        (self.work / 'lifecycle.json').write_text(json.dumps(self.record))
        for name in ('os.slot', 'test.slot', 'kernel.slot', 'result.slot'):
            (self.work / name).write_bytes(b'opaque')
        self.base_patch = patch.object(self.module, 'BASE', self.base)
        self.safe_patch = patch.object(self.module, 'safe_dir')
        self.file_patch = patch.object(self.module, 'safe_file', side_effect=lambda path: path.lstat())
        self.domain_patch = patch.object(self.module, 'domains', return_value={('Domain-0', None)})
        self.loop_patch = patch.object(self.module, 'attached', return_value=[])
        for patcher in (self.base_patch, self.safe_patch, self.file_patch,
                        self.domain_patch, self.loop_patch):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_exact_failed_operation_reclaims_only_its_disks(self):
        marker = self.work / 'diagnostic.log'
        marker.write_text('keep')
        other = self.base / ('b' * 24)
        other.mkdir()
        (other / 'os.slot').write_text('keep')
        result = self.module.reclaim(self.work, self.operation)
        self.assertEqual(result['bytes_reclaimed'], 6 * 4)
        self.assertEqual(result['removed'], ['os.slot', 'test.slot', 'kernel.slot', 'result.slot'])
        self.assertEqual(marker.read_text(), 'keep')
        self.assertEqual((other / 'os.slot').read_text(), 'keep')
        self.assertEqual(json.loads((self.work / 'lifecycle.json').read_text())['stage'], 'storage-reclaimed')
        self.assertEqual(self.module.reclaim(self.work, self.operation)['removed'], [])

    def test_candidate_or_running_guest_blocks_every_unlink(self):
        (self.work / 'candidate.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'qualified'):
            self.module.reclaim(self.work, self.operation)
        (self.work / 'candidate.json').unlink()
        with patch.object(self.module, 'domains', return_value={(self.record['domains']['test'],
                    self.record['uuids']['test'])}):
            with self.assertRaisesRegex(RuntimeError, 'domain still exists'):
                self.module.reclaim(self.work, self.operation)
        self.assertTrue((self.work / 'os.slot').exists())

    def test_attached_disk_or_changed_record_blocks_every_unlink(self):
        with patch.object(self.module, 'attached', return_value=['/dev/loop7']):
            with self.assertRaisesRegex(RuntimeError, 'loop attachment'):
                self.module.reclaim(self.work, self.operation)
        self.record['stage'] = 'allocated'
        (self.work / 'lifecycle.json').write_text(json.dumps(self.record))
        with self.assertRaisesRegex(RuntimeError, 'detached operation'):
            self.module.reclaim(self.work, self.operation)
        self.assertTrue((self.work / 'os.slot').exists())

    def test_symlink_blob_is_unsafe(self):
        (self.work / 'os.slot').unlink()
        (self.work / 'os.slot').symlink_to(self.work / 'test.slot')
        self.file_patch.stop()
        with self.assertRaisesRegex(RuntimeError, 'unsafe'):
            self.module.reclaim(self.work, self.operation)
        self.assertTrue((self.work / 'test.slot').exists())


if __name__ == '__main__':
    unittest.main()
