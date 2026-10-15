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
from test_routing_profile import CN
PROFILE = dict(CN, **{"bootstrap-dns": ["1.1.1.1", "8.8.8.8"]})
from test_infrastructure_guest import load


class VPNEgressTests(ResourceTestCase):
    def test_controller_reconstruction_restores_declared_proxy_before_git(self):
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
        self.assertEqual((client['source'], client['destination']), ('192.168.100.11', '192.168.200.41'))
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
        workload = [r for r in rules if r['source'] == '192.168.100.11' and r['ports'] == [53]]
        self.assertEqual(len(workload), 2)
        self.assertEqual({r['destination'] for r in workload}, {'192.168.200.41'})
        config['vpn_egress']['clients'] = []
        _, rules, _ = vpn.compile_network(registry, registry['boxes'], topology)
        self.assertFalse(any(r['source'] == '192.168.125.10' and r['ports'] == [53] for r in rules))
        for invalid in (['223.5.5.5'] * 2, ['not-an-ip', '8.8.8.8'], [[], {}], ['127.0.0.1', '8.8.8.8']):
            with self.subTest(invalid=invalid), self.assertRaises(SystemExit):
                model.bootstrap_dns({'bootstrap_dns': invalid})

    def test_undeclared_gateway_cannot_open_bootstrap(self):
        with self.assertRaises(ValueError):
            vpn.compile_network({'boxes': {'boxa': {}}}, {'boxa': {}}, model.load_topology(repo_root=REPO_ROOT), bootstrap_boxes=['boxa'])

    def test_subscription_cannot_grant_routes_or_private_access(self):
        config = vpn.render_config('https://subscription.example/token', '192.168.200.41', ['192.168.125.10'], 'x' * 32, profile=PROFILE)
        self.assertFalse(config['tun']['enable'])
        self.assertTrue(config['dns']['enable'])
        self.assertEqual(config['dns']['proxy-server-nameserver'], ['1.1.1.1', '8.8.8.8'])
        self.assertEqual(config['dns']['listen'], '192.168.200.41:1053')
        self.assertEqual(config['dns']['nameserver'], ['https://1.1.1.1/dns-query#VPN', 'https://8.8.8.8/dns-query#VPN'])
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
        self.assertEqual(group['type'], 'url-test')
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
        self.assertEqual(config['dns']['nameserver-policy'], {'subscription.example': PROFILE['bootstrap-dns']})
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


if __name__ == '__main__': unittest.main()

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
