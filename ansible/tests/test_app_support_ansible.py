"""Offline invocation, delivery, and independent application behavior tests."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))
from app_support.ansible import AnsibleInvocationError, run_playbook


RENDERER = '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
path = Path(args[args.index('--output') + 1])
record = {'kind': 'render', 'argv': args, 'file_mode': path.stat().st_mode & 0o777,
          'directory_mode': path.parent.stat().st_mode & 0o777}
with open(os.environ['RECORDS'], 'a') as log:
    log.write(json.dumps(record) + '\\n')
path.write_text('all: {}\\n')
raise SystemExit(int(os.environ.get('RENDER_STATUS', '0')))
'''

ANSIBLE = '''import json, os, shlex, signal, sys, time
from pathlib import Path
args = sys.argv[1:]
ping = Path(sys.argv[0]).name == 'ansible'
record = {'kind': 'ping' if ping else 'playbook', 'argv': args, 'cwd': os.getcwd(),
          'environment': {k:v for k,v in os.environ.items() if k.startswith('ANSIBLE_')},
          'pid': os.getpid()}
record['limit'] = args[-4] if ping else args[args.index('--limit') + 1]
if not ping:
    record['playbook'] = Path(args[args.index('--limit') - 1]).name
if '-e' in args:
    path = Path(args[args.index('-e') + 1][1:])
    record['variables'] = json.loads(path.read_text())
    record['vars_mode'] = path.stat().st_mode & 0o777
    record['vars_path'] = str(path)
for option in shlex.split(os.environ.get('ANSIBLE_SSH_COMMON_ARGS', '')):
    if option.startswith('UserKnownHostsFile='):
        path = Path(option.split('=', 1)[1].strip('"'))
        record['known_hosts'] = str(path)
        record['known_hosts_mode'] = path.stat().st_mode & 0o777
        path.write_text('test host key\\n')
with open(os.environ['RECORDS'], 'a') as log:
    log.write(json.dumps(record) + '\\n')
print('Ansible stdout', flush=True)
print('Ansible stderr', file=sys.stderr, flush=True)
if os.environ.get('BLOCK_CHILD') == '1':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    ready = Path(os.environ['READY'])
    ready.with_suffix('.pending').write_text(str(os.getpid()))
    ready.with_suffix('.pending').replace(ready)
    time.sleep(30)
if ping and os.environ.get('FAIL_PING_BOX', '') and os.environ['FAIL_PING_BOX'] in record['limit']:
    raise SystemExit(4)
if os.environ.get('FAIL_PLAYBOOK') and os.environ['FAIL_PLAYBOOK'] == record.get('playbook'):
    raise SystemExit(7)
raise SystemExit(int(os.environ.get('ANSIBLE_STATUS', '0')))
'''

RESOURCES = '''import json, os, sys
args = sys.argv[1:]
with open(os.environ['RECORDS'], 'a') as log:
    log.write(json.dumps({'kind': 'resources', 'argv': args}) + '\\n')
if args[-1] == 'show':
    app = args[args.index('--app') + 1]
    print(json.dumps({'apps': {app: {'enabled': True,
        'boxes': ['boxa', 'boxb'] if app == 'nextcloud' else ['boxa'],
        'resources': ['household-https', 'music-upstream']}}}))
'''


class InvocationFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='app-invocation-test-')
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        self.repo = self.work / 'checkout with spaces'
        self.bin = self.work / 'installed/bin'
        self.temp = self.work / 'temporary files'
        self.temp.mkdir()
        (self.temp / 'unrelated').write_text('keep')
        self.records = self.work / 'records.jsonl'
        self.records.touch()
        self.ready = self.work / 'ready'
        self.renderer = self.repo / 'ansible/bin/render-node-inventory'
        self.script(self.renderer, RENDERER)
        for name in ('ansible', 'ansible-playbook'):
            self.script(self.bin / name, ANSIBLE)
        (self.bin / 'python3').symlink_to(sys.executable)
        package = self.bin.parent / 'lib/klokast/app_support'
        shutil.copytree(ROOT / 'ansible/lib/app_support', package, ignore=shutil.ignore_patterns('__pycache__'))
        self.cli = self.bin / 'platform-ansible'
        shutil.copy2(ROOT / 'ansible/bin/platform-ansible', self.cli)
        self.env = {**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'TMPDIR': str(self.temp), 'RECORDS': str(self.records), 'READY': str(self.ready),
                    'ANSIBLE_SSH_EXECUTABLE': '/explicit/transport',
                    'ANSIBLE_SSH_COMMON_ARGS': '-o BatchMode=yes',
                    'ANSIBLE_SSH_EXTRA_ARGS': '-o ConnectTimeout=17',
                    'ANSIBLE_SSH_TRANSFER_METHOD': 'piped', 'ANSIBLE_SSH_USETTY': 'false',
                    'KLOKAST_PLATFORM_ANSIBLE': str(self.cli),
                    'PYTHONDONTWRITEBYTECODE': '1'}

    def script(self, path, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'#!{sys.executable}\n' + source)
        path.chmod(0o755)

    def command(self, mode='playbook'):
        args = [str(self.cli), mode, '--repo-root', str(self.repo), '--config', 'ansible/ansible.cfg',
                '--role-path', 'app roles', '--role-path', 'ansible/roles',
                '--inventory', 'base inventory', '--inventory', 'second inventory',
                '--box', 'boxb', '--box', 'boxa', '--box', 'boxb',
                '--magicdns-suffix', 'test.ts.net', '--limit', 'boxb-bak,boxa-dmz']
        if mode == 'playbook':
            args += ['--playbook', 'selected playbook.yml']
        return args

    def run_command(self, argv=None, *, variables=None):
        command = argv or self.command()
        if variables is not None:
            command = [*command, '--extra-vars-stdin']
        return subprocess.run(command, env=self.env, input=variables, text=True,
                              capture_output=True, timeout=15)

    def events(self, kind=None):
        records = [json.loads(line) for line in self.records.read_text().splitlines()]
        return records if kind is None else [item for item in records if item['kind'] == kind]

    def assert_clean(self):
        self.assertEqual([p.name for p in self.temp.iterdir()], ['unrelated'])
        self.assertEqual((self.temp / 'unrelated').read_text(), 'keep')

    def install_app(self, app):
        source = ROOT / 'apps' / app
        target = self.repo / 'apps' / app
        shutil.copytree(source, target)
        shutil.copytree(ROOT / 'ansible/lib/app_support', self.repo / 'ansible/lib/app_support',
                        ignore=shutil.ignore_patterns('__pycache__'))
        self.script(self.repo / 'ansible/bin/platform-resources', RESOURCES)
        self.assertEqual([p.name for p in (self.repo / 'apps').iterdir()], [app])
        return target / 'bin' / (app + 'ctl')

    def interrupt(self, command, signum):
        self.env['BLOCK_CHILD'] = '1'
        child = subprocess.Popen(command, env=self.env, text=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not self.ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(self.ready.exists(), 'stub Ansible did not start')
            local_pid = int(self.ready.read_text())
            child.send_signal(signum)
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 128 + signum, (stdout, stderr))
            self.assertIn('interrupted', stderr)
            with self.assertRaises(ProcessLookupError):
                os.kill(local_pid, 0)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()
        self.assert_clean()


class SharedInvocationTest(InvocationFixture):
    def test_playbook_inputs_permissions_order_and_streams(self):
        variables = {'password': 'private-sentinel', 'enabled': False, 'nested': {'count': 3}}
        result = self.run_command(variables=json.dumps(variables))
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = self.events('render')
        self.assertEqual([item['argv'][1] for item in rendered], ['boxb', 'boxa'])
        for item in rendered:
            self.assertEqual(item['file_mode'], 0o600)
            self.assertEqual(item['directory_mode'], 0o700)
            self.assertEqual(item['argv'][2:4], ['--magicdns-suffix', 'test.ts.net'])
        record, = self.events('playbook')
        generated = [item['argv'][-1] for item in rendered]
        self.assertEqual(record['argv'], ['-vv', '-i', str(self.repo / 'base inventory'),
            '-i', str(self.repo / 'second inventory'), '-i', generated[0], '-i', generated[1],
            str(self.repo / 'selected playbook.yml'), '--limit', 'boxb-bak,boxa-dmz',
            '-e', '@' + record['vars_path']])
        self.assertEqual(record['variables'], variables)
        self.assertEqual(record['vars_mode'], 0o600)
        self.assertEqual(record['cwd'], str(self.repo))
        self.assertEqual(record['environment']['ANSIBLE_CONFIG'], str(self.repo / 'ansible/ansible.cfg'))
        self.assertEqual(record['environment']['ANSIBLE_ROLES_PATH'],
                         f'{self.repo}/app roles:{self.repo}/ansible/roles')
        for key, value in self.env.items():
            if key.startswith('ANSIBLE_SSH_'):
                self.assertEqual(record['environment'][key], value)
        self.assertIn('Ansible stdout', result.stdout)
        self.assertIn('Ansible stderr', result.stderr)
        self.assertNotIn('private-sentinel', result.stdout + result.stderr + str(record['argv']))
        self.assert_clean()

    def test_ping_has_no_playbook_or_extra_variables(self):
        result = self.run_command(self.command('ping'))
        self.assertEqual(result.returncode, 0, result.stderr)
        record, = self.events('ping')
        self.assertEqual(record['argv'][-4:], ['boxb-bak,boxa-dmz', '-m', 'ping', '-o'])
        self.assertNotIn('-e', record['argv'])
        self.assert_clean()

    def test_render_failure_stops_before_ansible_and_cleans(self):
        self.env['RENDER_STATUS'] = '9'
        result = self.run_command(variables='{"password":"private-sentinel"}')
        self.assertEqual(result.returncode, 9)
        self.assertIn('render inventory', result.stderr)
        self.assertIn('boxb', result.stderr)
        self.assertEqual(len(self.events()), 1)
        self.assertNotIn('private-sentinel', result.stderr)
        self.assert_clean()

    def test_ansible_failure_and_missing_executable(self):
        self.env['ANSIBLE_STATUS'] = '6'
        result = self.run_command(variables='{"password":"private-sentinel"}')
        self.assertEqual(result.returncode, 6)
        self.assertIn('selected playbook.yml', result.stderr)
        self.assertIn('limit', result.stderr)
        self.assertNotIn('private-sentinel', result.stderr)
        self.assert_clean()
        (self.bin / 'ansible-playbook').unlink()
        self.env['PATH'] = str(self.bin)
        result = self.run_command()
        self.assertEqual(result.returncode, 2)
        self.assertIn('cannot start ansible-playbook', result.stderr)
        self.assert_clean()

    def test_invalid_json_and_missing_installed_module_fail_without_processes(self):
        for value in ('{"password":"private-sentinel"', '["private-sentinel"]'):
            with self.subTest(value=value):
                result = self.run_command(variables=value)
                self.assertEqual(result.returncode, 2)
                self.assertIn('JSON object', result.stderr)
                self.assertNotIn('private-sentinel', result.stderr)
                self.assertEqual(self.events(), [])
                self.assert_clean()
        (self.bin.parent / 'lib/klokast/app_support/ansible.py').unlink()
        result = self.run_command()
        self.assertEqual(result.returncode, 2)
        self.assertIn('converge the controller toolchain', result.stderr)

    def test_python_api_preserves_environment_and_returns_safe_errors(self):
        original = dict(self.env)
        kwargs = dict(playbook='selected.yml', repo_root=self.repo, config='config',
                      role_paths=['roles'], inventories=['base'], boxes=['boxa'],
                      magicdns_suffix='test.ts.net', limit='boxa-dmz', env=self.env)
        with self.assertRaises(AnsibleInvocationError) as caught:
            run_playbook(**kwargs, extra_vars={'password': object()})
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn('JSON values', str(caught.exception))
        self.env['RENDER_STATUS'] = '8'
        # tempfile uses the process environment; isolate its cached directory too.
        with patch.dict(os.environ, self.env), patch('tempfile.tempdir', str(self.temp)):
            with self.assertRaises(AnsibleInvocationError) as caught:
                run_playbook(**kwargs)
        self.assertEqual(caught.exception.exit_code, 8)
        self.env.pop('RENDER_STATUS')
        self.assertEqual(self.env, original)
        self.assert_clean()

    def test_real_renderer_is_called_without_reimplementing_inventory(self):
        shutil.copy2(ROOT / 'ansible/bin/render-node-inventory', self.renderer)
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.events('playbook')), 1)
        self.assert_clean()

    def test_sigterm_stops_uncooperative_child_and_cleans(self):
        self.interrupt(self.command(), signal.SIGTERM)

    def test_sigint_stops_uncooperative_child_and_cleans(self):
        self.interrupt(self.command(), signal.SIGINT)


class ApplicationInvocationTest(InvocationFixture):
    def nextcloud(self, operation='install', *extra):
        script = self.install_app('nextcloud')
        for name in ('ADMIN_PASSWORD', 'POSTGRES_PASSWORD', 'RESTIC_PASSWORD', 'RESTIC_REPOSITORY'):
            self.env['NEXTCLOUD_' + name] = 'private-sentinel'
        return [str(script), operation, '--active-master', 'boxa', '--passive-backup', 'boxb',
                '--domain', 'cloud.example.com', *extra]

    def ingress(self, operation='deploy'):
        script = self.install_app('local-ingress')
        cert = self.work / 'cert file'
        key = self.work / 'key file'
        cert.touch()
        key.touch()
        args = [str(script), operation, '--box', 'boxa', '--local-domain', 'home.example.com']
        if operation != 'verify':
            args += ['--tls-cert', str(cert), '--tls-key', str(key)]
        if operation == 'deploy':
            args += ['--approved-commit', 'reviewed-commit']
        return args

    def assert_transport(self, records):
        for record in records:
            settings = record['environment']
            self.assertEqual(settings['ANSIBLE_SSH_EXECUTABLE'], '/explicit/transport')
            self.assertEqual(settings['ANSIBLE_SSH_EXTRA_ARGS'], '-o ConnectTimeout=17')
            self.assertIn('StrictHostKeyChecking=accept-new', settings['ANSIBLE_SSH_COMMON_ARGS'])
            self.assertEqual(record['known_hosts_mode'], 0o600)
            self.assertFalse(Path(record['known_hosts']).exists())

    def test_nextcloud_install_order_variables_and_shared_known_hosts(self):
        result = self.run_command(self.nextcloud())
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.events('playbook')
        self.assertEqual([r['playbook'] for r in records],
                         ['00-preflight.yml', '15-private-ingress-identity.yml', '20-install.yml'])
        self.assertEqual(len({r['known_hosts'] for r in records}), 1)
        for record in records:
            self.assertEqual(record['limit'], 'boxa-bak,boxa-dmz,boxb-bak,boxb-dmz')
            self.assertEqual(record['variables']['nextcloud_active_master'], 'boxa')
            self.assertEqual(record['variables']['nextcloud_passive_backup'], 'boxb')
        self.assertIs(records[-1]['variables']['nextcloud_cloudflare_tunnel_enabled'], False)
        self.assertEqual([r['argv'][-1] for r in self.events('resources')], ['show', 'verify'])
        self.assert_transport(records)
        self.assertNotIn('private-sentinel', result.stdout + result.stderr)
        self.assert_clean()

    def test_nextcloud_failure_stops_later_playbooks_and_cleans(self):
        self.env['FAIL_PLAYBOOK'] = '15-private-ingress-identity.yml'
        result = self.run_command(self.nextcloud())
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual([r['playbook'] for r in self.events('playbook')],
                         ['00-preflight.yml', '15-private-ingress-identity.yml'])
        self.assert_clean()

    def test_nextcloud_optional_missing_passive_keeps_app_decision(self):
        self.env['FAIL_PING_BOX'] = 'boxb'
        result = self.run_command(self.nextcloud('preflight', '--allow-missing-passive-for-test'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.events('ping')), 2)
        record, = self.events('playbook')
        self.assertEqual(record['playbook'], '00-preflight.yml')
        self.assertEqual(record['limit'], 'boxa-bak,boxa-dmz')
        self.assertIn('continuing', result.stdout)
        self.assert_clean()

    def test_nextcloud_required_passive_failure_does_not_run_playbook(self):
        self.env['FAIL_PING_BOX'] = 'boxb'
        result = self.run_command(self.nextcloud('preflight'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('passive backup boxb is not reachable', result.stderr)
        self.assertEqual(self.events('playbook'), [])
        self.assert_clean()

    def test_nextcloud_missing_helper_reports_convergence(self):
        self.env['KLOKAST_PLATFORM_ANSIBLE'] = str(self.work / 'missing')
        result = self.run_command(self.nextcloud('preflight'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('converge the controller toolchain', result.stderr)
        self.assertEqual(self.events(), [])
        self.assert_clean()

    def test_nextcloud_sigterm_is_forwarded_and_transport_files_are_removed(self):
        self.interrupt(self.nextcloud(), signal.SIGTERM)

    def test_ingress_deploy_preserves_resource_install_verify_order(self):
        result = self.run_command(self.ingress())
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.events('playbook')
        self.assertEqual([r['playbook'] for r in records], ['20-install.yml', '40-verify.yml'])
        self.assertEqual([r['kind'] for r in self.events() if r['kind'] != 'render'],
                         ['resources', 'resources', 'resources', 'playbook', 'resources', 'playbook'])
        self.assertEqual([r['argv'][-1] for r in self.events('resources')], ['show', 'apply', 'show', 'show'])
        self.assertEqual(records[0]['variables']['local_ingress_tls_key_source'], str(self.work / 'key file'))
        self.assertEqual(records[1]['variables'], {'local_ingress_domain': 'home.example.com'})
        self.assertTrue(all(r['limit'] == 'boxa-dmz' for r in records))
        self.assert_transport(records)
        self.assert_clean()

    def test_ingress_failed_install_stops_before_verify(self):
        self.env['FAIL_PLAYBOOK'] = '20-install.yml'
        result = self.run_command(self.ingress())
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn('local-ingressctl:', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(len(self.events('playbook')), 1)
        self.assert_clean()

    def test_ingress_sigterm_cleans_helper_and_transport_files(self):
        self.interrupt(self.ingress('verify'), signal.SIGTERM)


class InvocationDeliveryTest(unittest.TestCase):
    def test_installation_and_offline_verification_cover_new_interface(self):
        tasks = yaml.safe_load((ROOT / 'ansible/roles/ops-controller/tasks/development-tools.yml').read_text())
        copy = next(t['ansible.builtin.copy'] for t in tasks
                    if t.get('ansible.builtin.copy', {}).get('dest') == '/usr/local/bin/platform-ansible')
        self.assertEqual((copy['owner'], copy['group'], copy['mode']), ('root', 'root', '0755'))
        self.assertEqual(copy['src'], '{{ playbook_dir }}/../bin/platform-ansible')
        self.assertTrue(any(t.get('with_fileglob') == '{{ playbook_dir }}/../lib/app_support/*.py' for t in tasks))
        verify = yaml.safe_load((ROOT / 'ansible/roles/ops-controller-verification/tasks/main.yml').read_text())
        self.assertIn('platform-ansible', verify[0]['ansible.builtin.set_fact']['ops_controller_check_required_commands'])
        self.assertTrue(any(t.get('ansible.builtin.command') ==
                            {'argv': ['/usr/local/bin/platform-ansible', '--help']} for t in verify))


if __name__ == '__main__':
    unittest.main()
