"""Restore integrity and refusal gates for the one-time DMZ data backup."""
import importlib.machinery
import importlib.util
import io
import json
import os
import hashlib
from pathlib import Path
import socket
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

    def stage_retirement_fixture(self):
        self.base.mkdir(mode=0o700)
        work = self.base / self.operation
        work.mkdir(mode=0o700)
        archive = work / 'static-site.tar'
        manifest = work / 'manifest.json'
        archive.write_bytes(b'archive bytes')
        manifest.write_bytes(b'manifest bytes')
        archive.chmod(0o600)
        manifest.chmod(0o600)
        receipt = {'operation_id': self.operation, 'host': socket.gethostname().split('.')[0],
                   'restore_verified': True, 'source_unchanged': True,
                   'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                   'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
        evidence = work / 'receipt.json'
        evidence.write_text(json.dumps(receipt))
        evidence.chmod(0o600)
        return receipt

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

    def test_verified_controller_copy_retires_exact_guest_staging(self):
        receipt = self.stage_retirement_fixture()
        helper = self.base / 'helper.py'
        helper.write_bytes(b'fixed helper')
        helper.chmod(0o600)
        (self.base / 'async').mkdir(mode=0o700)
        result = b.retire_staging(self.operation, receipt['archive_sha256'],
                                  receipt['manifest_sha256'], self.base)
        self.assertTrue(result['guest_staging_root_retired'])
        self.assertFalse(self.base.exists())
        self.assertTrue(self.page.exists())

    def test_unverified_or_changed_staging_is_preserved(self):
        receipt = self.stage_retirement_fixture()
        work = self.base / self.operation
        with self.assertRaisesRegex(ValueError, 'differs'):
            b.retire_staging(self.operation, '0' * 64, receipt['manifest_sha256'], self.base)
        self.assertTrue((work / 'static-site.tar').exists())
        (work / 'unexpected').write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, 'unexpected files'):
            b.retire_staging(self.operation, receipt['archive_sha256'],
                             receipt['manifest_sha256'], self.base)
        self.assertTrue((work / 'unexpected').exists())

    def test_archive_path_traversal_is_refused_without_writing_outside_restore(self):
        archive = Path(self.tmp.name) / 'hostile.tar'
        with tarfile.open(archive, 'w') as stream:
            item = tarfile.TarInfo('../escape'); item.size = 1
            stream.addfile(item, io.BytesIO(b'x'))
        with self.assertRaises(ValueError):
            b.restore(archive, Path(self.tmp.name)/'restore', {'../escape': {'type': 'file'}})
        self.assertFalse((Path(self.tmp.name)/'escape').exists())


class BackupOrchestrationTests(unittest.TestCase):
    def test_orphan_stop_checks_exact_service_and_pid_before_native_stop(self):
        tasks = yaml.safe_load((REPO / 'apps/nextcloud/ansible/roles/nextcloud-remove/tasks/main.yml').read_text())
        task = next(v for v in tasks if 'exact private ingress supervisor' in v['name'])
        code = task['ansible.builtin.command']['argv'][-1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / 'proc'; proc.mkdir()
            pidfile = root / 'pid'
            code = code.replace("Path('/proc')", f'Path({str(proc)!r})')
            code = code.replace('/run/nextcloud-private-ingress/tailscaled.pid', str(pidfile))
            management = proc / '12'; management.mkdir()
            (management / 'cmdline').write_bytes(b'supervise-daemon\0tailscale\0--start\0')
            with patch('subprocess.run') as run:
                exec(compile(code, '<orphan-stop>', 'exec'), {})
                run.assert_not_called()
            app = proc / '13'; app.mkdir()
            (app / 'cmdline').write_bytes(b'\0'.join([b'supervise-daemon', b'nextcloud-private-ingress',
                                                   b'--start', b'--pidfile', str(pidfile).encode(), b'']))
            pidfile.write_text('12\n')
            with patch('subprocess.run') as run, self.assertRaisesRegex(AssertionError, 'differs'):
                exec(compile(code, '<orphan-stop>', 'exec'), {})
            run.assert_not_called()
            pidfile.write_text('13\n')
            with patch('subprocess.run') as run:
                exec(compile(code, '<orphan-stop>', 'exec'), {})
                run.assert_called_once_with(['/sbin/supervise-daemon', 'nextcloud-private-ingress',
                                             '--stop', '--pidfile', str(pidfile)], check=True, timeout=30,
                                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                                 'RC_SVCNAME': 'nextcloud-private-ingress'})

    def test_bounded_backup_uses_an_async_supported_command_after_staging(self):
        tasks = yaml.safe_load((SCRIPT.parents[1] / 'tasks/main.yml').read_text())
        asynchronous = [v for v in tasks if v.get('async')]
        self.assertEqual(len(asynchronous), 1)
        self.assertIn('ansible.builtin.command', asynchronous[0])
        self.assertEqual(asynchronous[0]['async'], 660)
        self.assertEqual(asynchronous[0]['vars']['ansible_async_dir'], '/var/tmp/klokast-static-site-backup/async')
        self.assertTrue(any('ansible.builtin.copy' in v for v in tasks[:tasks.index(asynchronous[0])]))

    def test_guest_stage_retirement_follows_controller_and_source_checks(self):
        tasks = yaml.safe_load((SCRIPT.parents[1] / 'tasks/main.yml').read_text())
        names = [task['name'] for task in tasks]
        verified = names.index('Verify the fetched bytes and bind the backup to this guest')
        source = names.index('Recheck the original data against its verified backup before removal')
        retired = names.index('Retire only guest staging whose bytes match the verified controller copy')
        self.assertLess(verified, source)
        self.assertLess(source, retired)
        args = tasks[retired]['ansible.builtin.command']['argv']
        self.assertIn('static_site_backup_verified.stdout | from_json', ' '.join(args))

    def test_cleanup_play_never_selects_backend_and_requires_backup_evidence(self):
        plays = yaml.safe_load((REPO / 'ansible/playbooks/74-platform-update-dmz-cleanup.yml').read_text())
        self.assertEqual([v['hosts'] for v in plays], ['dmz', 'dmz'])
        self.assertTrue(all(v['any_errors_fatal'] for v in plays))
        self.assertIn('static_site_backup_verified.rc == 0', plays[1]['tasks'][0]['ansible.builtin.assert']['that'])
        self.assertTrue(plays[1]['tasks'][1]['vars']['nextcloud_remove_dmz_only'])

    def test_historical_staging_cleanup_preserves_missing_copy_before_retirement(self):
        plays = yaml.safe_load((REPO / 'ansible/playbooks/74-platform-update-static-site-staging-cleanup.yml').read_text())
        self.assertEqual([play['hosts'] for play in plays], ['dmz', 'dmz'])
        self.assertTrue(all(play['any_errors_fatal'] for play in plays))
        self.assertEqual(len(plays[0]['vars']['stage_operations']), 4)
        names = [task['name'] for task in plays[0]['tasks']]
        self.assertLess(names.index('Preserve the missing guest archive, manifest, and receipt'),
                        names.index('Verify all four independent controller backup copies'))
        self.assertIn('stage_verified_copies.results', str(plays[1]['tasks']))
        self.assertNotIn('static_site_wipe_data', str(plays))


if __name__ == '__main__':
    unittest.main()
