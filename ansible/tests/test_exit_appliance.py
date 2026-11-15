"""Exit appliance policy and fixed driver tests."""
import importlib.util
from pathlib import Path
import sys
import unittest

FILES=Path(__file__).resolve().parents[1]/'roles/appliance/files'
sys.path.insert(0,str(FILES))
spec=importlib.util.spec_from_file_location('exit_image',FILES/'exit_image.py')
image=importlib.util.module_from_spec(spec);spec.loader.exec_module(image)
CONFIG=dict(hostname='boxb-exit',gateway='192.168.200.1',network_interfaces='')


def chain(rules,name):
    start=rules.index(' chain '+name+' {')
    return rules[start:rules.index('\n }',start)]


class ExitPolicyTest(unittest.TestCase):
    def test_forwarding_allows_only_public_web_traffic(self):
        forward=chain(image.firewall(CONFIG),'forward')
        self.assertIn('policy drop;',forward)
        self.assertIn('tcp dport { 80, 443 } accept',forward)
        self.assertIn('udp dport { 443 } accept',forward)
        private=forward.index('192.168.0.0/16')
        self.assertLess(private,forward.index('tcp dport { 80, 443 } accept'))
        self.assertLess(private,forward.index('udp dport { 443 } accept'))
        for network in ('10.0.0.0/8','100.64.0.0/10','172.16.0.0/12','169.254.0.0/16'):
            self.assertIn(network,forward)
        self.assertNotIn('dport 22',forward)
        self.assertNotIn('dport 53',forward)

    def test_input_has_no_management_listener(self):
        inbound=chain(image.firewall(CONFIG),'input')
        self.assertIn('policy drop;',inbound)
        self.assertNotIn('dport 22',inbound)
        self.assertNotIn('iifname "eth0" tcp',inbound)

    def test_only_tailscale_clients_are_translated(self):
        rules=image.firewall(CONFIG)
        nat=rules[rules.index('table ip nat'):]
        self.assertIn('oifname "eth0" ip saddr 100.64.0.0/10 masquerade',nat)
        self.assertEqual(nat.count('masquerade'),1)

    def test_own_dns_uses_only_the_zone_router(self):
        output=chain(image.firewall(CONFIG),'output')
        self.assertIn('ip daddr 192.168.200.1 udp dport 53 accept',output)
        self.assertLess(output.index('192.168.200.1'),output.index('192.168.0.0/16'))


class ApplianceDriverTest(unittest.TestCase):
    def test_kinds_are_a_closed_set_with_fixed_print_names(self):
        spec=importlib.util.spec_from_file_location('appliance_dom0',FILES/'appliance-dom0.py')
        driver=importlib.util.module_from_spec(spec)
        sys.path.insert(0,str(FILES.parents[2]/'lib'))
        spec.loader.exec_module(driver)
        self.assertEqual(set(driver.APPLIANCES),{'print','exit'})
        driver.select('print')
        self.assertEqual((str(driver.BASE),driver.LV),('/mnt/dom0_data/klokast-print','/dev/vg0/lv_print_server_print'))
        driver.select('exit')
        self.assertEqual((str(driver.BASE),driver.LV),('/mnt/dom0_data/klokast-exit','/dev/vg0/lv_exit_node_exit'))
        with self.assertRaises(RuntimeError):driver.select('../print')


if __name__=='__main__':unittest.main()
