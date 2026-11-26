"""Check the shared proxy boundary and client revocation before live deployment."""
import copy
import contextlib
import io
import re
import gzip
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from platform_resource_test_support import ResourceTestCase, REPO_ROOT, model, compiler
import platform_vpn_egress as vpn
from test_routing_profile import CN
PROFILE = dict(CN, **{"bootstrap-dns": ["1.1.1.1", "8.8.8.8"]})
from test_infrastructure_guest import load


class VPNEgressTests(ResourceTestCase):
    def test_controller_reconstruction_converges_client_policy_before_git(self):
        import yaml
        tasks=yaml.safe_load((REPO_ROOT/'ansible/roles/ops-controller/tasks/main.yml').read_text())
        client=next(i for i,t in enumerate(tasks) if t.get('ansible.builtin.import_role',{}).get('name')=='vpn-egress-client')
        clone=next(i for i,t in enumerate(tasks) if t.get('name')=='Clone missing public infra repositories')
        self.assertLess(client,clone)
        defaults=yaml.safe_load((REPO_ROOT/'ansible/roles/vpn-egress-client/defaults/main.yml').read_text())
        self.assertEqual(defaults['vpn_egress_environment'],'{{ platform_vpn_egress_environment }}')
        client_tasks=yaml.safe_load((REPO_ROOT/'ansible/roles/vpn-egress-client/tasks/main.yml').read_text())
        git=next(t['ansible.builtin.blockinfile'] for t in client_tasks if 'ansible.builtin.blockinfile' in t)
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'gitconfig'
            path.write_text(git['block'].replace('{{ vpn_egress_environment.https_proxy }}','http://192.168.200.41:7890'))
            result=subprocess.check_output(['git','config','--file',str(path),'--get-urlmatch','http.proxy','https://github.com/klokast/klokast-box.git'],text=True)
            self.assertEqual(result.strip(),'http://192.168.200.41:7890')

    def config(self):
        return {'access': {'enabled_capabilities': ['overlay', 'vpn-egress']},
                'vpn_egress': {'clients': ['boxa-ops']}}

    def test_controller_tunnel_does_not_restore_underlay_client_access(self):
        import vpn_egress_provisioning as provisioning
        topology=model.load_topology(repo_root=REPO_ROOT)
        config=dict(self.config(),routing_profile=PROFILE)
        values=provisioning.variables('boxa',config,topology)
        self.assertEqual(values['vpn_egress_client_addresses'],[])
        self.assertTrue(values['vpn_egress_ops_downloads'])
        rendered=vpn.render_config('https://subscription.example/token','192.168.200.41',[],
                                   'x'*32,profile=PROFILE,controller_tunnel=True)
        self.assertEqual(rendered['lan-allowed-ips'],['192.168.200.41/32'])
        self.assertEqual(rendered['bind-address'],'192.168.200.41')
        revoked=vpn.render_config('https://subscription.example/token','192.168.200.41',[],
                                  'x'*32,profile=PROFILE)
        self.assertNotIn('192.168.200.41/32',revoked['lan-allowed-ips'])

    def test_private_download_uses_stdin_and_withholds_transport_errors(self):
        import subprocess
        import vpn_egress_provisioning as provisioning
        secret_url='https://subscription.example:5000/private-token'
        def run(argv, **kwargs):
            self.assertNotIn(secret_url, argv)
            self.assertIn(secret_url, kwargs['input'])
            self.assertIn('--ipv4', argv)
            self.assertIn('--max-time', argv)
            Path(argv[argv.index('--output')+1]).write_bytes(b'fixture')
            return subprocess.CompletedProcess(argv,0,stdout=secret_url,stderr='')
        with patch.object(provisioning.subprocess, 'run', side_effect=run):
            self.assertEqual(provisioning.download(secret_url, 100), b'fixture')
        with patch.object(provisioning.subprocess, 'run', side_effect=RuntimeError(secret_url)):
            with self.assertRaises(RuntimeError) as error: provisioning.download(secret_url,100)
            self.assertNotIn(secret_url,str(error.exception))

    def test_controller_seed_downloads_have_no_workload_router_grant(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        configs = {'boxa': self.config(), 'boxb': {}}
        _, rules, _ = vpn.compile_network({'boxes': configs}, configs, topology)
        seeds = [r for r in rules if r['resource'] == 'vpn-subscription-seed']
        self.assertEqual(seeds, [])
        self.assertFalse(any(r['source'] == topology['control_zones']['ops']['vm_ipv4_address'] for r in rules))
        configs['boxa'] = {}
        self.assertEqual(vpn.compile_network({'boxes': configs}, configs, topology)[1], [])

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
        cli = load('vpn_reference_test', REPO_ROOT / 'ansible/lib/vpn_egress_provisioning.py')
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
                with patch.object(cli, 'download') as download:
                    _, binary = cli.private_config(subscription_ref=reference)
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
        self.assertFalse(any(v['ports'] == [7890] for v in rules))
        config['vpn_egress']['clients'].append('boxa-bak')
        _, rules, vm_rules = vpn.compile_network(registry, registry['boxes'], topology)
        client = next(v for v in rules if v['ports'] == [7890])
        self.assertEqual((client['source'], client['destination']), ('192.168.100.10', '192.168.200.41'))
        self.assertTrue(all(v['app'] == 'platform' for v in rules + vm_rules))
        subscription = next(v for v in rules if v['resource'] == 'vpn-egress-subscription')
        self.assertEqual(subscription['ports'], [5000])
        self.assertEqual(subscription['source'], '192.168.200.41')
        config['vpn_egress']['clients'] = []
        _, rules, _ = vpn.compile_network(registry, registry['boxes'], topology)
        self.assertFalse(any(v['ports'] == [7890] for v in rules))
        for invalid in (['boxb-ops'], ['boxa-dom0'], ['boxa-ops', 'boxa-ops'], ['boxa-air']):
            config['vpn_egress']['clients'] = invalid
            with self.assertRaises(ValueError): vpn.compile_network(registry, registry['boxes'], topology)

    def test_country_dns_and_client_revocation(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        config = self.config()
        config['bootstrap_dns'] = ['223.5.5.5', '119.29.29.29']
        registry = {'boxes': {'boxa': config}}
        _, rules, _ = vpn.compile_network(registry, registry['boxes'], topology)
        bootstrap = [r for r in rules if r['source'] == '192.168.200.41' and r['ports'] == [53]]
        self.assertEqual({r['destination'] for r in bootstrap}, set(config['bootstrap_dns']))
        client = [r for r in rules if r['source'] == '192.168.125.10' and r['ports'] == [53]]
        self.assertEqual(client, [])
        config['vpn_egress']['clients'].append('boxa-bak')
        _, rules, _ = vpn.compile_network(registry, registry['boxes'], topology)
        workload = [r for r in rules if r['source'] == '192.168.100.10' and r['ports'] == [53]]
        self.assertEqual(len(workload), 2)
        self.assertEqual({r['destination'] for r in workload}, {'192.168.200.41'})
        config['vpn_egress']['clients'] = []
        _, rules, _ = vpn.compile_network(registry, registry['boxes'], topology)
        self.assertFalse(any(r['source'] == '192.168.125.10' and r['ports'] == [53] for r in rules))
        for invalid in (['223.5.5.5'] * 2, ['not-an-ip', '8.8.8.8'], [[], {}], ['127.0.0.1', '8.8.8.8']):
            with self.subTest(invalid=invalid), self.assertRaises(SystemExit):
                model.bootstrap_dns({'bootstrap_dns': invalid})

    def test_redirected_clients_reach_only_public_destinations_through_the_gateway(self):
        topology = model.load_topology(repo_root=REPO_ROOT)
        config = self.config()
        config['vpn_egress']['clients'].append('boxa-bak')
        registry = {'boxes': {'boxa': config}}
        _, rules, _ = vpn.compile_network(registry, registry['boxes'], topology)
        redirect = [r for r in rules if 'redirect' in r['resource']]
        self.assertEqual([(r['resource'], r['source'], r['ports'], r['protocol']) for r in redirect],
                         [('vpn-egress-client-redirect-bak', '192.168.100.10', [80, 443], 'tcp')])
        self.assertEqual(redirect[0]['out_interface'], topology['zones']['dmz']['router_interface'])
        self.assertEqual(redirect[0]['destination_exclusions'], vpn.PRIVATE4)
        rendered = compiler.render_router_resource_rule(compiler.normalized_router_rule(redirect[0]), 'x')
        self.assertIn('ip daddr != { 0.0.0.0/8, 10.0.0.0/8', rendered)
        self.assertIn('192.168.0.0/16', rendered)
        # The controller repairs the gateway and is never redirected.
        self.assertEqual(vpn.redirect_sources('boxa', config, topology), ['192.168.100.10'])
        config['vpn_egress']['clients'] = ['boxa-ops']
        _, rules, _ = vpn.compile_network(registry, registry['boxes'], topology)
        self.assertFalse(any('redirect' in r['resource'] for r in rules))
        self.assertEqual(vpn.redirect_sources('boxa', config, topology), [])

    def test_dns_follows_the_traffic_split(self):
        vpn_dns = [v + '#VPN' for v in PROFILE['vpn-dns']]
        # Each name that a rule sends through the VPN resolves through the VPN.
        listed = vpn.dns_servers(PROFILE)
        self.assertEqual(set(listed['nameserver-policy']),
                         {'pkgs.tailscale.com'} | {'rule-set:' + v for v in vpn.rule_providers(PROFILE)})
        self.assertTrue(all(v == vpn_dns for v in listed['nameserver-policy'].values()))
        self.assertEqual([r.rsplit(',', 1)[1] for r in vpn.routing_rules(PROFILE) if r.endswith(',VPN')], ['VPN', 'VPN'])
        self.assertEqual(listed['nameserver'], PROFILE['bootstrap-dns'])
        self.assertEqual(vpn.dns_servers(dict(PROFILE, **{'domain-list-url': None})),
                         {'nameserver': vpn_dns, 'nameserver-policy': {}})
        unlisted = vpn.render_config('https://subscription.example/token', '192.168.200.41', [], 'x' * 32,
                                     profile=dict(PROFILE, **{'domain-list-url': None}), controller_tunnel=True)
        self.assertEqual(unlisted['dns']['nameserver-policy'], {'subscription.example': PROFILE['bootstrap-dns']})

    def test_each_start_requests_one_bounded_relay_test_round(self):
        text = (REPO_ROOT / 'ansible/roles/vpn-egress-runtime/templates/mihomo.openrc.j2').read_text()
        script = text.split("<<'RELAY_TEST_ROUND'\n", 1)[1].split('\nRELAY_TEST_ROUND\n', 1)[0]
        compile(script, 'relay-test-round', 'exec')
        self.assertIn('/healthcheck', script)
        self.assertIn('deadline = time.monotonic() + 40', script)
        # The secret goes in a request header, never in argv or output.
        self.assertIn("python3 - /etc/vpn-egress/config.yml <<'RELAY_TEST_ROUND'", text)
        self.assertNotIn('print(config', script)
        post = text.split('start_post() {', 1)[1]
        self.assertIn('    return 0\n}', post)
        # Scheduled tests stay disabled in the native provider and group.
        self.assertEqual(vpn.relay_health_check()['enable'], False)
        self.assertEqual(vpn.relay_group()['interval'], 0)

    def test_redirect_listener_sniffs_names_without_resolving_again(self):
        config = vpn.render_config('https://subscription.example/token', '192.168.200.41', ['192.168.125.10'], 'x' * 32, profile=PROFILE)
        self.assertEqual(config['listeners'], [{'name': 'redirect', 'type': 'redir', 'port': vpn.REDIRECT_PORT,
                                                'listen': '192.168.200.41'}])
        self.assertNotIn('redir-port', config)
        self.assertEqual(config['sniffer'], {'enable': True, 'parse-pure-ip': True, 'override-destination': False,
                                             'sniff': {'TLS': {'ports': [443]}, 'HTTP': {'ports': [80]}}})
        self.assertEqual(config['bind-address'], '192.168.200.41')
        controller_only = vpn.render_config('https://subscription.example/token', '192.168.200.41', [], 'x' * 32,
                                            profile=PROFILE, controller_tunnel=True)
        self.assertNotIn('listeners', controller_only)
        self.assertNotIn('sniffer', controller_only)
        for profile in (PROFILE, dict(PROFILE, **{'domain-list-url': None})):
            rules = vpn.routing_rules(profile)
            direct = rules.index('AND,((IN-TYPE,REDIR),(DOMAIN-SUFFIX,tailscale.com)),DIRECT')
            self.assertLess(direct, rules.index('DOMAIN-SUFFIX,tailscale.com,REJECT'))
            self.assertGreater(direct, max(i for i, r in enumerate(rules) if r.startswith('IP-CIDR')))

    def test_router_policy_routing_renders_only_for_redirected_clients(self):
        import jinja2
        environment = jinja2.Environment(undefined=jinja2.ChainableUndefined, trim_blocks=True)
        environment.filters['bool'] = lambda value: str(value).lower() not in ('false', '0', '', 'none')
        template = environment.from_string((REPO_ROOT / 'ansible/roles/router/templates/interfaces.j2').read_text())
        values = dict(router_dmz_interface='eth2', router_vpn_egress_redirect_table=111,
                      router_vpn_egress_redirect_priority_base=12000)
        self.assertNotIn('post-up', template.render(**values))
        redirect = {'sources': ['192.168.175.11'], 'ports': [80, 443], 'gateway': '192.168.200.41', 'bypass': vpn.PRIVATE4}
        rendered = template.render(**values, router_vpn_egress_redirect=redirect)
        dmz = rendered[rendered.index('iface eth2'):]
        dmz = dmz[:dmz.index('\nauto ')]
        lines = [line.strip() for line in dmz.splitlines()]
        self.assertIn('post-up ip -4 route replace table 111 default via 192.168.200.41 dev eth2', lines)
        self.assertIn('post-up ip -4 rule add priority 12006 from 192.168.175.11 to 192.168.0.0/16 lookup main', lines)
        self.assertIn('post-up ip -4 rule add priority 12091 from 192.168.175.11 ipproto tcp dport 443 lookup 111', lines)
        self.assertEqual(sum(line.startswith('post-up ip -4 rule add') for line in lines),
                         sum(line.startswith('pre-down ip -4 rule del') for line in lines))
        verification = (REPO_ROOT / 'ansible/roles/router-verification/tasks/main.yml').read_text()
        self.assertIn('{first + 90 + n}: from {source} ipproto tcp dport {port} lookup {table}', verification)

    def test_router_configuration_identity_changes_only_with_redirected_clients(self):
        compiled = {'box_configs': {'boxa': {'access': {}}, 'boxb': {'access': {}}},
                    'vpn_egress_redirect': {'boxa': {'sources': ['192.168.175.11']}}}
        with patch.object(compiler, 'router_managed_dhcp_hosts', return_value=[]):
            self.assertEqual(compiler.box_access_router_vars(compiled, 'boxa')['router_vpn_egress_redirect'],
                             {'sources': ['192.168.175.11']})
            self.assertNotIn('router_vpn_egress_redirect', compiler.box_access_router_vars(compiled, 'boxb'))
            del compiled['vpn_egress_redirect']
            self.assertNotIn('router_vpn_egress_redirect', compiler.box_access_router_vars(compiled, 'boxa'))

    def test_gateway_redirects_only_declared_clients_to_public_destinations(self):
        import jinja2
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined, trim_blocks=True)
        template = environment.from_string((REPO_ROOT / 'ansible/roles/vpn-egress-runtime/templates/nftables.nft.j2').read_text())
        rendered = template.render(vpn_egress_client_addresses=['192.168.175.11'], platform_vpn_egress={'ipv4_address': '192.168.200.41'},
                                   vpn_egress_private4=vpn.PRIVATE4, vpn_egress_redirect_ports=vpn.REDIRECT_TCP_PORTS,
                                   vpn_egress_redirect_port=vpn.REDIRECT_PORT, vpn_egress_user={'uid': 101})
        redirect = [line.strip() for line in rendered.splitlines() if 'redirect to :7892' in line]
        self.assertEqual(len(redirect), 1)
        self.assertTrue(redirect[0].startswith('iifname "eth0" ip saddr 192.168.175.11 ip daddr != { 0.0.0.0/8,'))
        self.assertIn('tcp dport { 80, 443 }', redirect[0])
        self.assertIn('ip saddr 192.168.175.11 ip daddr 192.168.200.41 tcp dport 7892 accept', rendered)
        self.assertIn('tcp dport { 7890, 7892 } drop', rendered)
        self.assertIn('chain forward {\n        type filter hook forward priority 0; policy drop;\n    }', rendered)
        unused = template.render(vpn_egress_client_addresses=[], platform_vpn_egress={'ipv4_address': '192.168.200.41'},
                                 vpn_egress_private4=vpn.PRIVATE4, vpn_egress_redirect_ports=vpn.REDIRECT_TCP_PORTS,
                                 vpn_egress_redirect_port=vpn.REDIRECT_PORT, vpn_egress_user={'uid': 101})
        self.assertNotIn('redirect to :7892', unused)

    def test_gateway_creation_has_no_bootstrap_ssh_flow_or_key(self):
        import vpn_egress_provisioning as provisioning
        topology = model.load_topology(repo_root=REPO_ROOT)
        registry = {'boxes': {'boxa': self.config()}}
        _, rules, _ = vpn.compile_network(registry, registry['boxes'], topology)
        self.assertTrue(rules)
        self.assertFalse(any(22 in rule['ports'] or 'bootstrap' in rule['resource'] for rule in rules))
        with patch.object(provisioning.Path, 'home', side_effect=AssertionError('SSH key must not be read')):
            public = provisioning.creation_config('boxa', topology, PROFILE)
        self.assertNotIn('public_key', public); self.assertNotIn('bootstrap_source', public)
        play = (REPO_ROOT / 'ansible/playbooks/69-vpn-egress-provision.yml').read_text()
        self.assertNotIn('sshd', play); self.assertNotIn('vm-tailscale-enrollment', play)
        self.assertIn("['tag:vm', 'tag:vpn-egress']", play)

    def test_subscription_cannot_grant_routes_or_private_access(self):
        config = vpn.render_config('https://subscription.example/token', '192.168.200.41', ['192.168.125.10'], 'x' * 32, profile=PROFILE)
        self.assertFalse(config['tun']['enable'])
        self.assertTrue(config['dns']['enable'])
        self.assertEqual(config['dns']['proxy-server-nameserver'], ['1.1.1.1', '8.8.8.8'])
        self.assertEqual(config['dns']['listen'], '192.168.200.41:1053')
        # Direct names use bootstrap DNS; listed names and the package host use VPN DNS.
        self.assertEqual(config['dns']['nameserver'], ['1.1.1.1', '8.8.8.8'])
        vpn_dns = ['https://1.1.1.1/dns-query#VPN', 'https://8.8.8.8/dns-query#VPN']
        self.assertEqual(list(config['dns']['nameserver-policy'].items()),
                         [('subscription.example', ['1.1.1.1', '8.8.8.8']), ('pkgs.tailscale.com', vpn_dns),
                          ('rule-set:selected', vpn_dns)])
        self.assertNotIn('fallback', config['dns'])
        self.assertEqual(config['rules'][-1], 'MATCH,DIRECT')
        self.assertNotIn('MATCH,VPN', config['rules'])
        self.assertIn('RULE-SET,selected,VPN', config['rules'])
        self.assertFalse(any(rule.startswith('DOMAIN-SUFFIX,github') for rule in config['rules']))
        self.assertLess(config['rules'].index('IP-CIDR,100.64.0.0/10,REJECT'),
                        config['rules'].index('RULE-SET,selected,VPN'))
        provider = config['rule-providers']['selected']
        self.assertEqual(set(config['rule-providers']), {'selected'})
        self.assertEqual(provider['behavior'], 'domain')
        self.assertEqual(provider['proxy'], 'VPN')
        self.assertEqual(provider['interval'], 86400)
        self.assertLessEqual(provider['size-limit'], 4 * 1024 * 1024)
        self.assertTrue(provider['url'].startswith('https://raw.githubusercontent.com/Loyalsoldier/clash-rules/'))
        group = config['proxy-groups'][0]
        self.assertEqual(group['type'], 'fallback')  # url-test keeps stale relays after updates.
        self.assertEqual(group['interval'], 0)
        self.assertNotIn('proxies', group)  # Direct members silently enable a 300s timer.
        self.assertEqual(group['use'], ['relays'])
        self.assertFalse(config['profile']['store-selected'])
        self.assertNotIn('proxies', config)
        self.assertEqual(set(config['proxy-providers']), {'relays'})
        relays = config['proxy-providers']['relays']
        self.assertEqual(relays['type'], 'http')
        self.assertEqual(relays['interval'], 86400)
        self.assertEqual(relays['header'], {'User-Agent': ['clash.meta']})
        self.assertEqual(relays['proxy'], 'DIRECT')
        self.assertFalse(relays['override']['udp'])
        self.assertIn('direct', relays['exclude-type'].split('|'))
        # The subscription host resolves with bootstrap DNS before any list entry.
        self.assertEqual(next(iter(config['dns']['nameserver-policy'].items())),
                         ('subscription.example', PROFILE['bootstrap-dns']))
        self.assertEqual(relays['payload'][0]['server'], '127.0.0.1')
        self.assertFalse(relays['health-check']['enable'])
        self.assertEqual(relays['health-check']['interval'], 0)
        self.assertEqual(relays['health-check']['url'], group['url'])
        self.assertIn('IP-CIDR,100.64.0.0/10,REJECT', config['rules'])
        self.assertNotIn('no-resolve', str(config))
        for url in ('http://subscription.example/token', 'https://127.0.0.1/token', 'https://example.com:8443/token'):
            with self.assertRaises(ValueError):
                vpn.render_config(url, '192.168.200.41', [], 'x' * 32, profile=PROFILE)
        self.assertNotEqual(vpn.relay_provider('https://subscription.example/a')['path'],
                            vpn.relay_provider('https://subscription.example/b')['path'])

    def test_region_and_reserved_name_exclusions(self):
        pattern = vpn.relay_provider('https://subscription.example/token')['exclude-filter']
        for name in ('Hong Kong 01', 'Hong-Kong', 'hong_kong', 'HONGKONG',
                     '香港 02', '🇭🇰 relay', 'HK', 'hk01', '[HK] relay',
                     'DIRECT', 'reject', 'VPN', 'COMPATIBLE', 'GLOBAL'):
            with self.subTest(name=name):
                self.assertIsNotNone(re.search(pattern, name))
        for name in ('Tokyo', 'Singapore', 'New York', 'Shkoder',
                     'SUBSCRIPTION-UNAVAILABLE'):
            with self.subTest(name=name):
                self.assertIsNone(re.search(pattern, name))

    def test_subscription_cache_is_not_configuration_intent(self):
        import vpn_egress_provisioning as provisioning
        topology = model.load_topology(repo_root=REPO_ROOT)
        config = dict(self.config(), routing_profile=PROFILE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / 'openclaw-vpn.yml'; private.write_text('protected source'); private.chmod(0o600)
            with patch.object(provisioning, 'PRIVATE', private), patch.object(provisioning, 'CACHE', root):
                before = provisioning.configuration_signature('boxa', config, topology, repo=REPO_ROOT)
                (root / 'subscription.yml').write_text('new dynamic subscription')
                self.assertEqual(before, provisioning.configuration_signature('boxa', config, topology, repo=REPO_ROOT))
                private.write_text('new subscription URL')
                self.assertNotEqual(before, provisioning.configuration_signature('boxa', config, topology, repo=REPO_ROOT))

    def test_refresh_only_updates_native_provider(self):
        import vpn_egress_provisioning as provisioning
        import vm_service_updates as updates
        cli = load('vpn_refresh_cli_test', REPO_ROOT / 'ansible/bin/platform-vpn-egress')
        config = dict(self.config(), routing_profile=PROFILE)
        snapshot = {'instance_sha256': 'fixture', 'rendered': {'projection': {
            'registry_sha256': 'fixture', 'registry': {'boxes': {'boxa': {'routing_profile': PROFILE}}}}}}
        with contextlib.ExitStack() as stack:
            for target, attribute, value in (
                (cli.source, 'require_controller', None), (cli.runtime, 'require_active_controller', None),
                (cli.source, 'snapshot', snapshot),
                (cli.model, 'registry_box_configs', {'boxa': config}),
                (cli.vpn_routing_runtime, 'select', (PROFILE, {'state': 'ready'})),
                (cli.vpn_routing_runtime, 'installed_profiles', {}),
                (cli.vpn_routing_runtime, 'installed_air_bootstrap', {}),
                (updates, 'step', {'pending': []})):
                stack.enter_context(patch.object(target, attribute, return_value=value))
            stack.enter_context(patch.object(cli.runtime, 'vm_update_installation_lock', return_value=contextlib.nullcontext()))
            prepare = stack.enter_context(patch.object(provisioning, 'prepare_configuration'))
            network = stack.enter_context(patch.object(provisioning, 'network'))
            call = stack.enter_context(patch.object(cli, 'ansible'))
            self.assertEqual(cli.main(['refresh', '--box', 'boxa']), 0)
            prepare.assert_not_called(); network.assert_not_called()
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.args[:2], ('69-vpn-egress-provider.yml', 'boxa-vpn-egress'))
            self.assertEqual(call.call_args.args[2]['vpn_egress_provider_action'], 'refresh')

    def test_gateway_clone_profile_and_size(self):
        guest = load('vpn_guest_test', REPO_ROOT / 'ansible/roles/vm-template-builder/files/vm-infrastructure-finalize')
        value = {'kind': 'klokast.infrastructure-config.v1', 'box': 'boxa', 'role': 'vpn-egress',
                 'address': '192.168.200.41/24', 'gateway': '192.168.200.1', 'bridge': 'br-dmz',
                 'bootstrap_source': '192.168.100.1', 'public_key': 'ssh-ed25519 YWJj', 'agent_uid': 1004, 'agent_gid': 1004}
        value['bootstrap_dns'] = ['223.5.5.5', '119.29.29.29']
        self.assertEqual(guest.validate(value), value)
        value['bridge'] = 'br-ops'
        with self.assertRaises(RuntimeError): guest.validate(value)



class InstalledRoutingNetworkTests(ResourceTestCase):
    def test_installed_resolvers_survive_desired_edits_but_client_revocation_applies(self):
        from test_routing_profile import CN, FR
        config={'access':{'enabled_capabilities':['overlay','vpn-egress']},
                'bootstrap_dns':FR['bootstrap-dns'],'routing_profile':FR,
                'vpn_egress':{'clients':[]}}
        topology=model.load_topology(repo_root=REPO_ROOT)
        _,rules,_=vpn.compile_network({'boxes':{'boxa':config}},{'boxa':config},topology,installed_profiles={'boxa':CN})
        self.assertEqual({r['destination'] for r in rules if r['resource']=='vpn-egress-dns'},set(CN['bootstrap-dns']))
        self.assertFalse(any(r['ports']==[7890] for r in rules))


if __name__ == '__main__': unittest.main()
