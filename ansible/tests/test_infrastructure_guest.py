"""Infrastructure cloning must preserve disks across failures and repeated calls."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    return module


class CloneTests(unittest.TestCase):
    def setUp(self):
        self.module = load('infra_dom0', ROOT / 'ansible/roles/infrastructure-guest/files/infrastructure-guest-dom0')
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.module.BASE = self.root / 'guests'
        self.module.IMAGES = self.root / 'images'
        self.module.XEN = self.root / 'xen'
        (self.module.XEN / 'auto').mkdir(parents=True)
        self.operation = 'a' * 24
        image = self.module.IMAGES / self.operation; image.mkdir(parents=True)
        artifacts = {}
        for name in ('root', 'kernel', 'initramfs'):
            content = ('synthetic-' + name).encode(); (image / name).write_bytes(content)
            artifacts[name] = {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        (image / 'candidate.json').write_text(json.dumps({'box': 'boxa', 'operation_id': self.operation,
            'success': True, 'validation': 'base-boot-tested', 'artifacts': artifacts}))
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'box': 'boxa', 'role': 'air', 'bridge': 'br-usr'}))
        self.info = None
        self.current = None
        self.copies = 0
        self.boots = 0
        self.removals = 0
        def run(argv, **kwargs):
            if argv[0] == 'lvcreate':
                self.info = {'lv_uuid': 'test-lv', 'lv_tags': 'klokast-air-' + self.operation}
            if argv[0] == 'dd': self.copies += 1
            if argv[0] == 'lvremove': self.removals += 1; self.info = None
            if argv[:2] == ['xl','shutdown']: self.current = None
        def boot(work, name, identity, config, request, **kwargs):
            self.boots += 1
            (work / 'result.slot').write_text(json.dumps({**request, 'success': True}))
        for target, replacement in (
                ('safe_directory', lambda *_: None), ('safe_file', lambda *_: None),
                ('lv_info', lambda *_: self.info), ('domain', lambda name: self.current if name == 'air' else None),
                ('capacity', lambda *_: None), ('run', run), ('boot_guest', boot),
                ('attach_loop', lambda path, **kwargs: '/dev/loop0'), ('detach_loop', lambda *_: None)):
            mock = patch.object(self.module, target, replacement); mock.start(); self.addCleanup(mock.stop)
        for target, replacement in (('gethostname', lambda: 'boxa-dom0'),):
            mock = patch.object(self.module.socket, target, replacement); mock.start(); self.addCleanup(mock.stop)
        mock = patch.object(self.module.os, 'geteuid', return_value=0); mock.start(); self.addCleanup(mock.stop)

    def provision(self):
        return self.module.provision('boxa', 'air', self.operation, self.config)

    def test_unknown_existing_disk_refuses_before_copy_or_stop(self):
        self.info = {'lv_uuid': 'user-owned', 'lv_tags': ''}
        with self.assertRaisesRegex(RuntimeError, 'existing unowned'): self.provision()
        self.assertEqual((self.copies, self.boots), (0, 0))

    def test_repeat_preserves_existing_data_and_running_identity(self):
        result = self.provision()
        self.current = {'domid': 5, 'config': {'c_info': {'uuid': result['state']['uuid']}}}
        self.assertFalse(self.provision()['changed'])
        self.assertEqual((self.copies, self.boots), (1, 1))
        self.current['config']['c_info']['uuid'] = 'different-guest'
        with self.assertRaisesRegex(RuntimeError, 'UUID changed'): self.provision()
        self.assertEqual(self.copies, 1)

    def test_failed_finalization_resumes_without_recopying_root(self):
        with patch.object(self.module, 'boot_guest', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'): self.provision()
        self.assertEqual(json.loads((self.module.BASE / 'air/assignment.json').read_text())['stage'], 'copied')
        self.assertEqual(self.provision()['state']['stage'], 'ready')
        self.assertEqual(self.copies, 1)

    def test_unavailable_capacity_does_not_allocate_assignment(self):
        with patch.object(self.module, 'capacity', side_effect=RuntimeError('insufficient LVM capacity')):
            with self.assertRaisesRegex(RuntimeError, 'capacity'): self.provision()
        self.assertFalse((self.module.BASE / 'air/assignment.json').exists())
        self.assertIsNone(self.info)

    def test_changed_image_never_allocates_a_disk(self):
        (self.module.IMAGES / self.operation / 'root').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'checksum differs'): self.provision()
        self.assertIsNone(self.info)

    def runner(self):
        result = self.provision()
        self.current = {'domid': 5, 'config': {'c_info': {'uuid': result['state']['uuid']}}}
        return {'uuid':result['state']['uuid'], 'config_sha256':result['state']['config_sha256'],
                'test_only':True, 'fresh':True}

    def test_retirement_retains_working_data_by_default(self):
        self.runner()
        result = self.module.retire('boxa')
        self.assertEqual(result['state']['stage'], 'retired-retained')
        self.assertEqual(self.removals, 0)
        self.assertIsNotNone(self.info)
        self.assertIsNone(self.current)

    def test_test_only_retirement_erases_only_the_recorded_disk(self):
        proof = self.runner()
        result = self.module.retire('boxa', proof)
        self.assertEqual(result['state']['stage'], 'retired-test-only')
        self.assertEqual(self.removals, 1)
        self.assertFalse(self.module.retire('boxa', proof)['changed'])

    def test_stale_proof_cannot_erase_a_running_vm(self):
        proof = self.runner(); proof['fresh'] = False
        with self.assertRaisesRegex(RuntimeError, 'proof differs'):
            self.module.retire('boxa', proof)
        self.assertEqual(self.removals, 0)
        self.assertIsNotNone(self.current)

    def test_changed_lv_identity_blocks_retirement(self):
        proof = self.runner(); self.info['lv_uuid'] = 'another-disk'
        with self.assertRaisesRegex(RuntimeError, 'LV identity differs'):
            self.module.retire('boxa', proof)
        self.assertEqual(self.removals, 0)
        self.assertIsNotNone(self.current)


if __name__ == '__main__':
    unittest.main()
