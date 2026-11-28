"""Verify writable-media preparation and the committed-overlay check of diskless persistence."""
import io
import sys
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'roles/vm-update-recovery/files'))
import vm_update_native as native

class PersistenceTests(unittest.TestCase):
    def run_case(self, mounts, failure=None, config='LBU_BACKUPDIR=/media/efi\n'):
        def read(path):
            return config if str(path) == '/etc/lbu/lbu.conf' else mounts
        with patch.object(native.Path, 'read_text', read), patch.object(native, 'command', side_effect=failure) as command:
            if failure or ('LBU_BACKUPDIR=' in config and not mounts):
                with self.assertRaises(native.Refused): native.Native().persist()
            else:
                native.Native().persist()
            return [c.args[0] for c in command.call_args_list]

    def test_readonly_remount_precedes_commit(self):
        self.assertEqual(self.run_case('/dev/nvme0n1p1 /media/efi vfat ro,relatime 0 0'),
                         [['mount', '-o', 'remount,rw', '/dev/nvme0n1p1', '/media/efi'], ['lbu', 'commit', '-d'], ['mount', '-o', 'remount,ro', '/dev/nvme0n1p1', '/media/efi']])

    def test_writable_media_needs_only_commit(self):
        self.assertEqual(self.run_case('/dev/nvme0n1p1 /media/efi vfat rw 0 0'), [['lbu', 'commit', '-d']])

    def test_remount_failure_prevents_commit(self):
        self.assertEqual(len(self.run_case('/dev/nvme0n1p1 /media/efi vfat ro 0 0', native.Refused('remount failed'))), 1)

    def test_unmounted_directory_refuses_commit(self):
        self.assertEqual(self.run_case(''), [])

    def test_lbu_failure_propagates(self):
        self.assertEqual(self.run_case('/dev/nvme0n1p1 /media/efi vfat rw 0 0', native.Refused('commit failed')), [['lbu', 'commit', '-d']])

    def test_media_mode_preserves_native_lbu_handling(self):
        self.assertEqual(self.run_case('', config='LBU_MEDIA=nvme\n'), [['lbu', 'commit', '-d']])

    def test_failed_commit_restores_readonly_media(self):
        def fail_commit(argv, **kwargs):
            if argv[0] == 'lbu': raise native.Refused('commit failed')
        calls = self.run_case('/dev/nvme0n1p1 /media/efi vfat ro 0 0', fail_commit)
        self.assertEqual(calls[-1], ['mount', '-o', 'remount,ro', '/dev/nvme0n1p1', '/media/efi'])


class CommittedOverlayTests(unittest.TestCase):
    """The persistence check reads the last lbu commit, not the live files."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.archive = Path(temp.name) / 'boxa-dom0.apkovl.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as overlay:
            config = tarfile.TarInfo('etc/xen/bak.cfg'); config.size = len(b'name = "bak"\n')
            overlay.addfile(config, io.BytesIO(b'name = "bak"\n'))
            link = tarfile.TarInfo('etc/xen/auto/bak.cfg'); link.type = tarfile.SYMTYPE; link.linkname = '../bak.cfg'
            overlay.addfile(link)
            directory = tarfile.TarInfo('etc/xen/auto/dmz.cfg'); directory.type = tarfile.DIRTYPE
            overlay.addfile(directory)
        for target, name, value in ((native.Native, 'backup_directory', temp.name),
                                    (native.socket, 'gethostname', 'boxa-dom0')):
            started = patch.object(target, name, return_value=value); started.start(); self.addCleanup(started.stop)

    def held(self, entries):
        return native.Native().committed({Path(path): value for path, value in entries.items()})

    def test_exact_files_links_and_absences_are_held(self):
        self.assertTrue(self.held({'/etc/xen/bak.cfg': b'name = "bak"\n',
                                   '/etc/xen/auto/bak.cfg': '../bak.cfg', '/etc/xen/auto/iot.cfg': None}))

    def test_each_difference_is_not_held(self):
        for entries in ({'/etc/xen/bak.cfg': b'name = "dmz"\n'}, {'/etc/xen/bak.cfg': '../bak.cfg'},
                        {'/etc/xen/auto/bak.cfg': None}, {'/etc/xen/auto/bak.cfg': '/etc/xen/bak.cfg'},
                        {'/etc/xen/iot.cfg': b''}, {'/etc/xen/auto/dmz.cfg': None}):
            with self.subTest(entries=entries):
                self.assertFalse(self.held(entries))

    def test_an_unreadable_or_absent_overlay_holds_nothing(self):
        entries = {'/etc/xen/auto/iot.cfg': None}
        self.assertTrue(self.held(entries))
        for damaged in (self.archive.read_bytes()[:40], b'not an overlay'):
            self.archive.write_bytes(damaged)
            self.assertFalse(self.held(entries))
        self.archive.unlink()
        self.assertFalse(self.held(entries))
        with patch.object(native.Native, 'backup_directory', return_value=None):
            self.assertFalse(self.held(entries))
