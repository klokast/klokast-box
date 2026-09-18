"""Full-disk backup verification boundaries; fixtures are local regular files."""
import builtins
import copy
import os
from pathlib import Path
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_vm_retained_data import d


class BackupRestore(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.source, self.target = self.base / 'backup', self.base / 'restore'
        with self.source.open('wb') as stream:
            stream.write(b'synthetic opaque disk'); stream.truncate(16 * 1024**2)
        self.target.write_bytes(b'original disposable target')
        self.mount = self.base / 'mount'; self.mount.mkdir()
        self.request = {'kind': 'klokast.vm-backup-restore.v1', 'operation_id': 'a' * 24,
                        'engine_commit': 'b' * 40, 'backup_receipt_sha256': 'c' * 64,
                        'disk_bytes': self.source.stat().st_size,
                        'disk_sha256': d.disk_digest(self.source, self.source.stat().st_size, time.monotonic() + 10),
                        'root_partition': 0, 'root_uuid': '11111111-1111-4111-8111-111111111111',
                        'runtime': {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[300000, 65536]]}}
        self.pending = self.base / 'pending'

    def test_closed_request_disk_size_and_numeric_ranges(self):
        d.validate_backup(self.request)
        for key, value in (('kind', 'other'), ('disk_bytes', True), ('disk_bytes', 16 * 1024**2 + 1),
                           ('disk_bytes', 256 * 1024**3), ('root_partition', 1), ('root_partition', True),
                           ('backup_receipt_sha256', '../path'), ('engine_commit', 'main'),
                           ('extra', 'untrusted'), ('runtime', dict(self.request['runtime'], subuid=[[0, 65536]]))):
            request = copy.deepcopy(self.request); request[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(d.CopyError): d.validate_backup(request)

    def execute(self, request=None, failure=None):
        real_open, real_exists = builtins.open, Path.exists
        paths = {'/dev/xvdc': self.source, '/dev/xvdd': self.target}
        def open_file(path, *args, **kwargs):
            return real_open(paths.get(str(path), path), *args, **kwargs)
        def command(argv, deadline, **kwargs):
            self.commands.append(argv)
            if argv[:3] == ['e2fsck', '-f', '-n'] and failure == 'filesystem':
                raise d.CopyError('filesystem check failed')
            if argv[0] == 'mount':
                identity = self.mount / 'var/lib/tailscale/tailscaled.state'
                identity.parent.mkdir(parents=True)
                identity.write_bytes(b'private synthetic identity'); identity.chmod(0o600)
        real_lstat = Path.lstat
        def metadata(path):
            info = real_lstat(path)
            if path == self.mount / 'var/lib/tailscale/tailscaled.state':
                info = list(info); info[4] = 0
                return os.stat_result(info)
            return info
        self.commands = []
        with patch.object(d, 'BACKUP_MOUNT', self.mount), patch.object(d, 'BACKUP_PENDING', self.pending), \
                patch.object(d, 'environment'), patch.object(d, 'backup_devices'), \
                patch.object(d, 'read_record', return_value={'engine_commit': 'b' * 40, 'profile': 'shared-alpine-v1'}), \
                patch.object(d, 'backup_root', side_effect=lambda name, part: '/dev/' + name), \
                patch.object(d, 'filesystem_uuid', return_value=self.request['root_uuid']), \
                patch.object(d, 'run', side_effect=command), patch.object(d.fcntl, 'ioctl'), \
                patch.object(d, 'mount_records', return_value=[{'source': '/dev/xvdd', 'path': str(self.mount),
                    'root': '/', 'type': 'ext4', 'options': ['ro', 'nodev', 'nosuid', 'noexec']}]), \
                patch.object(d, 'runtime_identity', return_value=self.request['runtime']), \
                patch.object(d, 'tree', return_value={'sha256': 'd' * 64, 'entries': 1, 'required_bytes': 4096}), \
                patch.object(Path, 'lstat', metadata), \
                patch.object(Path, 'exists', lambda p: True if str(p) == '/dev/xvdd' else real_exists(p)), \
                patch.object(builtins, 'open', side_effect=open_file):
            return d.restore_backup(request or self.request, time.monotonic() + 30)

    def test_complete_disk_restore_is_checked_without_accepting_adoption(self):
        result = self.execute()
        self.assertEqual(self.source.read_bytes(), self.target.read_bytes())
        self.assertTrue(result['complete_disk_restored']); self.assertTrue(result['backup_unchanged'])
        self.assertFalse(result['source_freshness_verified']); self.assertFalse(result['application_consistency_verified'])
        self.assertFalse(result['adoption_accepted']); self.assertTrue(self.pending.exists())
        self.assertEqual(result['receipt_sha256'], d.digest({k: v for k, v in result.items() if k != 'receipt_sha256'}))
        self.assertEqual(self.commands[:2], [['e2fsck', '-p', '-E', 'journal_only', '/dev/xvdd'],
                                            ['e2fsck', '-f', '-n', '/dev/xvdd']])
        self.assertIn('ro,noload,nodev,nosuid,noexec', self.commands[2])
        with self.assertRaisesRegex(d.CopyError, 'already used'): self.execute()

    def test_changed_backup_or_engine_fails_before_destination_writes(self):
        original = self.target.read_bytes()
        for key, value in (('disk_sha256', 'f' * 64), ('engine_commit', 'f' * 40),
                           ('root_uuid', '22222222-2222-4222-8222-222222222222')):
            request = dict(self.request); request[key] = value
            with self.subTest(key=key), self.assertRaises(d.CopyError): self.execute(request)
            self.assertEqual(self.target.read_bytes(), original)
            self.assertFalse(self.pending.exists())

    def test_filesystem_error_keeps_poisoned_staging_and_never_mounts(self):
        with self.assertRaisesRegex(d.CopyError, 'filesystem check failed'): self.execute(failure='filesystem')
        self.assertTrue(self.pending.exists())
        self.assertFalse(any(v[0] == 'mount' for v in self.commands))
        self.assertEqual(self.source.read_bytes(), self.target.read_bytes())

    def test_devices_refuse_writable_backup_aliases_and_mounted_partitions(self):
        sys = self.base / 'sys'; sys.mkdir()
        for name, readonly, dev in (('xvdc', '1', '8:1'), ('xvdd', '0', '8:2')):
            directory = sys / name; directory.mkdir()
            for field, value in (('size', str(self.request['disk_bytes'] // 512)), ('ro', readonly), ('dev', dev)):
                (directory / field).write_text(value)
        real_lstat = Path.lstat
        def metadata(path):
            if str(path) in ('/dev/xvdc', '/dev/xvdd'):
                return SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=1 if path.name == 'xvdc' else 2)
            return real_lstat(path)
        with patch.object(d, 'BLOCK_SYS', sys), patch.object(Path, 'lstat', metadata), patch.object(d, 'mount_records', return_value=[]) as mounts:
            d.backup_devices(self.request)
            mounts.return_value = [{'device': '8:1', 'source': 'UUID=alias'}]
            with self.assertRaisesRegex(d.CopyError, 'mounted'): d.backup_devices(self.request)
            mounts.return_value = []
            (sys / 'xvdc/ro').write_text('0')
            with self.assertRaisesRegex(d.CopyError, 'read-only'): d.backup_devices(self.request)


if __name__ == '__main__': unittest.main()
