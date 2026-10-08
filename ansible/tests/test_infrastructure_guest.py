"""Infrastructure cloning must preserve disks across failures and repeated calls."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    return module


class CapacityTests(unittest.TestCase):
    def test_native_xl_spacing_and_missing_or_insufficient_capacity(self):
        module = load('infra_capacity', ROOT / 'ansible/roles/infrastructure-guest/files/infrastructure-guest-dom0')
        for output, error in (
            ('total_memory           : 32537\nfree_memory            : 20383\n', None),
            ('free_memory            : 4096\n', 'insufficient free Xen memory'),
            ('total_memory           : 32537\n', 'cannot read free Xen memory'),
            ('free_memory            : unknown\n', 'cannot read free Xen memory'),
        ):
            with self.subTest(output=output), patch.object(module, 'run', side_effect=[
                    SimpleNamespace(stdout=str(100 * 1024**3)), SimpleNamespace(stdout=output)]):
                if error:
                    with self.assertRaisesRegex(RuntimeError, error): module.capacity(50 * 1024**3)
                else:
                    module.capacity(50 * 1024**3)
        with patch.object(module, 'run', return_value=SimpleNamespace(stdout='0')) as run:
            with self.assertRaisesRegex(RuntimeError, 'insufficient LVM capacity'):
                module.capacity(50 * 1024**3)
            self.assertEqual(run.call_count, 1)


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
        self.archived_info = None
        self.current = None
        self.copies = 0
        self.boots = 0
        self.removals = 0
        def run(argv, **kwargs):
            if argv[0] == 'lvcreate':
                self.info = {'lv_uuid': 'test-lv', 'lv_tags': 'klokast-air-' + self.operation}
            if argv[0] == 'dd': self.copies += 1
            if argv[0] == 'lvremove':
                self.removals += 1
                if '_failed_' in str(argv[-1]): self.archived_info = None
                else: self.info = None
            if argv[0] == 'lvrename': self.archived_info, self.info = self.info, None
            if argv[:2] == ['xl','shutdown']: self.current = None
        def boot(work, name, identity, config, request, **kwargs):
            self.boots += 1
            (work / 'result.slot').write_text(json.dumps({**request, 'success': True}))
        for target, replacement in (
                ('safe_directory', lambda *_: None), ('safe_file', lambda *_: None),
                ('lv_info', lambda path: self.archived_info if '_failed_' in str(path) else self.info),
                ('domain', lambda name: self.current if name == 'air' else None),
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

    def failed_clone(self):
        def fail(work, name, identity, config, request, **kwargs):
            (work / 'result.slot').write_text(json.dumps({**request,
                'kind': 'klokast.infrastructure-finalize.v1', 'success': False, 'error': 'missing tool'}))
        with patch.object(self.module, 'boot_guest', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'guest finalization failed'): self.provision()

    def test_failed_clone_archive_retains_disk_identity_and_allows_fresh_allocation(self):
        self.failed_clone()
        original = self.info.copy()
        result = self.module.archive_failed('boxa')
        self.assertEqual(self.archived_info, original)
        self.assertIsNone(self.info)
        self.assertEqual(self.removals, 0)
        archive = Path(result['archive'])
        self.assertTrue((archive / 'result.slot').exists())
        self.assertEqual(json.loads((archive / 'assignment.json').read_text())['stage'], 'archived-failed')
        self.assertFalse(self.module.archive_failed('boxa')['changed'])
        self.assertEqual(self.provision()['state']['stage'], 'ready')
        self.assertEqual(self.archived_info, original)

    def test_failed_clone_archive_resumes_after_interrupted_lv_rename(self):
        self.failed_clone()
        original_run = self.module.run
        def interrupt(argv, **kwargs):
            original_run(argv, **kwargs)
            if argv[0] == 'lvrename': raise RuntimeError('disconnected after rename')
        with patch.object(self.module, 'run', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'disconnected'): self.module.archive_failed('boxa')
        self.assertTrue(self.module.archive_failed('boxa')['changed'])
        self.assertIsNotNone(self.archived_info)
        self.assertEqual(self.removals, 0)

    def test_archival_refuses_ready_guest_or_unproven_failure(self):
        self.provision()
        with self.assertRaisesRegex(RuntimeError, 'only an unfinalized'): self.module.archive_failed('boxa')
        assignment = self.module.BASE / 'air/assignment.json'
        state = json.loads(assignment.read_text()); state['stage'] = 'copied'
        assignment.write_text(json.dumps(state))
        (self.module.BASE / 'air/air.cfg').unlink()
        with self.assertRaisesRegex(RuntimeError, 'failed finalization evidence'): self.module.archive_failed('boxa')
        self.assertIsNone(self.archived_info)
        self.assertIsNotNone(self.info)

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

    def archived_failure(self):
        self.failed_clone()
        return Path(self.module.archive_failed('boxa')['archive'])

    def test_test_only_retirement_removes_recorded_failed_allocations_and_keeps_evidence(self):
        archive = self.archived_failure()
        proof = self.runner()
        self.module.retire('boxa', proof)
        self.assertEqual(self.removals, 2)
        self.assertIsNone(self.archived_info)
        self.assertTrue((archive / 'result.slot').exists())
        self.assertEqual(json.loads((archive / 'assignment.json').read_text())['stage'], 'retired-failed')
        self.assertFalse(self.module.retire('boxa', proof)['changed'])

    def test_failed_allocation_cleanup_resumes_after_removal_disconnect(self):
        archive = self.archived_failure()
        proof = self.runner()
        original_run = self.module.run
        def interrupt(argv, **kwargs):
            original_run(argv, **kwargs)
            if argv[0] == 'lvremove' and '_failed_' in str(argv[-1]):
                raise RuntimeError('disconnected after removal')
        with patch.object(self.module, 'run', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'disconnected'): self.module.retire('boxa', proof)
        self.module.retire('boxa', proof)
        self.assertEqual(self.removals, 2)
        self.assertEqual(json.loads((archive / 'assignment.json').read_text())['stage'], 'retired-failed')

    def test_changed_failed_allocation_is_preserved(self):
        self.archived_failure()
        proof = self.runner()
        self.archived_info['lv_uuid'] = 'unrelated-disk'
        with self.assertRaisesRegex(RuntimeError, 'archive LV identity differs'):
            self.module.retire('boxa', proof)
        self.assertIsNotNone(self.archived_info)
        self.assertEqual(self.removals, 1)

    def test_failed_allocation_without_failure_proof_is_preserved(self):
        archive = self.archived_failure()
        proof = self.runner()
        p = archive / 'result.slot'
        result = json.loads(p.read_text()); result['success'] = True
        p.write_text(json.dumps(result))
        with self.assertRaisesRegex(RuntimeError, 'matching failure evidence'):
            self.module.retire('boxa', proof)
        self.assertIsNotNone(self.archived_info)
        self.assertEqual(self.removals, 1)


if __name__ == '__main__':
    unittest.main()
