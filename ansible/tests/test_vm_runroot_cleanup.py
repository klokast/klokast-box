#!/usr/bin/env python3
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'ansible/roles/vm-update-runroot-cleanup/files/cleanup-vm-runroot'
PLAYBOOK = REPO / 'ansible/playbooks/74-platform-update-runroot-cleanup.yml'


def module():
    loader = SourceFileLoader('runroot_cleanup', str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    value = importlib.util.module_from_spec(spec)
    loader.exec_module(value)
    return value


class RunrootCleanupTest(unittest.TestCase):
    def setUp(self):
        self.m = module()

    def test_apply_requires_the_exact_preview_and_rechecks_runtime(self):
        entries = [{'path': 'libpod/tmp/events/events.log', 'type': 'regular', 'mode': 0o600,
                    'size': 0, 'mtime_ns': 1, 'inode': 2, 'sha256': 'a' * 64}]
        checksum = self.m.hashlib.sha256(self.m.canonical(entries).encode()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'storage-run-1000'
            root.mkdir()
            with patch.object(self.m, 'ROOT', root), patch.object(self.m, 'inspect', return_value=entries), \
                    patch.object(self.m, 'tree', return_value=entries), patch.object(self.m, 'runtime_clear') as clear:
                with self.assertRaisesRegex(ValueError, 'changed since preview'):
                    self.m.execute('k001-dmz', 'a' * 24, True, 'b' * 64)
                receipt = self.m.execute('k001-dmz', 'a' * 24, True, checksum)
                self.assertTrue(receipt['applied'])
                self.assertFalse(root.exists())
                self.assertEqual(clear.call_count, 1)

    def test_tree_refuses_a_linked_runtime_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'storage-run-1000'
            root.mkdir()
            (root / 'linked').symlink_to('/tmp')
            with patch.object(self.m, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'linked'):
                    self.m.tree()

    def test_playbook_binds_apply_to_a_private_preview_and_no_application_intent(self):
        value = PLAYBOOK.read_text(encoding='utf-8')
        self.assertIn('runroot_cleanup_apply', value)
        self.assertIn('runroot_cleanup_preview', value)
        self.assertIn('intent.workloads == []', value)
        self.assertIn('intent.datasets == []', value)
        self.assertIn('cleanup-vm-runroot', value)
        self.assertIn('force: false', value)


if __name__ == '__main__':
    unittest.main()
