import sys
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import platform_lan_print as lan


class LANPrintTest(unittest.TestCase):
    def setUp(self):
        self.topology = {'zones': {'bak': {'cidr': '192.168.100.0/24', 'vm_ipv4_address': '192.168.100.10',
            'role': 'backend', 'vm_interface': 'eth0', 'router_interface': 'eth3'}},
            'realms': {'wan': {'router_interface': 'eth0'}}}

    def test_discovery_and_ingress_share_one_endpoint(self):
        service, router, vm = lan.compile_service('boxa', {'rg_lan': {'ipv4_cidr': '192.168.1.0/24'}}, self.topology)
        files = lan.router_files({'lan_print_services': [service]}, 'boxa')
        xml = ET.fromstring(files[lan.SERVICE])
        self.assertEqual(xml.findtext('service/port'), '631')
        self.assertIn('rp=printers/epson', [t.text for t in xml.findall('service/txt-record')])
        self.assertIn('host-name=boxa-bak-printer', files[lan.CONFIG])
        self.assertIn('allow-interfaces=eth0', files[lan.CONFIG])
        self.assertIn('enable-reflector=no', files[lan.CONFIG])
        self.assertIn('ip saddr 192.168.1.0/24 fib daddr type local', files[lan.NAT])
        self.assertIn('dnat to 192.168.100.10:18631', files[lan.NAT])
        self.assertEqual(router['ports'], vm['ports'])
        self.assertEqual(vm['source'], '192.168.1.0/24')

    def test_missing_public_and_overlapping_subnets_fail(self):
        for subnet in ['', '0.0.0.0/0', '8.8.8.0/24', '192.168.100.0/24', '192.168.1.1/24']:
            with self.subTest(subnet=subnet), self.assertRaises(ValueError):
                lan.compile_service('boxa', {'rg_lan': {'ipv4_cidr': subnet}}, self.topology)

    def test_disabled_or_other_box_has_no_advertisement_or_forwarding(self):
        service, _, _ = lan.compile_service('boxa', {'rg_lan': {'ipv4_cidr': '192.168.1.0/24'}}, self.topology)
        for compiled in [{}, {'lan_print_services': [service]}]:
            files = lan.router_files(compiled, 'boxb')
            self.assertNotIn(lan.SERVICE, files)
            self.assertNotIn('dnat', files[lan.NAT])
            self.assertNotIn('accept', files[lan.INPUT])
