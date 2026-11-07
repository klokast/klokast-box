"""Print appliance policy and sealed filesystem boundary tests."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'roles/print-appliance/files/print_image.py'
spec=importlib.util.spec_from_file_location('print_image',SOURCE)
image=importlib.util.module_from_spec(spec);spec.loader.exec_module(image)


class PrintPolicyTest(unittest.TestCase):
    def test_only_printing_and_declared_infrastructure_are_allowed(self):
        rules=image.firewall(dict(gateway='192.168.100.1',printer='192.168.150.78',lan_subnet='192.168.1.0/24'))
        self.assertIn('ip daddr 192.168.150.78 tcp dport 631 accept',rules)
        self.assertIn('ip saddr 192.168.1.0/24 tcp dport 631 accept',rules)
        self.assertNotIn('dport 22',rules)
        self.assertNotIn('dport 80',rules)
        self.assertLess(rules.index('192.168.0.0/16'),rules.index('tcp dport 443 accept'))
        self.assertIn('chain forward { type filter hook forward priority 0; policy drop; }',rules)

    def test_disabled_lan_has_no_submission_allowance(self):
        rules=image.firewall(dict(gateway='192.168.100.1',printer='192.168.150.78',lan_subnet=''))
        self.assertNotIn('ip saddr',rules)
        self.assertIn('iifname "tailscale0" tcp dport 631 accept',rules)

    def test_configuration_replaces_symlink_without_following_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);outside=root/'outside';outside.write_text('unchanged')
            (root/'config').symlink_to(outside)
            image.put(root,'config','new')
            self.assertEqual(outside.read_text(),'unchanged')
            self.assertFalse((root/'config').is_symlink())


if __name__=='__main__':unittest.main()
