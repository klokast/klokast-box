"""Check the shared proxy boundary and client revocation before live deployment."""
import copy
import contextlib
import io
import gzip
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from platform_resource_test_support import ResourceTestCase, REPO_ROOT, model, compiler
import platform_vpn_egress as vpn
from test_infrastructure_guest import load


class VPNEgressTests(ResourceTestCase):
    def config(self):
        return {'access': {'enabled_capabilities': ['overlay', 'vpn-egress']},
                'vpn_egress': {'clients': ['boxa-ops']}}

    def test_subscription_reference_does_not_change_network_authority(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        config = self.config()
        before = vpn.clients('boxa', config, topology)
        config['vpn_egress']['subscription_ref'] = 'family-vpn'
        self.assertEqual(vpn.clients('boxa', config, topology), before)
        for value in ('../private', '/tmp/secret', 'a.yml', '', 'https://provider/token'):
            config['vpn_egress']['subscription_ref'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'secret reference'):
                vpn.clients('boxa', config, topology)

    def test_subscription_references_use_separate_private_caches(self):
        import yaml
        cli = load('vpn_reference_test', REPO_ROOT / 'ansible/bin/platform-vpn-egress')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cli.PRIVATE, cli.CACHE = root / 'openclaw-vpn.yml', root / 'cache'
            archive = gzip.compress(b'\x7fELFtest-binary')
            for reference in ('alpha', 'beta'):
                subscription = ('proxies: []\n# ' + reference).encode()
                value = {'schema_version': 1, 'enabled': True, 'subscription_url': 'https://private.invalid/token',
                         'subscription': {'sha256': hashlib.sha256(subscription).hexdigest()},
                         'mihomo': {'download_url': 'https://public.invalid/mihomo.gz',
                                    'sha256': hashlib.sha256(archive).hexdigest()},
                         'controller': {'secret': 'private-test-value'}}
                path = root / (reference + '.yml'); path.write_text(yaml.safe_dump(value)); path.chmod(0o600)
                cache = cli.CACHE / reference; cache.mkdir(parents=True)
                (cache / 'mihomo.gz').write_bytes(archive)
                (cache / 'subscription.yml').write_bytes(subscription)
                with patch.object(cli.urllib.request, 'urlopen') as download:
                    _, _, binary = cli.private_config(subscription_ref=reference)
                    download.assert_not_called()
                    self.assertEqual(binary.parent, cache)
            with self.assertRaisesRegex(RuntimeError, 'secret reference'):
                cli.private_config(subscription_ref='../escape')

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
                        'rules': ['MATCH,VPN'], 'tun': {'enable': True},
                        'rule-providers': {'evil': {'url': 'https://attacker.example/rules'}},
                        'proxy-providers': {'evil': {'path': '/etc/passwd'}}}
        config = vpn.render_config(subscription, '192.168.200.41', ['192.168.125.10'], 'x' * 32)
        self.assertFalse(config['tun']['enable'])
        self.assertTrue(config['dns']['enable'])
        self.assertEqual(config['dns']['proxy-server-nameserver'], ['1.1.1.1', '1.0.0.1'])
        self.assertNotIn('listen', config['dns'])
        self.assertEqual(config['rules'][-1], 'MATCH,DIRECT')
        self.assertNotIn('MATCH,VPN', config['rules'])
        self.assertIn('RULE-SET,gfw,VPN', config['rules'])
        self.assertFalse(any(rule.startswith('DOMAIN-SUFFIX,github') for rule in config['rules']))
        self.assertLess(config['rules'].index('IP-CIDR,100.64.0.0/10,REJECT'),
                        config['rules'].index('RULE-SET,gfw,VPN'))
        provider = config['rule-providers']['gfw']
        self.assertEqual(set(config['rule-providers']), {'gfw'})
        self.assertEqual(provider['behavior'], 'domain')
        self.assertEqual(provider['proxy'], 'VPN')
        self.assertEqual(provider['interval'], 86400)
        self.assertLessEqual(provider['size-limit'], 4 * 1024 * 1024)
        self.assertTrue(provider['url'].startswith('https://raw.githubusercontent.com/Loyalsoldier/clash-rules/'))
        group = config['proxy-groups'][0]
        self.assertEqual(group['type'], 'url-test')
        self.assertEqual(group['interval'], 0)
        self.assertNotIn('proxies', group)  # Direct members silently enable a 300s timer.
        self.assertEqual(group['use'], ['relays'])
        self.assertFalse(config['profile']['store-selected'])
        self.assertNotIn('proxies', config)
        self.assertEqual(set(config['proxy-providers']), {'relays'})
        relays = config['proxy-providers']['relays']
        self.assertEqual(set(relays), {'type', 'payload', 'health-check'})
        self.assertEqual(relays['type'], 'inline')
        self.assertFalse(relays['health-check']['enable'])
        self.assertEqual(relays['health-check']['interval'], 0)
        self.assertEqual(relays['health-check']['url'], group['url'])
        self.assertEqual([p['name'] for p in relays['payload']], ['relay'])
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
