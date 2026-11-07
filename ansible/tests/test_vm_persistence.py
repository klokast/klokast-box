"""Verify writable-media preparation before diskless persistence."""
import sys
from pathlib import Path
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
