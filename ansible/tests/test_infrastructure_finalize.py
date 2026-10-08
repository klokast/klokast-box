"""Infrastructure identity setup must retain numeric ownership and root locks."""
from types import SimpleNamespace
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


if __name__ == '__main__': unittest.main()
