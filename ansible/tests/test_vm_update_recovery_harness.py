#!/usr/bin/env python3
"""Verify that the native watchdog harness cannot select production resources."""
import copy
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'roles/vm-update-recovery/files'


def load(name, path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


h = load('recovery_harness', ROOT / 'vm-update-recovery-test')


class Harness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.t = load('harness_transaction', ROOT / 'vm-update-transaction')
        self.original = self.t.Native
        self.resources = {'domain': 'vm-recovery-test-' + 'a' * 24,
                          'uuids': {'old': 'old-uuid', 'new': 'new-uuid'},
                          'disks': {'/dev/vg/test': {}}}
        self.backend = h.test_backend(self.t, self.work, self.resources)

    def test_native_watch_and_process_identity_are_not_substituted(self):
        self.assertIs(type(self.backend).watch, self.original.watch)
        self.assertIs(type(self.backend).watcher_alive, self.original.watcher_alive)
        self.assertEqual(self.t.HELPER, str(self.work / 'vm-update-recovery-test'))
        self.assertEqual(self.t.BASE, self.work / 'state')
        self.assertEqual(self.t.XEN, self.work / 'xen')

    def test_cannot_start_production_or_unallocated_disks(self):
        with self.assertRaisesRegex(RuntimeError, 'production configuration'):
            self.backend.start(Path('/etc/xen/bak.cfg'))
        config = self.work / 'old.cfg'
        config.write_text('staged')
        with patch.object(self.t, 'configuration', return_value=({}, ['/dev/vg/production'])), \
                patch.object(self.t, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'did not allocate'):
                self.backend.start(config)
            command.assert_not_called()

    def test_start_forces_networkless_test_identity(self):
        config = self.work / 'old.cfg'
        config.write_text('staged')
        parsed = {'name': 'bak', 'uuid': 'old-uuid', 'vif': ['bridge=production'], 'memory': 4096}
        with patch.object(self.t, 'configuration', return_value=(parsed, ['/dev/vg/test'])), \
                patch.object(self.t, 'atomic') as atomic, patch.object(self.t, 'command') as command, \
                patch.object(h.time, 'sleep'):
            self.backend.start(config)
            rendered = atomic.call_args.args[1].decode()
            self.assertIn("vif = []", rendered)
            self.assertIn(self.resources['domain'], rendered)
            self.assertNotIn('production', rendered)
            command.assert_called_once_with(['xl', 'create', self.work / 'disposable.cfg'])

    def test_stop_refuses_production_and_reused_domain_id(self):
        record = {'domid': 9, 'config': {'c_info': {'name': 'bak', 'uuid': 'old-uuid'}}}
        for name, identity in [('bak', 'old-uuid'), (self.resources['domain'], 'foreign-uuid')]:
            live = copy.deepcopy(record)
            live['config']['c_info'].update(name=name, uuid=identity)
            with patch.object(self.original, 'inventory', return_value=[live]), \
                    patch.object(self.original, 'stop') as stop:
                with self.assertRaisesRegex(RuntimeError, 'production domain'):
                    self.backend.stop(record, False)
                stop.assert_not_called()

    def test_persistence_does_not_call_lbu(self):
        with patch.object(self.t, 'syncdir') as sync, patch.object(self.t, 'command') as command:
            self.backend.persist()
            sync.assert_called_once_with(self.work / 'xen')
            command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
