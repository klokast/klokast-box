"""Test clone personalization and refusal of ambiguous persistent state."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/roles/vm-retained-data/files'))
sys.path.insert(0, str(REPO / 'ansible/roles/vm-personalize/files'))
import vm_personalize as p


from vm_personalize_test import fixture


class Personalization(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'root'; self.root.mkdir()
        self.retained = Path(temp.name) / 'retained'
        self.request = fixture(self.root, self.retained)
        for key, value in (('ROOT', self.root), ('RETAINED', self.retained)):
            mock = patch.object(p, key, value); mock.start(); self.addCleanup(mock.stop)
        mock = patch.object(p, 'mounted'); mock.start(); self.addCleanup(mock.stop)
        # Native tests verify filesystem mounts and numeric chown. Local tests
        # run as the developer, never request controller or root privileges.
        owner = patch.object(p.os, 'chown'); self.owner = owner.start(); self.addCleanup(owner.stop)
        commands = patch.object(p.data, 'run'); self.commands = commands.start(); self.addCleanup(commands.stop)

    def run_personalize(self):
        return p.personalize(self.request, time.monotonic() + 60)

    def test_exact_packages_numeric_ownership_and_retained_identity(self):
        db = (self.root / 'lib/apk/db/installed').read_bytes()
        identity = (self.retained / p.IDENTITY).read_bytes()
        receipt = self.run_personalize()
        self.assertFalse(receipt['adoption_accepted']); self.assertTrue(receipt['packages_unchanged'])
        self.assertEqual(p.data.runtime_identity(self.root), self.request['runtime'])
        self.assertEqual((self.root / 'lib/apk/db/installed').read_bytes(), db)
        self.assertEqual((self.retained / p.IDENTITY).read_bytes(), identity)
        self.assertFalse(any((self.root / 'var/lib/tailscale').iterdir()))
        self.assertIn('--state=/srv/retained/platform-tailscale-state', (self.root / 'etc/conf.d/tailscale').read_text())
        self.assertIn(self.request['retained_uuid'], (self.root / 'etc/fstab').read_text())
        self.owner.assert_called_once_with(self.root / 'home/neo', 2000, 2000)
        self.assertTrue(all(call.args[0][0] == 'sync' for call in self.commands.call_args_list))
        self.assertFalse((self.root / '.klokast-personalize-pending').exists())
        with self.assertRaisesRegex(p.PersonalizeError, 'already personalized'): self.run_personalize()

    def test_wrong_package_marker_or_retained_identity_refused_before_writes(self):
        for field, value in (('inputs_sha256', 'f' * 64), ('packages', {'linux-virt': '9-r0', 'tailscale': '2-r0', 'podman': '3-r0'}),
                             ('runtime', dict(self.request['runtime'], uid=2001))):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.request); changed[field] = value
                with self.assertRaises(p.PersonalizeError): p.personalize(changed, time.monotonic() + 60)
                self.assertFalse((self.root / '.klokast-personalize-pending').exists())
        self.assertNotIn('neo:', (self.root / 'etc/passwd').read_text())

    def test_path_injection_and_extra_files_are_refused(self):
        for name in ('etc/../shadow', 'etc/init.d/arbitrary', 'etc/apk/repositories', '/outside'):
            changed = copy.deepcopy(self.request); changed['files'][name] = 'bad\n'
            with self.assertRaises(p.PersonalizeError): p.validate(changed)
        changed = copy.deepcopy(self.request); changed['files']['etc/hostname'] = 'other-iot\n'
        with self.assertRaises(p.PersonalizeError): p.validate(changed)

    def test_account_collision_and_subordinate_overlap_are_refused(self):
        with (self.root / 'etc/passwd').open('a') as stream:
            stream.write('other:x:2000:100:Other:/var/empty:/sbin/nologin\n')
        with self.assertRaisesRegex(p.PersonalizeError, 'overlaps'): self.run_personalize()
        self.assertFalse((self.root / '.klokast-personalize-pending').exists())

    def test_identity_permissions_and_symlinks_are_refused(self):
        identity = self.retained / p.IDENTITY
        identity.chmod(0o644)
        with self.assertRaisesRegex(p.PersonalizeError, 'not private'): self.run_personalize()
        identity.chmod(0o600)
        outside = self.root.parent / 'outside'; outside.mkdir()
        (self.root / 'etc/network').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(p.PersonalizeError): self.run_personalize()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue((self.root / '.klokast-personalize-pending').exists())

    def test_interruption_poison_prevents_partial_clone_reuse(self):
        with patch.object(p, 'put', side_effect=InterruptedError('test interruption')):
            with self.assertRaises(InterruptedError): self.run_personalize()
        with self.assertRaisesRegex(p.PersonalizeError, 'interrupted'): self.run_personalize()
        self.assertFalse((self.root / 'etc/klokast-personalization.json').exists())


if __name__ == '__main__':
    unittest.main()
