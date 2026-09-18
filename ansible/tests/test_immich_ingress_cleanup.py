import importlib.machinery
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / 'apps/immich/ansible/roles/immich-ingress-state-cleanup/files/immich-ingress-state-cleanup'
loader = importlib.machinery.SourceFileLoader('immich_ingress_cleanup', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
cleanup = importlib.util.module_from_spec(spec)
loader.exec_module(cleanup)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'proc/self').mkdir(parents=True)
        (self.root / 'proc/self/mountinfo').write_text('1 0 8:1 / / rw - ext4 /dev/xvda3 rw\n')
        for path in cleanup.ROOTS:
            (self.root / path.lstrip('/')).mkdir(parents=True)

    def inspect(self):
        # Fixture ancestors need not be root-owned. Check production ownership
        # separately; retain the real filesystem entry and mount checks here.
        with patch.object(cleanup, 'require_protected', side_effect=lambda p: p.lstat()):
            return cleanup.inspect(self.root)

    def test_only_fixed_roots_and_no_symlink_traversal(self):
        outside = self.root / 'backend-library'
        outside.mkdir()
        (outside / 'photo').write_text('preserve')
        (self.root / cleanup.ROOTS[0].lstrip('/') / 'link').symlink_to(outside)
        rows = self.inspect()
        self.assertEqual([v['path'] for v in rows], list(cleanup.ROOTS))
        self.assertEqual(rows[0]['entries'], 2)
        self.assertEqual((outside / 'photo').read_text(), 'preserve')

    def test_nested_and_root_mounts_are_refused(self):
        for path in (cleanup.ROOTS[0], cleanup.ROOTS[1] + '/nested'):
            with self.subTest(path=path):
                (self.root / 'proc/self/mountinfo').write_text(f'1 0 8:1 / {path} rw - ext4 /dev/xvdc rw\n')
                with self.assertRaisesRegex(ValueError, 'contains a mount'):
                    self.inspect()

    def test_special_entry_refused(self):
        os.mkfifo(self.root / cleanup.ROOTS[0].lstrip('/') / 'pipe')
        with self.assertRaisesRegex(ValueError, 'unsupported filesystem entry'):
            self.inspect()

    def test_protected_directory_refuses_link_or_other_owner_or_write_access(self):
        for mode, uid in ((stat.S_IFLNK | 0o777, 0), (stat.S_IFDIR | 0o777, 0), (stat.S_IFDIR | 0o700, 1000)):
            with self.subTest(mode=mode, uid=uid):
                info = os.stat_result((mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))
                with patch.object(Path, 'lstat', return_value=info):
                    with self.assertRaisesRegex(ValueError, 'unsafe parent or root'):
                        cleanup.require_protected(Path('/fixed'))

    def test_disabled_script_and_dangling_runlevel_block_cleanup(self):
        init = self.root / 'etc/init.d'
        init.mkdir(parents=True)
        path = init / cleanup.SERVICES[0]
        path.write_text('unknown service')
        with self.assertRaisesRegex(ValueError, 'service state remains'):
            cleanup.require_inactive(self.root)
        path.unlink()
        runlevel = self.root / 'etc/runlevels/default'
        runlevel.mkdir(parents=True)
        (runlevel / cleanup.SERVICES[0]).symlink_to('/missing')
        with self.assertRaisesRegex(ValueError, 'enabled'):
            cleanup.require_inactive(self.root)

    def test_process_arguments_and_open_log_block_cleanup(self):
        proc = self.root / 'proc'
        pid = proc / '123'
        (pid / 'fd').mkdir(parents=True)
        (pid / 'cmdline').write_bytes(b'nginx\x00--prefix=immich-private-ingress\x00')
        with self.assertRaisesRegex(ValueError, 'process is still present'):
            cleanup.require_unused(proc)
        (pid / 'cmdline').write_bytes(b'nginx\x00')
        (pid / 'fd/7').symlink_to(cleanup.ROOTS[1] + '/access.log (deleted)')
        with self.assertRaisesRegex(ValueError, 'still uses'):
            cleanup.require_unused(proc)
        (pid / 'fd/7').unlink()
        cleanup.require_unused(proc)


if __name__ == '__main__':
    unittest.main()
