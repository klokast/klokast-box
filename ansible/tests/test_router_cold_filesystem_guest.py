"""The cold filesystem guest accepts only one networkless read-only source."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock


def guest_module():
    path = (Path(__file__).resolve().parents[1] / 'roles/router-cold-filesystem/files'
            / 'router-cold-filesystem-guest')
    spec = importlib.util.spec_from_loader('router_cold_guest_test',
        importlib.machinery.SourceFileLoader('router_cold_guest_test', str(path)))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class GuestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.guest = guest_module()
        self.job = {'kind': 'klokast.router-cold-filesystem-job.v1',
                    'box': 'boxa', 'operation_id': 'a'*24, 'engine_commit': 'c'*40,
                    'inputs_sha256': 'b'*64, 'metadata_sha256': 'd'*64,
                    'disk_sha256': 'e'*64, 'backup_uuid': 'backup-uuid'}
        for name in ('sys/hypervisor', 'sys/class/net/lo', 'sys/class/block/xvda',
                     'sys/class/block/xvdb', 'sys/class/block/xvdc', 'dev', 'proc/self'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / 'sys/hypervisor/type').write_text('xen\n')
        (self.root / 'sys/hypervisor/uuid').write_text('12345678-1111-4111-8111-111111111111\n')
        for name, mode in (('xvda', '1'), ('xvdb', '0'), ('xvdc', '1')):
            (self.root / 'dev' / name).write_bytes(b'\0' * 128)
            (self.root / 'sys/class/block' / name / 'ro').write_text(mode + '\n')
        (self.root / 'dev/xvdc').write_bytes((json.dumps(self.job) + '\0').encode())
        (self.root / 'proc/cmdline').write_text('console=hvc0 ' + ' '.join((
            'klokast_operation=' + self.job['operation_id'],
            'klokast_inputs=' + self.job['inputs_sha256'],
            'klokast_job=' + self.guest.digest(self.job))) + '\n')
        original = Path
        def selected(path):
            return self.root / str(path).removeprefix('/') if str(path).startswith('/') else original(path)
        class Device:
            def __init__(self, path, identity):
                self.path, self.identity = path, identity
            def is_symlink(self):
                return False
            def stat(self):
                return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=self.identity)
            def open(self, *args, **kwargs):
                return self.path.open(*args, **kwargs)
        for name, value in (('Path', selected),
                            ('SOURCE', Device(self.root / 'dev/xvda', 101)),
                            ('RESULT', Device(self.root / 'dev/xvdb', 102)),
                            ('JOB', Device(self.root / 'dev/xvdc', 103))):
            patch = mock.patch.object(self.guest, name, value)
            patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(self.guest.os, 'geteuid', return_value=0)
        patch.start(); self.addCleanup(patch.stop)

    def test_exact_readonly_networkless_job_is_accepted(self):
        self.assertEqual(self.guest.guard(), self.job)

    def test_writable_source_wrong_job_extra_nic_or_disk_refuses(self):
        cases = [
            ('writable source', self.root / 'sys/class/block/xvda/ro', '0\n'),
            ('writable job', self.root / 'sys/class/block/xvdc/ro', '0\n'),
            ('wrong boot', self.root / 'proc/cmdline', 'klokast_operation=' + 'f'*24),
        ]
        for label, path, changed in cases:
            original = path.read_bytes()
            path.write_text(changed)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                self.guest.guard()
            path.write_bytes(original)
        (self.root / 'sys/class/net/eth0').mkdir()
        with self.assertRaisesRegex(RuntimeError, 'networkless Xen'):
            self.guest.guard()
        (self.root / 'sys/class/net/eth0').rmdir()
        (self.root / 'sys/class/block/xvdd').mkdir()
        with self.assertRaisesRegex(RuntimeError, 'one read-only source'):
            self.guest.guard()

    def test_duplicate_job_fields_and_wrong_digest_refuse(self):
        job = self.root / 'dev/xvdc'
        job.write_text('{"kind":"a","kind":"b"}\0')
        with self.assertRaisesRegex(RuntimeError, 'duplicate fields'):
            self.guest.guard()
        changed = {**self.job, 'backup_uuid': 'different'}
        job.write_text(json.dumps(changed) + '\0')
        with self.assertRaisesRegex(RuntimeError, 'selects another job'):
            self.guest.guard()

    def test_readonly_ext4_check_and_recovery_files_precede_success(self):
        mount = self.root / 'run/router-cold-root'
        mount.parent.mkdir()
        mounted = {'value': False}
        commands = []
        def command(argv, deadline):
            commands.append(argv)
            if argv[0] == '/bin/mount':
                mounted['value'] = True
                for name in self.guest.FILES:
                    target = mount / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b'proof fixture')
            if argv[0] == '/bin/umount':
                mounted['value'] = False
        original = Path
        def selected(path):
            if str(path) == '/proc/self/mountinfo':
                class Mounts:
                    def read_text(self):
                        return ('0 0 0:0 / ' + str(mount) + ' rw - ext4 fake fake\n') if mounted['value'] else ''
                return Mounts()
            return self.root / str(path).removeprefix('/') if str(path).startswith('/') else original(path)
        with mock.patch.object(self.guest, 'Path', selected), \
             mock.patch.object(self.guest, 'ROOT', mount), \
             mock.patch.object(self.guest, 'command', side_effect=command):
            self.guest.inspect()
        self.assertEqual(commands[0][:2], ['/sbin/e2fsck', '-fn'])
        self.assertEqual(commands[1][4], 'ro,noload,nodev,nosuid,noexec')
        self.assertEqual(commands[-1], ['/bin/umount', str(mount)])

    def test_missing_recovery_file_refuses_and_unmounts(self):
        mount = self.root / 'run/router-cold-root'
        mount.parent.mkdir()
        unmounted = []
        def command(argv, deadline):
            if argv[0] == '/bin/mount':
                for name in self.guest.FILES[:-1]:
                    target = mount / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b'proof fixture')
            if argv[0] == '/bin/umount':
                unmounted.append(argv)
        with mock.patch.object(self.guest, 'ROOT', mount), \
             mock.patch.object(self.guest, 'command', side_effect=command):
            with self.assertRaisesRegex(RuntimeError, 'lacks a required'):
                self.guest.inspect()
        self.assertEqual(unmounted, [['/bin/umount', str(mount)]])


if __name__ == '__main__':
    unittest.main()
