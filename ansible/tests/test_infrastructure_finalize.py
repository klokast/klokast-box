"""Infrastructure identity setup must retain numeric ownership and root locks."""
from types import SimpleNamespace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_infrastructure_guest import load, ROOT


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.module = load('infrastructure_finalize', ROOT / 'ansible/roles/vm-template-builder/files/vm-infrastructure-finalize')

    def test_maintenance_account_disables_passwords_without_locking_public_keys(self):
        account = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_dir='/home/neo')
        with patch.object(self.module.pwd, 'getpwnam', return_value=account), patch.object(self.module, 'run') as run:
            self.module.account('neo', 1000, 1000, wheel=True)
        self.assertIn((['chpasswd','-e'],), [v.args for v in run.call_args_list])
        self.assertEqual(run.call_args_list[0].kwargs, {'input':'neo:*\n'})
        self.assertFalse(any('root' in v.args[0] for v in run.call_args_list))

    def test_existing_numeric_identity_cannot_be_reassigned(self):
        account = SimpleNamespace(pw_uid=1007, pw_gid=1004, pw_dir='/home/agent')
        with patch.object(self.module.pwd, 'getpwnam', return_value=account), patch.object(self.module, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'ownership differs'):
                self.module.account('agent',1004,1004)
        run.assert_not_called()

    def test_custom_init_remounts_readonly_root_before_finalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {'kind': 'klokast.infrastructure-config.v1', 'box': 'boxa', 'role': 'air',
                      'address': '192.168.175.11/24', 'gateway': '192.168.175.254',
                      'bridge': 'br-usr', 'bootstrap_source': '192.168.100.1',
                      'public_key': 'ssh-ed25519 AAAATEST', 'agent_uid': 1004, 'agent_gid': 1004}
            content = json.dumps(config).encode(); digest = hashlib.sha256(content).hexdigest()
            for name, value in {
                'sys/hypervisor/type': b'xen\n',
                'sys/hypervisor/uuid': b'12345678-1234-1234-1234-123456789abc\n',
                'proc/cmdline': ('klokast_operation=' + 'a' * 24 + ' klokast_inputs=' + digest).encode(),
                'dev/xvdb': content + b'\0',
            }.items():
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(value)
            (root / 'sys/class/net/lo').mkdir(parents=True)
            commands = []
            def finalize(value, checksum):
                self.assertEqual(value, config); self.assertEqual(checksum, digest)
                self.assertIn(['mount', '-o', 'remount,rw', '/'], commands)
                commands.append(['finalized'])
            with patch.object(self.module, 'Path', side_effect=lambda p: root / str(p).lstrip('/')), \
                    patch.object(self.module.os, 'getpid', return_value=1), \
                    patch.object(self.module.os, 'geteuid', return_value=0), \
                    patch.object(self.module.os.path, 'ismount', return_value=True), \
                    patch.object(self.module.os, 'sync'), \
                    patch.object(self.module, 'run', side_effect=lambda argv: commands.append(argv)), \
                    patch.object(self.module, 'finalize', side_effect=finalize):
                self.assertEqual(self.module.main(), 0)
            self.assertEqual(commands[-2:], [['finalized'], ['mount', '-o', 'remount,ro', '/']])
            result = json.loads((root / 'dev/xvdc').read_bytes().split(b'\0', 1)[0])
            self.assertTrue(result['success'])


if __name__ == '__main__': unittest.main()
