"""Restore integrity and refusal gates for the one-time DMZ data backup."""
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import time
import unittest
import yaml
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'apps/static-site/ansible/roles/static-site-backup/files/static-site-backup'
loader = importlib.machinery.SourceFileLoader('static_site_backup', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
b = importlib.util.module_from_spec(spec)
loader.exec_module(b)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'source'
        self.base = Path(self.tmp.name) / 'backups'
        self.public = self.root / 'srv/static-site/public'
        self.public.mkdir(parents=True)
        self.page = self.public / 'index.html'
        self.page.write_bytes(b'private-content-not-for-logs')
        self.page.chmod(0o640)
        os.utime(self.page, ns=(1234567890123456789, 1234567890123456789))
        self.operation = 'a' * 24

    def backup(self):
        return b.backup(self.operation, self.root, self.base)

    @unittest.skipUnless(hasattr(tarfile, 'data_filter'), 'requires controller/guest Python tar data filter')
    def test_archive_restores_data_metadata_xattrs_hardlinks_and_relative_symlinks(self):
        os.setxattr(self.page, 'user.test', b'opaque-xattr')
        os.link(self.page, self.public / 'copy.html')
        (self.public / 'link.html').symlink_to('index.html')
        receipt = self.backup()
        self.assertTrue(receipt['restore_verified'])
        self.assertNotIn('private-content', json.dumps(receipt))
        self.assertNotIn('opaque-xattr', json.dumps(receipt))
        self.assertEqual(b.verify_source(self.operation, self.root, self.base), receipt)
        work = self.base / self.operation
        restored = Path(self.tmp.name) / 'restored'
        restored.mkdir()
        entries = json.loads((work / 'manifest.json').read_bytes())['entries']
        b.restore(work / 'static-site.tar', restored, entries)
        self.assertEqual(b.inventory(restored, time.monotonic()+20), entries)
        self.assertEqual((work / 'static-site.tar').stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(hasattr(tarfile, 'data_filter'), 'requires controller/guest Python tar data filter')
    def test_empty_app_is_recorded_without_inventing_a_data_backup(self):
        root = Path(self.tmp.name) / 'absent'
        root.mkdir()
        receipt = b.backup(self.operation, root, self.base)
        self.assertEqual(receipt['entries'], 0)
        self.assertEqual(receipt['roots_present'], [])

    @unittest.skipUnless(hasattr(tarfile, 'data_filter'), 'requires controller/guest Python tar data filter')
    def test_changed_source_blocks_removal_even_with_equal_size_and_mtime(self):
        self.backup()
        old = self.page.stat()
        self.page.write_bytes(b'x' * old.st_size)
        os.utime(self.page, ns=(old.st_atime_ns, old.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'source changed'):
            b.verify_source(self.operation, self.root, self.base)

    @unittest.skipUnless(hasattr(tarfile, 'data_filter'), 'requires controller/guest Python tar data filter')
    def test_changed_archive_and_manifest_block_removal(self):
        self.backup()
        for name in ('static-site.tar', 'manifest.json'):
            p = self.base / self.operation / name
            old = p.read_bytes()
            p.write_bytes(old+b' ')
            with self.assertRaises(ValueError):
                b.verify_source(self.operation, self.root, self.base)
            p.write_bytes(old)

    def test_external_links_special_files_and_mounts_are_refused(self):
        link = self.public / 'outside'
        for target in ('/etc/passwd', '../../../outside'):
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'symlink leaves'):
                b.inventory(self.root, time.monotonic()+20)
            link.unlink()
        os.link(self.page, self.root / 'outside')
        with self.assertRaisesRegex(ValueError, 'hardlink outside'):
            b.inventory(self.root, time.monotonic()+20)
        (self.root / 'outside').unlink()
        os.mkfifo(self.public / 'fifo')
        with self.assertRaisesRegex(ValueError, 'special file'):
            b.inventory(self.root, time.monotonic()+20)
        (self.public / 'fifo').unlink()
        with self.assertRaisesRegex(ValueError, 'mount boundary'):
            b.inventory(self.root, time.monotonic()+20, [str(self.public)])

    def test_symlink_parent_is_not_traversed(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (self.root / 'etc').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink parent'):
            b.inventory(self.root, time.monotonic()+20)

    def test_capacity_and_limits_fail_before_publishing_receipt(self):
        for value in (0,):
            with patch.object(b, 'LIMIT', value), self.assertRaisesRegex(ValueError, '256 MiB'):
                b.inventory(self.root, time.monotonic()+20)
        with patch.object(b.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 0})()):
            with self.assertRaisesRegex(ValueError, 'lacks space'):
                self.backup()
        self.assertFalse((self.base / self.operation / 'receipt.json').exists())

    @unittest.skipUnless(hasattr(tarfile, 'data_filter'), 'requires controller/guest Python tar data filter')
    def test_interrupted_or_successful_operation_is_never_reused(self):
        self.backup()
        with self.assertRaises(FileExistsError):
            self.backup()

    def test_archive_path_traversal_is_refused_without_writing_outside_restore(self):
        archive = Path(self.tmp.name) / 'hostile.tar'
        with tarfile.open(archive, 'w') as stream:
            item = tarfile.TarInfo('../escape'); item.size = 1
            stream.addfile(item, io.BytesIO(b'x'))
        with self.assertRaises(ValueError):
            b.restore(archive, Path(self.tmp.name)/'restore', {'../escape': {'type': 'file'}})
        self.assertFalse((Path(self.tmp.name)/'escape').exists())


class BackupOrchestrationTests(unittest.TestCase):
    def test_bounded_backup_uses_an_async_supported_command_after_staging(self):
        tasks = yaml.safe_load((SCRIPT.parents[1] / 'tasks/main.yml').read_text())
        asynchronous = [v for v in tasks if v.get('async')]
        self.assertEqual(len(asynchronous), 1)
        self.assertIn('ansible.builtin.command', asynchronous[0])
        self.assertEqual(asynchronous[0]['async'], 660)
        self.assertTrue(any('ansible.builtin.copy' in v for v in tasks[:tasks.index(asynchronous[0])]))

    def test_cleanup_play_never_selects_backend_and_requires_backup_evidence(self):
        plays = yaml.safe_load((REPO / 'ansible/playbooks/74-platform-update-dmz-cleanup.yml').read_text())
        self.assertEqual([v['hosts'] for v in plays], ['dmz', 'dmz'])
        self.assertTrue(all(v['any_errors_fatal'] for v in plays))
        self.assertIn('static_site_backup_verified.rc == 0', plays[1]['tasks'][0]['ansible.builtin.assert']['that'])
        self.assertTrue(plays[1]['tasks'][1]['vars']['nextcloud_remove_dmz_only'])


if __name__ == '__main__':
    unittest.main()
