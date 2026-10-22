"""New package accounts must not take retained router service IDs."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from test_router_template import module


class BonjourAccounts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'etc').mkdir()
        self.guest = module('router-template-guest')
        (self.root/'etc/passwd').write_text('root:x:0:0:root:/root:/bin/ash\nmessagebus:x:103:103:messagebus:/dev/null:/sbin/nologin\ndnsmasq:x:104:104:dnsmasq:/dev/null:/sbin/nologin\n')
        (self.root/'etc/group').write_text('root:x:0:\nmessagebus:x:103:\ndnsmasq:x:104:\n')

    def test_reserves_package_identity_and_is_repeatable(self):
        with patch.object(self.guest.os,'chown'):
            self.guest.stable_messagebus_account(self.root)
            before = (self.root/'etc/passwd').read_text()
            self.guest.stable_messagebus_account(self.root)
        self.assertEqual((self.root/'etc/passwd').read_text(), before)
        self.assertIn('messagebus:x:81:81:', before)
        self.assertIn('dnsmasq:x:104:104:', before)
        self.assertIn('messagebus:x:81:', (self.root/'etc/group').read_text())

    def test_unrelated_account_conflict_is_refused_without_rewriting_tables(self):
        path = self.root/'etc/group'
        path.write_text(path.read_text()+'other:x:81:\n')
        before = path.read_text()
        with self.assertRaisesRegex(RuntimeError,'conflicts'):
            self.guest.stable_messagebus_account(self.root)
        self.assertEqual(path.read_text(), before)
