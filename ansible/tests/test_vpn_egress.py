"""Check the shared proxy boundary and client revocation before live deployment."""
import copy
import contextlib
import io
import unittest
from platform_resource_test_support import ResourceTestCase, REPO_ROOT, model, compiler
import platform_vpn_egress as vpn
from test_infrastructure_guest import load


class VPNEgressTests(ResourceTestCase):
    def config(self):
        return {'access': {'enabled_capabilities': ['overlay', 'vpn-egress']},
                'vpn_egress': {'clients': ['boxa-ops']}}

    def test_exact_same_box_clients_and_revocation(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        config = self.config()
        registry = {'boxes': {'boxa': config}}
        guests, rules, vm_rules = vpn.compile_network(registry, registry['boxes'], topology)
        self.assertEqual(guests[0]['hostname'], 'boxa-vpn-egress')
        client = next(v for v in rules if v['ports'] == [7890])
        self.assertEqual((client['source'], client['destination']), ('192.168.125.10', '192.168.200.41'))
        self.assertTrue(all(v['app'] == 'platform' for v in rules + vm_rules))
        config['vpn_egress']['clients'] = []
        _, rules, _ = vpn.compile_network(registry, registry['boxes'], topology)
        self.assertFalse(any(v['ports'] == [7890] for v in rules))
        for invalid in (['boxb-ops'], ['boxa-dom0'], ['boxa-ops', 'boxa-ops'], ['boxa-air']):
            config['vpn_egress']['clients'] = invalid
            with self.assertRaises(ValueError): vpn.compile_network(registry, registry['boxes'], topology)

    def test_undeclared_gateway_cannot_open_bootstrap(self):
        with self.assertRaises(ValueError):
            vpn.compile_network({'boxes': {'boxa': {}}}, {'boxa': {}}, model.load_topology(repo_root=REPO_ROOT), bootstrap_boxes=['boxa'])

    def test_subscription_cannot_grant_routes_or_private_access(self):
        subscription = {'proxies': [{'name': 'relay', 'type': 'vmess', 'server': 'relay.example.com', 'port': 16617,
                                    'uuid': 'example', 'cipher': 'auto', 'alterId': 0}],
                        'rules': ['MATCH,DIRECT'], 'tun': {'enable': True}, 'proxy-providers': {'evil': {'path': '/etc/passwd'}}}
        config = vpn.render_config(subscription, '192.168.200.41', ['192.168.125.10'], 'x' * 32)
        self.assertFalse(config['tun']['enable'])
        self.assertTrue(config['dns']['enable'])
        self.assertEqual(config['dns']['proxy-server-nameserver'], ['1.1.1.1', '1.0.0.1'])
        self.assertNotIn('listen', config['dns'])
        self.assertEqual(config['rules'][-1], 'MATCH,VPN')
        self.assertNotIn('proxy-providers', config)
        self.assertNotIn('DIRECT', str(config))
        self.assertIn('IP-CIDR,100.64.0.0/10,REJECT', config['rules'])
        self.assertNotIn('no-resolve', str(config))
        for key, value in [('server', '127.0.0.1'), ('port', 22), ('dialer-proxy', 'DIRECT'), ('type', 'direct')]:
            changed = copy.deepcopy(subscription); changed['proxies'][0][key] = value
            with self.assertRaises(ValueError): vpn.render_config(changed, '192.168.200.41', [], 'x' * 32)

    def test_gateway_clone_profile_and_size(self):
        guest = load('vpn_guest_test', REPO_ROOT / 'ansible/roles/vm-template-builder/files/vm-infrastructure-finalize')
        value = {'kind': 'klokast.infrastructure-config.v1', 'box': 'boxa', 'role': 'vpn-egress',
                 'address': '192.168.200.41/24', 'gateway': '192.168.200.1', 'bridge': 'br-dmz',
                 'bootstrap_source': '192.168.100.1', 'public_key': 'ssh-ed25519 YWJj', 'agent_uid': 1004, 'agent_gid': 1004}
        self.assertEqual(guest.validate(value), value)
        value['bridge'] = 'br-ops'
        with self.assertRaises(RuntimeError): guest.validate(value)


if __name__ == '__main__': unittest.main()
