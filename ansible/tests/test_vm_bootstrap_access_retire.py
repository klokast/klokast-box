"""Exact first-contact SSH retirement and steady-state producer regression."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

import yaml


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'ansible/roles/vm-bootstrap-access-retire/files/retire-bootstrap-access'
loader = importlib.machinery.SourceFileLoader('vm_bootstrap_access_retire', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
b = importlib.util.module_from_spec(spec)
loader.exec_module(b)


class RetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host = 'boxa-dmz'
        self.address = '192.168.200.10'
        self.old = b'ssh-ed25519 AAAAold old-key'
        self.admin = b'ssh-ed25519 AAAAnew controller-key'
        self.admin_sha = b.digest(self.admin)
        self.uid = os.geteuid()
        self.paths = [self.root / name.lstrip('/') for name in b.PATHS]
        for path in self.paths:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.paths[0].write_bytes(self.old + b'\n')
        self.paths[1].write_bytes(self.old + b'\n' + self.admin + b'\n')
        self.paths[2].write_bytes((f'ListenAddress {self.address}\nPasswordAuthentication no\n'
                                   'KbdInteractiveAuthentication no\nPubkeyAuthentication yes\n'
                                   'PermitRootLogin prohibit-password\n').encode())
        for path in self.paths[:2]:
            path.chmod(0o600)
        self.paths[2].chmod(0o644)

    def preview(self):
        return b.inspect(self.host, self.address, self.admin_sha, self.root,
                         self.uid, self.uid)

    def retire(self, state):
        return b.retire(self.host, self.address, self.admin_sha, state,
                        self.root, self.uid, self.uid)

    def test_exact_preview_retires_only_three_files_and_is_idempotent(self):
        other = self.root / 'home/neo/.ssh/known_hosts'
        other.write_bytes(b'keep')
        before = self.preview()
        self.assertEqual(before['status'], 'present')
        result = self.retire(before['state_sha256'])
        self.assertTrue(result['removed'])
        self.assertTrue(all(not path.exists() for path in self.paths))
        self.assertEqual(other.read_bytes(), b'keep')
        clean = self.preview()
        self.assertEqual(clean['status'], 'absent')
        self.assertFalse(self.retire(clean['state_sha256'])['removed'])

    def test_changed_key_or_unexpected_key_blocks_cleanup(self):
        before = self.preview()
        self.paths[1].write_bytes(self.old + b'\n' + b'ssh-ed25519 AAAAextra unknown\n')
        with self.assertRaisesRegex(ValueError, 'unapproved'):
            self.retire(before['state_sha256'])
        self.assertTrue(all(path.exists() for path in self.paths))
        self.paths[1].write_bytes(self.old + b'\n')
        with self.assertRaisesRegex(ValueError, 'changed after'):
            self.retire(before['state_sha256'])
        self.assertTrue(all(path.exists() for path in self.paths))

    def test_modified_policy_link_and_live_sshd_block_cleanup(self):
        before = self.preview()
        self.paths[2].write_bytes(self.paths[2].read_bytes() + b'PermitTunnel yes\n')
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.preview()
        self.paths[2].write_bytes((f'ListenAddress {self.address}\nPasswordAuthentication no\n'
                                   'KbdInteractiveAuthentication no\nPubkeyAuthentication yes\n'
                                   'PermitRootLogin prohibit-password\n').encode())
        self.paths[0].unlink()
        self.paths[0].symlink_to(self.paths[1])
        with self.assertRaisesRegex(ValueError, 'type'):
            self.preview()
        self.paths[0].unlink()
        self.paths[0].write_bytes(self.old + b'\n')
        self.paths[0].chmod(0o600)
        service = self.root / 'etc/init.d/sshd'
        service.parent.mkdir(parents=True, exist_ok=True)
        service.write_bytes(b'old service')
        with self.assertRaisesRegex(ValueError, 'OpenSSH still'):
            self.retire(before['state_sha256'])
        self.assertTrue(all(path.exists() for path in self.paths))

    def test_port_22_listener_blocks_cleanup(self):
        before = self.preview()
        table = self.root / 'proc/net/tcp'
        table.parent.mkdir(parents=True)
        table.write_text('sl local_address rem_address st\n0: 00000000:0016 00000000:0000 0A\n')
        with self.assertRaisesRegex(ValueError, 'port 22 listener'):
            self.retire(before['state_sha256'])
        self.assertTrue(all(path.exists() for path in self.paths))


class ProvisioningTests(unittest.TestCase):
    def test_steady_state_does_not_recreate_open_ssh_keys(self):
        base = yaml.safe_load((REPO / 'ansible/roles/vm-base/tasks/main.yml').read_text())
        key = next(task for task in base if task['name'] == 'Ensure the VM admin authorized key is present')
        self.assertIn('vm_manage_sshd | default(true)', key['when'])
        for number in ('54-vm-dmz-podman.yml', '64-vm-iot-podman.yml',
                       '69-vm-podman-hosts.yml'):
            plays = yaml.safe_load((REPO / 'ansible/playbooks' / number).read_text())
            role_names = [task.get('ansible.builtin.import_role', {}).get('name')
                          for play in plays for task in play.get('tasks', [])]
            self.assertIn('vm-bootstrap-access-retire', role_names)


if __name__ == '__main__':
    unittest.main()
