"""Safety boundaries for offline FAT maintenance."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('boot_check', Path(__file__).parents[1] / 'roles/dom0-update/files/dom0_boot_check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class BootCheckTests(unittest.TestCase):
    def test_failed_unmount_stops_before_fsck(self):
        mounts = '/dev/nvme0n1p1 /media/nvme0n1p1 vfat rw 0 0\n'
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[0] == 'umount':
                raise RuntimeError('busy')
        with patch.object(check.Path, 'read_text', return_value=mounts), \
             patch.object(check.Path, 'read_bytes', return_value=b'original'), \
             patch.object(check.Path, 'exists', return_value=False), \
             patch.object(check, 'hashes', return_value={}), \
             patch.object(check, 'release_boot_modloop'), \
             patch.object(check.shutil, 'which', return_value='/sbin/fsck.fat'), \
             patch.object(check, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                check.maintain()
        self.assertEqual(calls, [['sync'], ['umount', '/media/nvme0n1p1']])

    def test_ambiguous_mount_refuses_any_command(self):
        with patch.object(check.Path, 'read_text', return_value=''), patch.object(check, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'one managed FAT'):
                check.maintain()
            run.assert_not_called()

    def test_fsck_uncorrected_error_is_not_accepted(self):
        with patch.object(check.subprocess, 'run', return_value=subprocess.CompletedProcess([], 4, '', 'uncorrected')):
            with self.assertRaises(RuntimeError):
                check.run(['fsck.fat', '-a', '/dev/example'], codes=(0, 1))

    def test_readonly_check_requires_clean_exit(self):
        with patch.object(check.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'dirty')):
            with self.assertRaises(RuntimeError):
                check.run(['fsck.fat', '-n', '/dev/example'])

class RouterPersistenceTests(unittest.TestCase):
    def test_failed_commit_restores_readonly_media(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parents[1] / 'lib'))
        import router_native
        calls = []
        def read(path):
            return 'LBU_BACKUPDIR=/media/efi\n' if str(path).endswith('lbu.conf') else '/dev/boot /media/efi vfat ro 0 0\n'
        def invoke(argv, *args, **kwargs):
            calls.append(argv)
            if argv[0] == 'lbu':
                raise router_native.TransactionError('failed commit')
        with patch.object(router_native.Path, 'read_text', read), patch.object(router_native, '_command', side_effect=invoke):
            with self.assertRaises(router_native.TransactionError):
                router_native.command(['/usr/sbin/lbu', 'commit', '-d'], 100)
        self.assertEqual(calls, [['mount', '-o', 'remount,rw', '/dev/boot', '/media/efi'],
                                 ['lbu', 'commit', '-d'],
                                 ['mount', '-o', 'remount,ro', '/dev/boot', '/media/efi']])
