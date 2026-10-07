"""Each application can use installed transport with no other application present."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class ApplicationTransportTest(unittest.TestCase):
    def test_each_consumer_runs_its_own_playbook_with_shared_transport(self):
        for app in ('household-vpn', 'torrent'):
            with self.subTest(app=app), tempfile.TemporaryDirectory() as directory:
                view = Path(directory)
                app_bin = view / 'apps' / app / 'bin'
                app_bin.mkdir(parents=True)
                entrypoint = app_bin / (app + 'ctl')
                shutil.copy2(ROOT / 'apps' / app / 'bin' / (app + 'ctl'), entrypoint)
                shutil.copytree(ROOT / 'ansible/lib/app_support', view / 'ansible/lib/app_support')
                installed = view / 'installed-bin'
                installed.mkdir()
                adapter = installed / 'platform-tailscale-ssh'
                shutil.copy2(ROOT / 'ansible/bin/platform-tailscale-ssh', adapter)
                tailscale = installed / 'tailscale'
                tailscale.write_text(f'#!{sys.executable}\n' +
                                     'import json, sys\nprint(json.dumps(sys.argv[1:]))\n')
                tailscale.chmod(0o755)
                ansible = installed / 'ansible-playbook'
                ansible.write_text(f'#!{sys.executable}\n' + '''import json, os, subprocess, sys
args = sys.argv[1:]
target = args[args.index('--limit') + 1]
transport = subprocess.check_output([
    os.environ['ANSIBLE_SSH_EXECUTABLE'], '-o', 'User="neo"',
    '-o', 'StrictHostKeyChecking=accept-new', target, 'true'], text=True)
print(json.dumps({'argv': args, 'transport': json.loads(transport),
    'environment': {key: value for key, value in os.environ.items() if key.startswith('ANSIBLE_')}}))
''')
                ansible.chmod(0o755)
                harness = '''import importlib.util, sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
loader = SourceFileLoader('app_controller', sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
app = importlib.util.module_from_spec(spec)
loader.exec_module(app)
from app_support import tailscale_ssh
tailscale_ssh.DEFAULT_EXECUTABLE = Path(sys.argv[2])
app.run_playbook('/selected/playbook.yml', sys.argv[3], '/private/vars.yml',
                 ['/selected/base-inventory', '/selected/app-inventory'])
'''
                env = {**os.environ, 'PATH': str(installed),
                       'ANSIBLE_SSH_EXECUTABLE': '/unused/ssh',
                       'ANSIBLE_SSH_COMMON_ARGS': '-o UserKnownHostsFile=/unused',
                       'ANSIBLE_SSH_EXTRA_ARGS': '-o BatchMode=yes'}
                result = subprocess.run([sys.executable, '-B', '-I', '-c', harness,
                                         str(entrypoint), str(adapter), 'boxa-' + app],
                                        capture_output=True, text=True, env=env, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                record = json.loads(result.stdout)
                self.assertEqual(record['argv'], [
                    '-vv', '-i', '/selected/base-inventory', '-i', '/selected/app-inventory',
                    '/selected/playbook.yml', '--limit', 'boxa-' + app, '-e', '@/private/vars.yml'])
                self.assertEqual(record['transport'], ['ssh', 'neo@boxa-' + app, 'true'])
                settings = record['environment']
                self.assertEqual(settings['ANSIBLE_CONFIG'], str(view / 'ansible/ansible.cfg'))
                self.assertEqual(settings['ANSIBLE_ROLES_PATH'],
                                 f"{app_bin.parent / 'ansible/roles'}:{view / 'ansible/roles'}")
                self.assertEqual(settings['ANSIBLE_SSH_EXECUTABLE'], str(adapter))
                self.assertEqual(settings['ANSIBLE_SSH_ARGS'], '-o ControlMaster=no -o ControlPersist=no')
                self.assertEqual(settings['ANSIBLE_SSH_TRANSFER_METHOD'], 'piped')
                self.assertEqual(settings['ANSIBLE_SSH_USETTY'], 'false')
                self.assertEqual(settings['ANSIBLE_SSH_COMMON_ARGS'], '')
                self.assertEqual(settings['ANSIBLE_SSH_EXTRA_ARGS'], '-o BatchMode=yes')
                self.assertEqual([p.name for p in (view / 'apps').iterdir()], [app])
                self.assertEqual(list(view.rglob('tailscale-ssh')), [])
                self.assertEqual(list(view.rglob('known_hosts')), [])

                # A removed controller helper fails before the playbook process.
                result = subprocess.run([sys.executable, '-B', '-I', '-c', harness,
                                         str(entrypoint), str(installed / 'missing'), 'boxa-' + app],
                                        capture_output=True, text=True, env=env, timeout=15)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertIn(app + 'ctl:', result.stderr)
                self.assertIn('converge the controller toolchain', result.stderr)


class ControllerDeliveryTest(unittest.TestCase):
    def test_tasks_deliver_an_independent_importable_package_and_adapter(self):
        tasks = yaml.safe_load((ROOT / 'ansible/roles/ops-controller/tasks/development-tools.yml').read_text())
        copy_adapter = next(task['ansible.builtin.copy'] for task in tasks
                            if task.get('ansible.builtin.copy', {}).get('dest') ==
                            '/usr/local/bin/platform-tailscale-ssh')
        self.assertEqual(copy_adapter['mode'], '0755')
        self.assertEqual((copy_adapter['owner'], copy_adapter['group']), ('root', 'root'))
        module_task = next(task for task in tasks
                           if task.get('with_fileglob') == '{{ playbook_dir }}/../lib/app_support/*.py')
        copy_module = module_task['ansible.builtin.copy']
        self.assertEqual(copy_module['mode'], '0644')
        self.assertEqual((copy_module['owner'], copy_module['group']), ('root', 'root'))
        dirs = next(task['loop'] for task in tasks if task.get('name') == 'Create controller tool and state directories')
        self.assertIn({'path': '/usr/local/lib/klokast/app_support', 'owner': 'root', 'mode': '0755'}, dirs)

        # Reproduce the declared source selection in a temporary install root.
        with tempfile.TemporaryDirectory() as directory:
            install_root = Path(directory)
            package = install_root / 'app_support'
            package.mkdir()
            pattern = module_task['with_fileglob'].replace('{{ playbook_dir }}/../', '')
            for source in (ROOT / 'ansible').glob(pattern):
                destination = copy_module['dest'].replace('{{ item | basename }}', source.name)
                shutil.copy2(source, install_root / Path(destination).relative_to('/usr/local/lib/klokast'))
            adapter = install_root / 'platform-tailscale-ssh'
            source = copy_adapter['src'].replace('{{ playbook_dir }}/../', '')
            shutil.copy2(ROOT / 'ansible' / source, adapter)
            result = subprocess.run([sys.executable, '-B', '-I', '-c',
                'import sys; sys.path.insert(0, sys.argv[1]); '
                'from app_support.tailscale_ssh import ansible_environment; '
                'assert ansible_environment({}, executable=sys.argv[2])["ANSIBLE_SSH_EXECUTABLE"] == sys.argv[2]',
                str(install_root), str(adapter)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

        verify = yaml.safe_load((ROOT / 'ansible/roles/ops-controller-verification/tasks/main.yml').read_text())
        self.assertIn('platform-tailscale-ssh', verify[0]['ansible.builtin.set_fact']['ops_controller_check_required_commands'])
        self.assertTrue(any('from app_support.tailscale_ssh import ansible_environment' in
                            str(task.get('ansible.builtin.command', {})) for task in verify))


if __name__ == '__main__':
    unittest.main()
