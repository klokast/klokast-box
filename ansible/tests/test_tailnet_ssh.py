"""Controller SSH to Tailnet hosts: network-map host keys, the same-box hop and path checks."""
from importlib.machinery import SourceFileLoader
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'ansible/bin/platform-known-hosts'
POLICY = ROOT / 'ansible/inventory-policy/group_vars'
ED25519 = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl'
RSA = 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDIneaT9u9VSZ='
STATUS = {
    'MagicDNSSuffix': 'tail.ts.net',
    'Self': {'DNSName': 'boxb-ops.tail.ts.net.', 'TailscaleIPs': ['100.64.0.1']},
    'Peer': {
        'a': {'DNSName': 'boxa-bak.tail.ts.net.', 'TailscaleIPs': ['100.64.0.2', 'fd7a::2'],
              'sshHostKeys': [ED25519, RSA + '\n'], 'Online': True},
        'b': {'DNSName': 'boxa-print.tail.ts.net.', 'TailscaleIPs': ['100.64.0.3'], 'Online': True},
        'c': {'DNSName': 'boxa-dmz.tail.ts.net.', 'TailscaleIPs': ['100.64.0.4'],
              'sshHostKeys': ['ssh-ed25519 AAAA AAAA'], 'Online': True},
        'd': {'DNSName': 'other.example.ts.net.', 'TailscaleIPs': ['100.64.0.5'],
              'sshHostKeys': [ED25519], 'Online': True},
    },
}


def load(path, name):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class KnownHostsCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='known-hosts-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        (self.directory / 'status.json').write_text(json.dumps(STATUS))
        stub = self.directory / 'tailscale'
        stub.write_text('#!/bin/sh\n[ "$*" = "status --json" ] || exit 9\n'
                        'cat "$TEST_STATUS"\nexit "${TEST_STATUS_RC:-0}"\n')
        stub.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.directory) + os.pathsep + os.environ['PATH'],
                    'TEST_STATUS': str(self.directory / 'status.json')}

    def run_helper(self, *args, **env):
        return subprocess.run([sys.executable, str(HELPER), *args], capture_output=True, text=True,
                              env={**self.env, **env}, check=False)

    def test_prints_the_published_keys_under_the_requested_name(self):
        for name in ('boxa-bak.tail.ts.net', 'boxa-bak.tail.ts.net.', 'boxa-bak', 'BOXA-BAK', '100.64.0.2'):
            with self.subTest(name=name):
                result = self.run_helper(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [name + ' ' + ED25519, name + ' ' + RSA])

    def test_refuses_names_that_do_not_select_one_key_holder(self):
        cases = {'boxa-print': 'publishes no SSH host key', 'nosuch': 'matches 0 peers',
                 'boxb-ops': 'is this machine', '100.64.0.1': 'is this machine',
                 'boxa-dmz': 'unsupported format', 'other': 'matches 0 peers',
                 '-oProxyCommand=x': 'usage', 'a b': 'not a Tailnet host name'}
        for name, message in cases.items():
            with self.subTest(name=name):
                result = self.run_helper(name)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, '')
                self.assertIn(message, result.stderr)

    def test_a_failed_status_read_prints_no_key(self):
        result = self.run_helper('boxa-bak', TEST_STATUS_RC='1')
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertIn('tailscale status failed', result.stderr)
        (self.directory / 'status.json').write_text('not json')
        result = self.run_helper('boxa-bak')
        self.assertEqual((result.returncode, result.stdout), (1, ''))


class InventoryTransportTests(unittest.TestCase):
    def setUp(self):
        self.all = yaml.safe_load((POLICY / 'all.yml').read_text())
        self.environment = jinja2.Environment()

    def render(self, *, hop, node='boxa', controller='boxb-ops', groups=None):
        host_keys = self.environment.from_string(self.all['platform_tailnet_host_key_args']).render()
        return self.environment.from_string(self.all['ansible_ssh_common_args']).render(
            platform_tailnet_host_key_args=host_keys, platform_same_box_hop=hop, node_name=node,
            platform_controller_hostname=controller, platform_magicdns_suffix='tail.ts.net',
            groups={'ops': ['boxa-ops', 'boxb-ops']} if groups is None else groups,
            lookup=lambda *args: 'unused')

    def test_every_connection_checks_the_network_map_without_recorded_keys(self):
        arguments = shlex.split(self.render(hop=False))
        self.assertEqual(arguments, ['-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
                                     '-o', 'UserKnownHostsFile=/dev/null', '-o', 'GlobalKnownHostsFile=/dev/null',
                                     '-o', 'KnownHostsCommand=/usr/local/bin/platform-known-hosts %H'])

    def test_remote_guest_uses_the_same_box_ops_with_the_same_host_key_rule(self):
        arguments = shlex.split(self.render(hop=True))
        proxy = arguments[arguments.index('-o', 9) + 1]
        self.assertTrue(proxy.startswith('ProxyCommand=ssh '))
        inner = shlex.split(proxy[len('ProxyCommand='):])
        self.assertIn('KnownHostsCommand=/usr/local/bin/platform-known-hosts %%H', inner)
        self.assertIn('StrictHostKeyChecking=yes', inner)
        self.assertEqual(inner[-4:], ['smith', '-W', '[%h]:%p', 'boxa-ops.tail.ts.net'])
        self.assertNotIn('accept-new', proxy)

    def test_no_hop_on_the_same_box_for_public_hosts_or_without_ops(self):
        for case in ({'hop': True, 'controller': 'boxa-ops'}, {'hop': False},
                     {'hop': True, 'groups': {'ops': ['boxb-ops']}}, {'hop': True, 'groups': {}}):
            with self.subTest(case=case):
                self.assertNotIn('ProxyCommand', self.render(**case))

    def test_guest_groups_and_checks_share_one_hop_list(self):
        groups = {'air': 'air', 'backend': 'bak', 'dmz': 'dmz', 'iot': 'iot', 'vpn_egress': 'vpn-egress'}
        self.assertIs(self.all['platform_same_box_hop'], False)
        for path in POLICY.glob('*.yml'):
            if path.stem != 'all':
                expected = True if path.stem in groups else None
                self.assertIs(yaml.safe_load(path.read_text()).get('platform_same_box_hop'), expected, path.name)
        paths = load(ROOT / 'ansible/bin/platform-tailnet-paths', 'tailnet_paths')
        sys.path.insert(0, str(ROOT / 'ansible/lib'))
        inspection = load(ROOT / 'ansible/lib/platform_update_inspection.py', 'inspection_hop')
        self.assertEqual(set(paths.GUESTS), set(groups.values()))
        self.assertEqual(set(inspection.SAME_BOX_HOP_GUESTS), set(groups.values()))
        host_keys = self.environment.from_string(self.all['platform_tailnet_host_key_args']).render()
        self.assertEqual(shlex.split(inspection.HOST_KEY_ARGS), shlex.split(host_keys))

    def test_steady_state_wrappers_do_not_trust_on_first_use(self):
        for name in ('ansible/inventory-policy/group_vars/all.yml', 'ansible/inventory-policy/group_vars/vpn_egress.yml',
                     'ansible/bin/converge-ops-controller', 'ansible/bin/converge-ops-airunner',
                     'ansible/bin/archive-codex-sessions', 'ansible/bin/overlay-ipv6-ops',
                     'ansible/bin/overlay-ipv6-router', 'ansible/lib/platform_resource_runtime.py',
                     'ansible/lib/platform_update_inspection.py'):
            with self.subTest(name=name):
                self.assertNotIn('accept-new', (ROOT / name).read_text())


class UpdateInspectionHopTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / 'ansible/lib'))
        self.inspection = load(ROOT / 'ansible/lib/platform_update_inspection.py', 'inspection_hop')
        self.targets = {'boxa-bak': {'box': 'boxa', 'role': 'bak', 'runtime': 'running'},
                        'boxa-vpn-egress': {'box': 'boxa', 'role': 'unknown', 'runtime': 'running'},
                        'boxa-router': {'box': 'boxa', 'role': 'router', 'runtime': 'running'},
                        'boxa-ops': {'box': 'boxa', 'role': 'ops', 'runtime': 'running'}}

    def args(self, host, controller='boxb-ops'):
        with patch.object(self.inspection.socket, 'gethostname', return_value=controller):
            return self.inspection.ssh_args(host, self.targets[host], self.targets, 'tail.ts.net')

    def test_remote_guests_use_the_hop_and_public_hosts_stay_direct(self):
        for host in ('boxa-bak', 'boxa-vpn-egress'):
            self.assertIn('-W [%h]:%p boxa-ops.tail.ts.net"', self.args(host))
            self.assertIn("platform-known-hosts %%H'", self.args(host))
        self.assertNotIn('ProxyCommand', self.args('boxa-router'))
        self.assertNotIn('ProxyCommand', self.args('boxa-bak', controller='boxa-ops'))
        self.targets['boxa-ops']['runtime'] = 'stopped'
        self.assertNotIn('ProxyCommand', self.args('boxa-bak'))
        self.assertIn('StrictHostKeyChecking=yes', self.args('boxa-bak'))


class PathCheckTests(unittest.TestCase):
    def setUp(self):
        self.paths = load(ROOT / 'ansible/bin/platform-tailnet-paths', 'tailnet_paths')
        self.status = {'Peer': {
            name: {'DNSName': name + '.tail.ts.net.', 'Online': name != 'boxa-iot'}
            for name in ('boxa-dom0', 'boxa-router', 'boxa-ops', 'boxa-bak', 'boxa-iot', 'boxa-print', 'boxc-dom0')}}

    def test_classify_reads_the_last_ping_line(self):
        self.assertEqual(self.paths.classify(0, 'pong from x (100.1.2.3) via [2408::1]:41641 in 236ms'),
                         ('direct', '[2408::1]:41641 236ms'))
        self.assertEqual(self.paths.classify(0, 'pong from x (100.1.2.3) via 192.168.1.39:41641 in 1ms')[0], 'direct')
        self.assertEqual(self.paths.classify(1, 'direct connection not established')[0], 'relayed')
        self.assertEqual(self.paths.classify(0, 'pong from x (100.1.2.3) via DERP(nue) in 224ms')[0], 'failed')
        self.assertEqual(self.paths.classify(1, 'no matching peer')[0], 'failed')

    def run_check(self, controller, local, remote):
        status = subprocess.CompletedProcess([], 0, json.dumps(self.status), '')
        def run(argv, **kwargs):
            if argv[:2] == ['tailscale', 'status']:
                return status
            if argv[:2] == ['tailscale', 'ssh']:
                self.remote_argv = argv
                rows = ''.join(f'{host}\t{rc}\t{line}\n' for host, (rc, line) in remote.items())
                return subprocess.CompletedProcess(argv, 0, rows, '')
            rc, line = local[argv[-1]]
            return subprocess.CompletedProcess(argv, rc, 'pong\n' + line + '\n', '')
        with patch.object(self.paths.socket, 'gethostname', return_value=controller), \
                patch.object(self.paths.subprocess, 'run', side_effect=run):
            return {item['host']: item for item in self.paths.check('boxa')}

    def test_remote_controller_checks_guests_from_their_ops(self):
        direct = (0, 'pong from x (100.1.2.3) via [2408::1]:41641 in 200ms')
        results = self.run_check('boxb-ops', {'boxa-dom0': direct, 'boxa-router': direct, 'boxa-ops': direct},
                                 {'boxa-bak': (1, 'direct connection not established')})
        self.assertEqual(self.remote_argv[:5], ['tailscale', 'ssh', 'smith@boxa-ops', 'sh', '-s'])
        self.assertEqual(self.remote_argv[-1], 'boxa-bak')
        self.assertEqual((results['boxa-bak']['from'], results['boxa-bak']['path']), ('boxa-ops', 'relayed'))
        self.assertEqual(results['boxa-iot']['path'], 'offline')
        self.assertEqual({results[h]['path'] for h in ('boxa-dom0', 'boxa-router', 'boxa-ops')}, {'direct'})
        self.assertNotIn('boxa-print', results)
        self.assertNotIn('boxc-dom0', results)

    def test_same_box_controller_checks_guests_directly(self):
        direct = (0, 'pong from x (100.1.2.3) via 192.168.1.39:41641 in 1ms')
        self.status['Peer'].pop('boxa-ops')
        results = self.run_check('boxa-ops', {h: direct for h in ('boxa-dom0', 'boxa-router', 'boxa-bak')}, {})
        self.assertEqual(results['boxa-bak']['from'], 'boxa-ops')
        self.assertEqual(results['boxa-bak']['path'], 'direct')
        self.assertFalse(hasattr(self, 'remote_argv'))

    def test_an_offline_hop_fails_its_guests(self):
        self.status['Peer']['boxa-ops']['Online'] = False
        direct = (0, 'pong from x (100.1.2.3) via [2408::1]:41641 in 200ms')
        results = self.run_check('boxb-ops', {'boxa-dom0': direct, 'boxa-router': direct}, {})
        self.assertEqual(results['boxa-bak']['path'], 'failed')
        self.assertIn('hop is unavailable', results['boxa-bak']['detail'])

    def test_exit_status_reports_problems(self):
        with patch.object(self.paths, 'check', return_value=[{'host': 'boxa-bak', 'from': 'boxa-ops',
                                                               'path': 'relayed', 'detail': 'x'}]):
            self.assertEqual(self.paths.main(['--box', 'boxa']), 1)
        with patch.object(self.paths, 'check', return_value=[{'host': 'boxa-bak', 'from': 'boxa-ops',
                                                               'path': 'direct', 'detail': 'x'}]):
            self.assertEqual(self.paths.main(['--box', 'boxa']), 0)
        self.assertEqual(self.paths.main(['--box', 'Box_A']), 2)


if __name__ == '__main__':
    unittest.main()
