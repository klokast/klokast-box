"""The controller renders the Instance-dependent runner files for the finalizer."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/roles/vm-template-builder/files'))
import vm_infrastructure_personalize as personalize

PROXY = 'http://192.168.200.41:7890'
HOOK = """#!/bin/sh
# bootstrap-dns: 1.1.1.1 8.8.8.8
# Static network DNS is desired configuration; resolv.conf is runtime output.
set -eu
[ "${IFACE:-}" = eth0 ] || exit 0
/usr/sbin/nft -f /etc/klokast/dns-filter.nft
# Images and older guests can have resolver output without OpenResolv signature.
/usr/sbin/resolvconf -u
{
    printf 'nameserver %s\\n' NAMESERVERS
} | /usr/sbin/resolvconf -a eth0.klokast
"""


@unittest.skipUnless(shutil.which('ansible-playbook'), 'ansible-playbook is required')
class RunnerRender(unittest.TestCase):
    def render(self, clients):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for role in ('air-candidate', 'vm-dns', 'vpn-egress-client'):
                shutil.copytree(ROOT / 'ansible/roles' / role, work / 'roles' / role)
            (work / 'playbooks').mkdir()
            shutil.copy(ROOT / 'ansible/playbooks/74-air-candidate-render.yml', work / 'playbooks')
            stub = work / 'platform-source'
            stub.write_text('#!/bin/sh\necho \'{"controllers": {"active": {"box": "k002", "hostname": "k002-ops"},'
                            ' "standby": {"box": "k001", "hostname": "k001-ops"}}}\'\n')
            stub.chmod(0o700)
            tasks = work / 'roles/air-candidate/tasks/main.yml'
            tasks.write_text(tasks.read_text().replace('/usr/local/sbin/platform-source', str(stub)))
            proxy = {k: PROXY for k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY')}
            inventory = {'all': {'children': {'air': {'hosts': {'k002-air': {'node_hostname': 'k002-air',
                'ansible_python_interpreter': sys.executable}}}},
                'vars': {'platform_vpn_egress_clients': clients,
                         'platform_vpn_egress': {'ipv4_address': '192.168.200.41'},
                         'platform_vpn_egress_environment': proxy}}}
            (work / 'hosts.json').write_text(json.dumps(inventory))
            result = work / 'out/runner-files.json'; result.parent.mkdir()
            (work / 'args.json').write_text(json.dumps({'air_candidate_result': str(result),
                                                        'air_candidate_bootstrap_dns': ['1.1.1.1', '8.8.8.8']}))
            run = subprocess.run(['ansible-playbook', '-i', str(work / 'hosts.json'),
                                  str(work / 'playbooks/74-air-candidate-render.yml'),
                                  '--limit', 'k002-air', '-e', '@' + str(work / 'args.json')],
                                 cwd=work, capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, ANSIBLE_ROLES_PATH=str(work / 'roles')))
            self.assertEqual(run.returncode, 0, run.stdout[-3000:] + run.stderr[-3000:])
            return json.loads(result.read_text())

    def test_a_runner_outside_the_clients_keeps_the_bootstrap_resolvers(self):
        files = self.render([])
        self.assertEqual(set(files), {'etc/gitconfig', 'etc/claude-code/managed-settings.json',
                                      'etc/klokast/dns-filter.nft', 'etc/network/if-up.d/10-klokast-dns'})
        # These are the files that the former post-boot roles wrote on k002-air.
        self.assertEqual(files['etc/gitconfig'], '# BEGIN KLOKAST RUNNER AUTHOR\n[user]\n    name = Klokast Agent\n'
                                                 '    email = agent@k002-air\n# END KLOKAST RUNNER AUTHOR\n')
        self.assertEqual(files['etc/network/if-up.d/10-klokast-dns'],
                         HOOK.replace("NAMESERVERS\n", "1.1.1.1\n    printf 'nameserver %s\\n' 8.8.8.8\n"))
        self.assertNotIn('chain output', files['etc/klokast/dns-filter.nft'])
        settings = json.loads(files['etc/claude-code/managed-settings.json'])
        self.assertEqual(settings['permissions']['allow'], ['Bash(tailscale ssh smith@k001-ops:*)',
                                                            'Bash(tailscale ssh smith@k002-ops:*)'])
        self.assertEqual(settings['env']['DISABLE_AUTOUPDATER'], '1')
        personalize.validate_runner_files({'role': 'air', 'runner_files': files})

    def test_a_declared_client_uses_the_gateway_and_the_proxy(self):
        files = self.render(['k002-air'])
        self.assertEqual(files['etc/network/if-up.d/10-klokast-dns'], HOOK.replace('NAMESERVERS', '192.168.200.41'))
        self.assertIn('ip daddr 192.168.200.41 meta l4proto { tcp, udp } th dport 53 accept',
                      files['etc/klokast/dns-filter.nft'])
        self.assertEqual(json.loads(files['etc/klokast/vpn-egress.json'])['https_proxy'], PROXY)
        self.assertIn('export https_proxy=' + PROXY + '\n', files['etc/profile.d/vpn-egress.sh'])
        self.assertIn('# BEGIN KLOKAST VPN EGRESS\n[http "https://github.com"]\n    proxy = ' + PROXY,
                      files['etc/gitconfig'])
        self.assertTrue(files['etc/gitconfig'].endswith('# END KLOKAST VPN EGRESS\n'))
        personalize.validate_runner_files({'role': 'air', 'runner_files': files})


class RunnerFileValidation(unittest.TestCase):
    FILES = {'etc/gitconfig': 'a\n', 'etc/claude-code/managed-settings.json': '{}\n',
             'etc/klokast/dns-filter.nft': 'b\n', 'etc/network/if-up.d/10-klokast-dns': 'c\n'}

    def test_only_the_runner_has_complete_rendered_files(self):
        personalize.validate_runner_files({'role': 'air', 'runner_files': dict(self.FILES)})
        personalize.validate_runner_files({'role': 'ops'})
        for value in ({'role': 'air'}, {'role': 'ops', 'runner_files': dict(self.FILES)},
                      {'role': 'air', 'runner_files': {k: v for k, v in self.FILES.items() if k != 'etc/gitconfig'}},
                      {'role': 'air', 'runner_files': dict(self.FILES, **{'etc/klokast/vpn-egress.json': '{}'})},
                      {'role': 'air', 'runner_files': dict(self.FILES, **{'etc/shadow': 'x'})},
                      {'role': 'air', 'runner_files': dict(self.FILES, **{'etc/gitconfig': ''})}):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'runner'):
                personalize.validate_runner_files(value)


if __name__ == '__main__':
    unittest.main()


class RunnerFreeze(unittest.TestCase):
    def test_a_runner_replacement_freezes_its_rendered_files(self):
        from unittest.mock import patch
        sys.path.insert(0, str(ROOT / 'ansible/lib'))
        import vm_control_updates
        public = {'role': 'air', 'bootstrap_dns': ['1.1.1.1', '8.8.8.8']}
        files = dict(RunnerFileValidation.FILES)
        with patch.object(vm_control_updates.platform_source, 'snapshot',
                          return_value={'rendered': {'projection': {}}}), \
                patch.object(vm_control_updates, 'guest_config', return_value=public), \
                patch.object(vm_control_updates, 'runner_files', return_value=files) as render:
            values = vm_control_updates.configuration('boxa', 'air', {'accounts': {}, 'persistent_storage': True},
                                                      {}, migrate=False)
        render.assert_called_once_with('boxa', public)
        self.assertEqual(values['infrastructure_creation']['runner_files'], files)
