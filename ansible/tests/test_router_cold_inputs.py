"""The cold inspector boot capsule binds approved inputs and fixed guest code."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_inputs as cold
import router_generations as generations
from platform_updates import UpdateError


class InputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo, self.source, self.work = (self.root / name for name in ('repo', 'source', 'work'))
        for path in (self.repo / 'ansible/update-profiles',
                     self.repo / 'ansible/roles/router-cold-filesystem/files', self.source, self.work):
            path.mkdir(parents=True, exist_ok=True)
        (self.repo / 'ansible/update-profiles/router-alpine-v2.json').write_text('{}')
        self.guest = self.repo / 'ansible/roles/router-cold-filesystem/files/router-cold-filesystem-guest'
        self.guest.write_text('fixed guest source\n')
        self.manifest = {'inputs_sha256': 'a'*64, 'engine_commit': 'c'*40}
        (self.source / 'inputs.json').write_text(json.dumps(self.manifest))
        self.boot = {name: {'bytes': 100, 'sha256': name[0]*64}
                     for name in ('kernel', 'initramfs')}

    def test_capsule_uses_exact_profile_engine_guest_and_boot_artifacts(self):
        with mock.patch.object(cold.router_updates, 'validate_inputs') as validated, \
             mock.patch.object(cold.vm_template_inputs, 'bootstrap', return_value=self.boot) as built:
            result = cold.prepare(self.source, self.work, self.repo,
                                  box='k001', operation='b'*24, engine='c'*40)
        validated.assert_called_once_with(self.manifest, {}, 'c'*40)
        built.assert_called_once_with(self.source, self.work / 'boot', self.guest,
                                      expected_profile='router-alpine-v2')
        self.assertEqual(generations.check_seal(result), None)
        self.assertEqual(result['guest_sha256'], hashlib.sha256(self.guest.read_bytes()).hexdigest())
        self.assertEqual(result['boot'], self.boot)
        self.assertEqual(json.loads((self.work / 'filesystem-bootstrap.json').read_text()), result)
        with self.assertRaisesRegex(UpdateError, 'new private operation'):
            cold.prepare(self.source, self.work, self.repo,
                         box='k001', operation='b'*24, engine='c'*40)

    def test_wrong_box_or_symlink_guest_refuses_before_build(self):
        with mock.patch.object(cold.router_updates, 'validate_inputs'), \
             mock.patch.object(cold.vm_template_inputs, 'bootstrap') as built:
            with self.assertRaisesRegex(UpdateError, 'K001'):
                cold.prepare(self.source, self.work, self.repo,
                             box='k002', operation='b'*24, engine='c'*40)
            self.guest.unlink()
            self.guest.symlink_to(self.root / 'missing')
            with self.assertRaisesRegex(UpdateError, 'guest source'):
                cold.prepare(self.source, self.work, self.repo,
                             box='k001', operation='b'*24, engine='c'*40)
            built.assert_not_called()

    def test_guest_change_during_bootstrap_refuses_capsule(self):
        def changed(*args, **kwargs):
            self.guest.write_text('changed guest source\n')
            return self.boot
        with mock.patch.object(cold.router_updates, 'validate_inputs'), \
             mock.patch.object(cold.vm_template_inputs, 'bootstrap', side_effect=changed):
            with self.assertRaisesRegex(UpdateError, 'changed during bootstrap'):
                cold.prepare(self.source, self.work, self.repo,
                             box='k001', operation='b'*24, engine='c'*40)
        self.assertFalse((self.work / 'filesystem-bootstrap.json').exists())

    def test_transfer_freezes_real_parts_and_refuses_changed_boot_bytes(self):
        (self.work / 'boot').mkdir(mode=0o700)
        boot = {}
        for name in ('kernel', 'initramfs'):
            payload = name.encode() * 3
            path = self.work / 'boot' / name; path.write_bytes(payload); path.chmod(0o600)
            boot[name] = {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        capsule = generations.seal({'boot': boot})
        result = cold.transfer(self.work, capsule)
        self.assertEqual(result['capsule'], capsule)
        for name in boot:
            part = self.work / ('parts-' + name) / 'part-0000'
            self.assertEqual(part.read_bytes(), (self.work / 'boot' / name).read_bytes())
            self.assertEqual(result['parts'][name][0]['sha256'], boot[name]['sha256'])
        with self.assertRaisesRegex(UpdateError, 'new bounded input'): cold.transfer(self.work, capsule)


if __name__ == '__main__':
    unittest.main()
