import importlib.util
from importlib.machinery import SourceFileLoader
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))
import dom0_updates


def module():
    loader = SourceFileLoader('dom0_native_test', str(ROOT / 'ansible/roles/dom0-update/files/dom0_update.py'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    value = importlib.util.module_from_spec(spec); loader.exec_module(value)
    return value


class Dom0Updates(unittest.TestCase):
    def setUp(self):
        self.native = module()

    def bundle(self, path, *, unsafe=None, change=False, qualified=True):
        data = {name: b'public boot bytes' for name in (
            'boot/xen.gz', 'boot/vmlinuz-lts', 'boot/initramfs-lts', 'boot/modloop-lts')}
        if unsafe: data[unsafe] = b'unsafe'
        metadata = {'kind': 'klokast.dom0-bundle.v1', 'branch': 'v3.24',
                    'offline_reconstruction_verified': qualified,
                    'files': {name: {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
                              for name, value in data.items()}}
        data['bundle.json'] = json.dumps(metadata).encode()
        if change: data['boot/xen.gz'] = b'changed bytes!!!!'
        with tarfile.open(path, 'w') as archive:
            for name, value in data.items():
                item = tarfile.TarInfo(name); item.size = len(value)
                archive.addfile(item, io.BytesIO(value))
        return self.native.sha(path)

    def test_selected_bundle_requires_exact_bytes_and_offline_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.tar'
            checksum = self.bundle(path)
            metadata, size = self.native.inspect_bundle(path, checksum)
            self.assertEqual(metadata['branch'], 'v3.24')
            self.assertEqual(size, 4 * len(b'public boot bytes'))
            for options in ({'change': True}, {'qualified': False}, {'unsafe': '../escape'}, {'unsafe': '/escape'}):
                checksum = self.bundle(path, **options)
                with self.subTest(options=options), self.assertRaises(RuntimeError):
                    self.native.inspect_bundle(path, checksum)
            self.bundle(path)
            with self.assertRaises(RuntimeError): self.native.inspect_bundle(path, '0' * 64)

    def test_bundle_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.tar'
            checksum = self.bundle(path)
            link = Path(directory) / 'link'; link.symlink_to(path)
            with self.assertRaises(RuntimeError): self.native.inspect_bundle(link, checksum)

    def test_no_overlay_restore_after_a_new_boot_or_started_reboot(self):
        for boot, state in (('new', 'selected'), ('old', 'rebooting')):
            with patch.object(self.native, 'boot_id', return_value=boot), self.assertRaisesRegex(RuntimeError, 'forbidden'):
                self.native.abort({'boot_before': 'old', 'state': state})

    def test_no_reboot_without_a_persisted_selection(self):
        with patch.object(self.native, 'boot_id', return_value='old'), \
                patch.object(self.native.subprocess, 'Popen') as start:
            with self.assertRaises(RuntimeError):
                self.native.reboot({'boot_before': 'old', 'state': 'prepared'})
            start.assert_not_called()

    def test_unrelated_post_boot_configuration_is_never_committed(self):
        current = {'tailscale_id': 'node', 'domains': {}}
        with patch.object(self.native, 'identity', return_value='node'), \
                patch.object(self.native, 'domains', return_value={}), \
                patch.object(self.native, 'run', return_value='U etc/shadow') as run:
            with self.assertRaisesRegex(RuntimeError, 'unrelated post-boot'):
                self.native.persist_runtime(current)
            run.assert_called_once_with(['lbu', 'status'])

    def test_boot_identity_change_blocks_persistence(self):
        with patch.object(self.native, 'identity', return_value='other'), \
                patch.object(self.native, 'run') as run:
            with self.assertRaises(RuntimeError):
                self.native.persist_runtime({'tailscale_id': 'node', 'domains': {}})
            run.assert_not_called()

    def test_unstopped_guest_never_causes_a_forced_host_reboot(self):
        record = {'boot_before': 'old', 'state': 'rebooting'}
        with patch.object(self.native, 'boot_id', return_value='old'), \
                patch.object(self.native, 'domains', return_value={'busy': 'uuid'}), \
                patch.object(self.native.time, 'monotonic', side_effect=[0, 301]), \
                patch.object(self.native.subprocess, 'Popen') as start:
            with self.assertRaisesRegex(RuntimeError, 'did not shut down'): self.native.reboot(record)
            start.assert_not_called()

    def test_grub_selects_generation_specific_boot_and_offline_repositories(self):
        value = self.native.grub(Path('/media/disk/klokast-dom0/abc'), Path('/media/disk'), 'rescue')
        self.assertIn('multiboot2 /klokast-dom0/abc/boot/xen.gz', value)
        self.assertIn('modloop=/klokast-dom0/abc/boot/modloop-lts', value)
        self.assertIn('alpine_repo=/media/disk/klokast-dom0/abc/repos/0,/media/disk/klokast-dom0/abc/repos/1', value)
        self.assertTrue(value.endswith('rescue'))

    def test_active_controller_box_is_never_a_reboot_target(self):
        view = {'instance': {'boxes': {'boxa': {}, 'boxb': {}}}, 'controllers': {'active': 'boxa', 'standby': 'boxb'}}
        with patch.object(dom0_updates.platform_source, 'snapshot', return_value=view):
            self.assertEqual(dom0_updates.authority('boxb'), view)
            with self.assertRaises(RuntimeError): dom0_updates.authority('boxa')

    def test_unqualified_host_is_skipped_before_any_prepare(self):
        view = {'instance': {'airunners': []}, 'controllers': {'active': 'boxa'}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dom0_updates, 'STATE', Path(directory)), \
                patch.object(dom0_updates, 'authority', return_value=view), \
                patch.object(dom0_updates, 'step', return_value={'pending': None, 'accepted': None}), \
                patch.object(dom0_updates.infrastructure_images, 'local_action') as build:
            result = dom0_updates.execute('boxb', lock_held=True)
            self.assertEqual(result['state'], 'skipped'); build.assert_not_called()

    def test_another_pending_host_operation_returns_the_exact_resume_id(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dom0_updates, 'STATE', Path(directory)), \
                patch.object(dom0_updates, 'authority', return_value={}), \
                patch.object(dom0_updates, 'step', return_value={'pending': {'operation': 'a' * 24}}):
            with self.assertRaisesRegex(RuntimeError, '--resume ' + 'a' * 24):
                dom0_updates.execute('boxb', image='b' * 24, lock_held=True)


if __name__ == '__main__': unittest.main()
