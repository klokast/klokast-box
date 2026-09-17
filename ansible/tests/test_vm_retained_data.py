"""Data-copy integrity and refusal tests; no Platform disks are used here."""
import copy
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'roles/vm-retained-data/files/retained_data.py'
spec = importlib.util.spec_from_file_location('retained_data', PATH)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class RetainedData(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target = self.root / 'source', self.root / 'target'
        self.source.mkdir()
        self.target.mkdir()
        self.library = self.source / 'srv/app/library'
        self.library.mkdir(parents=True)
        (self.library / 'file').write_text('retained data')
        self.runtime = {'uid': os.getuid(), 'gid': os.getgid(), 'subuid': [[200000, 65536]], 'subgid': [[200000, 65536]]}
        self.request = {'kind': 'klokast.vm-retained-copy.v1', 'operation_id': 'a' * 24,
                        'source_uuid': '11111111-1111-1111-1111-111111111111',
                        'destination_uuid': '22222222-2222-2222-2222-222222222222',
                        'runtime': self.runtime, 'entries': [{'key': 'library', 'source': 'srv/app/library'}]}
        self.deadline = time.monotonic() + 30

    def copy(self):
        with patch.object(d, 'SOURCE', self.source), patch.object(d, 'TARGET', self.target), \
                patch.object(d, 'environment'), patch.object(d, 'check_mounts'), \
                patch.object(d, 'runtime_identity', return_value=self.runtime):
            return d.copy(self.request, self.deadline)

    def test_invalid_mappings_and_contracts_cannot_write(self):
        for change in (lambda r: r.update(extra=True), lambda r: r.update(operation_id='../../oops'),
                       lambda r: r.update(source_uuid=r['destination_uuid']),
                       lambda r: r['entries'].append(r['entries'][0].copy()),
                       lambda r: r['runtime'].update(subuid=[[200000, 65536], [200001, 65536]]),
                       lambda r: r['runtime'].update(uid=True)):
            value = copy.deepcopy(self.request)
            change(value)
            with self.assertRaises(d.CopyError):
                d.validate(value)
        for path in ('/etc', '../etc/shadow', 'home/neo', 'srv/app/../secret', 'srv/app//data',
                     'srv/app/data\nsecret', 'etc/init.d/service', 'var/cache/app'):
            value = copy.deepcopy(self.request)
            value['entries'][0]['source'] = path
            with self.subTest(path=path), self.assertRaises(d.CopyError):
                d.validate(value)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_nested_mapping_is_rejected(self):
        self.request['entries'].append({'key': 'nested', 'source': 'srv/app/library/nested'})
        with self.assertRaisesRegex(d.CopyError, 'overlap'):
            d.validate(self.request)

    def test_ordinary_runner_cannot_copy(self):
        with patch.object(d.os, 'geteuid', return_value=1004), patch.object(d, 'check_mounts') as mounts:
            with self.assertRaisesRegex(d.CopyError, 'networkless Xen'):
                d.copy(self.request, self.deadline)
            mounts.assert_not_called()

    @unittest.skipUnless(shutil.which('rsync'), 'native rsync is required')
    def test_native_copy_and_receipt_preserve_data_and_refuse_reuse(self):
        os.link(self.library / 'file', self.library / 'hardlink')
        (self.library / 'symlink').symlink_to('/etc/shadow')
        os.setxattr(self.library / 'file', 'user.test', b'metadata')
        os.utime(self.library / 'file', ns=(1720000000123456789, 1720000000123456789))
        (self.library / 'empty').mkdir(mode=0o750)
        before = d.tree(self.library, self.deadline)
        receipt = self.copy()
        destination = self.target / 'library'
        self.assertEqual(d.tree(destination, self.deadline), before)
        self.assertEqual(d.tree(self.library, self.deadline), before)
        self.assertEqual((destination / 'file').stat().st_ino, (destination / 'hardlink').stat().st_ino)
        self.assertTrue(receipt['copy_verified'])
        self.assertFalse(receipt['adoption_accepted'])
        self.assertEqual(receipt['request_sha256'], d.digest(self.request))
        self.assertFalse((self.target / '.klokast-copy-pending').exists())
        with self.assertRaisesRegex(d.CopyError, 'unknown or partial'):
            self.copy()

    def test_bytes_modes_xattrs_and_link_changes_are_detected(self):
        path = self.library / 'file'
        for mutation in (lambda: path.write_text('changed bytes'), lambda: path.chmod(0o600),
                         lambda: os.setxattr(path, 'user.test', b'changed')):
            before = d.tree(self.library, self.deadline)
            mutation()
            self.assertNotEqual(d.tree(self.library, self.deadline), before)

    def test_symlink_source_and_special_files_refuse_before_claim(self):
        (self.source / 'srv/app/alias').symlink_to(self.library)
        self.request['entries'][0]['source'] = 'srv/app/alias'
        with self.assertRaisesRegex(d.CopyError, 'symlink'):
            self.copy()
        self.request['entries'][0]['source'] = 'srv/app/library'
        os.mkfifo(self.library / 'pipe')
        with self.assertRaisesRegex(d.CopyError, 'FIFO'):
            self.copy()
        self.assertEqual(list(self.target.iterdir()), [])

    def test_external_hardlink_refuses_instead_of_silently_splitting_group(self):
        os.link(self.library / 'file', self.source / 'outside')
        with self.assertRaisesRegex(d.CopyError, 'hardlink extends'):
            self.copy()
        self.assertEqual(list(self.target.iterdir()), [])

    def test_capacity_and_inode_shortage_refuse_before_claim(self):
        for bytes_free, inodes in ((1, 100000), (10**12, 1)):
            with patch.object(d.os, 'statvfs', return_value=SimpleNamespace(f_bavail=bytes_free, f_frsize=1, f_favail=inodes)):
                with self.assertRaisesRegex(d.CopyError, 'insufficient'):
                    self.copy()
            self.assertEqual(list(self.target.iterdir()), [])

    def test_unknown_data_survives_refusal(self):
        path = self.target / 'unknown'
        path.write_text('keep')
        with self.assertRaisesRegex(d.CopyError, 'unknown or partial'):
            self.copy()
        self.assertEqual(path.read_text(), 'keep')

    def test_failure_leaves_claim_and_cannot_be_retried(self):
        with patch.object(d, 'run', side_effect=d.CopyError('copy interrupted')):
            with self.assertRaisesRegex(d.CopyError, 'interrupted'):
                self.copy()
        self.assertTrue((self.target / '.klokast-copy-pending').is_file())
        self.assertFalse((self.target / '.klokast-copy-result.json').exists())
        with self.assertRaisesRegex(d.CopyError, 'unknown or partial'):
            self.copy()

    def test_changed_identity_and_elapsed_budget_prevent_writes(self):
        self.request['runtime'] = {**self.runtime, 'uid': 4567}
        with self.assertRaisesRegex(d.CopyError, 'identities changed'):
            self.copy()
        self.deadline = time.monotonic() - 1
        with self.assertRaisesRegex(d.CopyError, 'deadline'):
            self.copy()
        self.assertEqual(list(self.target.iterdir()), [])

    def test_identity_reads_exact_numeric_and_subordinate_ids(self):
        (self.source / 'etc').mkdir()
        (self.source / 'etc/passwd').write_text('neo:x:2000:3000::/home/neo:/bin/sh\n')
        for name in ('subuid', 'subgid'):
            (self.source / 'etc' / name).write_text('2000:200000:65536\n')
        self.assertEqual(d.runtime_identity(self.source), {**self.runtime, 'uid': 2000, 'gid': 3000})
        (self.source / 'etc/subuid').unlink()
        (self.source / 'etc/subuid').symlink_to('/etc/subuid')
        with self.assertRaisesRegex(d.CopyError, 'symlink'):
            d.runtime_identity(self.source)

    def test_writable_alias_nested_and_wrong_filesystem_mounts_are_rejected(self):
        good = [{'path': str(self.source), 'root': '/', 'source': '/dev/xvdc', 'type': 'ext4',
                 'options': ['ro'], 'device': '202:32'},
                {'path': str(self.target), 'root': '/', 'source': '/dev/xvdd', 'type': 'ext4',
                 'options': ['rw'], 'device': '202:48'}]
        for change in (lambda rows: rows[0].update(options=['rw']),
                       lambda rows: rows.append({**rows[0], 'path': '/alias'}),
                       lambda rows: rows.append({**rows[0], 'device': '0:1', 'path': str(self.source / 'nested')}),
                       lambda rows: rows[0].update(type='tmpfs'), lambda rows: rows[0].update(root='/subdir')):
            records = copy.deepcopy(good)
            change(records)
            with patch.object(d, 'SOURCE', self.source), patch.object(d, 'TARGET', self.target), \
                    patch.object(d, 'mount_records', return_value=records):
                with self.assertRaisesRegex(d.CopyError, 'wrong access'):
                    d.check_mounts(self.request)


if __name__ == '__main__':
    unittest.main()
