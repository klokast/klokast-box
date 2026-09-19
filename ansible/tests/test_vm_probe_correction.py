"""The inspection correction cannot follow substituted directories."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


class ProbeCorrection(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'maintenance/cleanup-empty-podman-probe-20260919.py'
        spec = importlib.util.spec_from_file_location('probe_correction', path)
        self.m = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name); self.root = self.parent / 'store'
        self.root.mkdir(); (self.root / 'sub').mkdir(); (self.root / 'sub/file').write_text('fixture')

    def entries(self):
        rows = []
        for path in [self.root, self.root / 'sub', self.root / 'sub/file']:
            info = path.lstat()
            rows.append({'path': str(path), 'inode': info.st_ino, 'uid': info.st_uid, 'gid': info.st_gid,
                         'mode': info.st_mode, 'mtime_ns': info.st_mtime_ns, 'bytes': info.st_size})
        return rows

    def test_remove_only_the_recorded_tree(self):
        outside = self.parent / 'keep'; outside.write_text('keep')
        self.m.remove_entries(self.root, self.entries())
        self.assertFalse(self.root.exists()); self.assertEqual(outside.read_text(), 'keep')

    def test_changed_file_is_refused_before_removal(self):
        rows = self.entries(); (self.root / 'sub/file').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.m.remove_entries(self.root, rows)
        self.assertEqual((self.root / 'sub/file').read_text(), 'changed')

    def test_substituted_parent_does_not_delete_outside_files(self):
        rows = self.entries(); outside = self.parent / 'outside'
        (self.root / 'sub').rename(outside); (self.root / 'sub').symlink_to(outside)
        with self.assertRaises(ValueError): self.m.remove_entries(self.root, rows)
        self.assertEqual((outside / 'file').read_text(), 'fixture')


if __name__ == '__main__': unittest.main()
