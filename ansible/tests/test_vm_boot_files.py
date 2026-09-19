"""Separate boot filesystems must not escape no-application accounting."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_vm_storage_inventory import load_collector
from platform_updates import digest, UpdateError
import vm_no_application as noapp


class BootFiles(unittest.TestCase):
    def setUp(self):
        self.m = load_collector()
        work = tempfile.TemporaryDirectory(); self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.boot = self.root / 'boot'; self.boot.mkdir()
        self.boot.joinpath('vmlinuz-virt').write_bytes(b'kernel fixture')
        self.boot.joinpath('initramfs-virt').write_bytes(b'initramfs fixture')
        device = self.boot.stat().st_dev
        self.mounts = [{'path': '/boot', 'root': '/', 'type': 'ext4',
                        'device': f'{os.major(device)}:{os.minor(device)}'}]

    def collect(self):
        with patch.object(self.m, 'capture', side_effect=AssertionError('no artifact execution or mounts')):
            return self.m.collect_boot_files(self.root, self.mounts)

    def test_known_artifacts_are_hashed_but_unknown_contents_are_never_read_or_approved(self):
        unknown = self.boot / 'private-application-file'; unknown.write_bytes(b'SECRET APPLICATION CONTENT')
        value = self.collect()
        self.assertTrue(value['complete']); self.assertTrue(value['stable'])
        self.assertFalse(value['adoption_authorized'])
        self.assertEqual(set(value['artifacts']), {'/boot/vmlinuz-virt', '/boot/initramfs-virt'})
        self.assertIn('/boot/private-application-file', {r['path'] for r in value['metadata']['entries']})
        self.assertNotIn('SECRET', json.dumps(value))
        self.assertEqual(noapp.checked_boot_files(value, self.mounts), value)

    def test_missing_nested_or_wrong_filesystem_identity_refuses(self):
        for mounts in ([], [{**self.mounts[0], 'device': '999:999'}],
                       [{**self.mounts[0], 'type': 'nfs'}],
                       self.mounts + [{**self.mounts[0], 'path': '/boot/nested'}]):
            self.assertFalse(self.m.collect_boot_files(self.root, mounts)['complete'])

    def test_linked_artifact_hardlink_and_oversized_file_refuse(self):
        path = self.boot / 'vmlinuz-virt'
        path.unlink(); path.symlink_to('/etc/passwd')
        self.assertFalse(self.collect()['complete'])
        path.unlink(); path.write_bytes(b'kernel'); os.link(path, self.boot / 'alias')
        self.assertFalse(self.collect()['complete'])
        self.boot.joinpath('alias').unlink()
        with path.open('wb') as stream: stream.truncate(128 * 1024 * 1024 + 1)
        self.assertFalse(self.collect()['complete'])

    def test_changed_tree_or_linked_boot_root_refuses(self):
        original = self.m.unowned_tree; calls = []
        def changed(*args, **kwargs):
            result = original(*args, **kwargs); calls.append(True)
            if len(calls) == 1: self.boot.joinpath('new-file').write_bytes(b'changed')
            return result
        with patch.object(self.m, 'unowned_tree', side_effect=changed):
            self.assertFalse(self.collect()['stable'])
        self.boot.rename(self.root / 'outside'); self.boot.symlink_to('outside')
        self.assertFalse(self.collect()['complete'])

    def test_receiver_refuses_forged_coverage_and_spliced_mount(self):
        original = self.collect()
        for change in (
            lambda v: v.update(adoption_authorized=True),
            lambda v: v.update(stable=False),
            lambda v: v['metadata'].update(entries=[]),
            lambda v: v['artifacts'].pop('/boot/vmlinuz-virt'),
            lambda v: v['artifacts'].update({'/outside': 'a' * 64}),
            lambda v: v['mount'].update(device='999:999'),
        ):
            value = copy.deepcopy(original); change(value)
            value['evidence_sha256'] = digest({k: v for k, v in value.items() if k != 'evidence_sha256'})
            with self.assertRaises(UpdateError): noapp.checked_boot_files(value, self.mounts)


if __name__ == '__main__': unittest.main()
