"""Exact-scope tests for the separately authorized backup maintenance."""

import hashlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ExtraBackupDeletionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = load('extra_backup_task', ROOT / 'ansible/maintenance/delete-extra-registry-backups-20260913.py')
        self.m = load('extra_backup_apply', ROOT / 'klokast-ops/secret-authority/bin/ksa-apply')
        self.m.PRIVATE_ROOT = self.root
        self.m.AUTHORITY_POINTER = self.root / 'authority'
        self.m.AUTHORITY_POINTER.write_text(self.task.STATE + '\n')
        self.m.LEGACY_INPUTS = tuple(self.root / n for n in ('deployment.yml', 'platform-resources.yml', 'controller-ha.yml'))
        self.m.OBSOLETE_BACKUPS = (self.root / 'platform-resources.yml.retained.bak',)
        for p in (*self.m.LEGACY_INPUTS, *self.m.OBSOLETE_BACKUPS):
            p.write_text('preserve\n')
        targets = []
        self.paths = []
        for name, _, _, _ in self.task.TARGETS:
            p = self.root / name
            p.write_text(name)
            p.chmod(0o600)
            targets.append((name, hashlib.sha256(name.encode()).hexdigest(), len(name), '0600'))
            self.paths.append(p)
        self.task.TARGETS = tuple(targets)
        self.task.EVIDENCE = self.root / 'evidence'
        metadata = self.m.retirement_metadata
        self.m.retirement_metadata = lambda p: dict(metadata(p), uid=1002, gid=1002)
        self.m.require_root_active = lambda: None
        self.m.require_self_match = lambda: None
        self.m.authority_publication_lock = nullcontext

    def test_prepare_does_not_delete_or_create_evidence(self):
        self.assertEqual(self.task.maintain(self.m, False)['result'], 'prepared')
        self.assertTrue(all(p.exists() for p in self.paths))
        self.assertFalse(self.task.EVIDENCE.exists())

    def test_exact_delete_preserves_live_and_original_backups(self):
        self.assertEqual(self.task.maintain(self.m, True)['result'], 'deleted')
        self.assertTrue(all(not p.exists() for p in self.paths))
        for p in (*self.m.LEGACY_INPUTS, *self.m.OBSOLETE_BACKUPS):
            self.assertEqual(p.read_text(), 'preserve\n')
        self.assertEqual(self.task.EVIDENCE.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.task.EVIDENCE / 'result.json').stat().st_mode & 0o777, 0o400)
        with self.assertRaises(Exception):
            self.task.maintain(self.m, True)

    def test_changed_last_file_refuses_before_any_deletion(self):
        self.paths[-1].write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.task.maintain(self.m, True)
        self.assertTrue(all(p.exists() for p in self.paths))

    def test_unapproved_extra_refuses(self):
        (self.root / 'platform-resources.yml.unapproved.bak').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'name set changed'):
            self.task.maintain(self.m, True)
        self.assertTrue(all(p.exists() for p in self.paths))

    def test_symlink_and_hardlink_refuse(self):
        for link_type in ('symbolic', 'hard'):
            with self.subTest(link_type=link_type):
                target = self.paths[-1]
                target.unlink()
                if link_type == 'symbolic':
                    target.symlink_to(self.paths[0])
                else:
                    os.link(self.paths[0], target)
                with self.assertRaisesRegex(self.m.ApplyError, 'safe regular file'):
                    self.task.maintain(self.m, True)
                self.assertTrue(all(p.exists() for p in self.paths))

    def test_metadata_drift_refuses(self):
        self.paths[-1].chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            self.task.maintain(self.m, True)
        self.assertTrue(all(p.exists() for p in self.paths))

    def test_evidence_failure_before_attempt_preserves_every_file(self):
        with patch.object(self.task, 'save', side_effect=OSError('storage failure')):
            with self.assertRaises(OSError):
                self.task.maintain(self.m, True)
        self.assertTrue(all(p.exists() for p in self.paths))

    def test_interruption_stops_and_records_partial_deletion(self):
        unlink = Path.unlink
        def fail_second(path, *args, **kwargs):
            if path == self.paths[1]:
                raise OSError('interrupted')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_second):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                self.task.maintain(self.m, True)
        self.assertFalse(self.paths[0].exists())
        self.assertTrue(all(p.exists() for p in self.paths[1:]))
        self.assertTrue((self.task.EVIDENCE / 'failure.json').exists())


if __name__ == '__main__':
    unittest.main()
