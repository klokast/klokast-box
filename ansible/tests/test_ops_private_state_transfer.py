"""Validate private-state transfer gates with offline Instance fixtures.

The integration tests require Go, Ansible, rsync, and ansible.posix. They run
only the role's transfer and validation tasks against temporary local paths.
No controller, provider credential, or live inventory is accessed.
"""
import copy
import getpass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = ROOT / 'ansible/roles/ops-private-state-transfer/tasks/main.yml'


def transfer_tasks():
    return yaml.safe_load(TASK_FILE.read_text())


class TransferContractTest(unittest.TestCase):
    def test_validation_runs_at_both_ends_before_credentials(self):
        tasks = transfer_tasks()
        sync = next(i for i, task in enumerate(tasks) if 'ansible.posix.synchronize' in task)
        credentials = next(i for i, task in enumerate(tasks)
                           if task.get('ansible.builtin.import_tasks') == 'root-secret-files.yml')
        source = next(task for task in tasks[:sync] if 'ansible.builtin.command' in task)
        destination = next(task for task in tasks[sync:credentials] if 'block' in task)
        command = destination['block'][0]
        self.assertEqual(source['delegate_to'], 'localhost')
        self.assertIs(source['become'], False)
        self.assertIs(source['check_mode'], False)
        self.assertEqual(destination['when'], 'not ansible_check_mode')
        self.assertIs(command['become'], True)
        self.assertEqual(command['become_user'], '{{ ops_infra_user }}')
        for task, private_root in ((source, 'ops_controller_source_private_root'),
                                   (command, 'ops_infra_private_root')):
            self.assertEqual(task['ansible.builtin.command']['argv'], [
                '/usr/local/bin/klokast', 'check', '--instance',
                '{{ ' + private_root + ' }}/instance', '--json'])
            self.assertIs(task['changed_when'], False)
            self.assertIs(task['no_log'], True)

    def test_legacy_registry_and_active_controller_are_not_transfer_requirements(self):
        text = TASK_FILE.read_text()
        for retired in ('platform-resources.yml', 'platform-source', 'platform-instance',
                        'klokast-controller-guard'):
            self.assertNotIn(retired, text)
        credential_task = next(task for task in transfer_tasks()
                               if task.get('ansible.builtin.import_tasks') == 'root-secret-files.yml')
        self.assertIn('ops_private_state_transfer_root_secrets', credential_task['when'])


@unittest.skipUnless(all(shutil.which(tool) for tool in ('go', 'ansible-playbook', 'rsync')),
                     'Go, Ansible, and rsync are required for local transfer integration tests')
class TransferIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='klokast-transfer-build-')
        cls.addClassCleanup(cls.build.cleanup)
        cls.binary = Path(cls.build.name) / 'klokast'
        subprocess.run(['go', 'build', '-mod=vendor', '-o', str(cls.binary), './cmd/klokast'],
                       cwd=ROOT, env=dict(os.environ, GOTOOLCHAIN='local', GOPROXY='off'),
                       check=True, capture_output=True, text=True, timeout=120)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='klokast-transfer-test-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.source = self.work / 'source'
        self.destination = self.work / 'destination'
        self.instance = self.source / 'instance'
        self.instance.mkdir(parents=True)
        self.destination.mkdir()
        self.existing = self.destination / 'existing-state'
        self.existing.write_text('preserve if source validation fails')
        self.document = self.instance / 'klokast-instance.json'
        self.document.write_text((ROOT / 'tests/fixtures/contract/init-single.json').read_text())
        self.git('init', '--quiet')
        self.git('add', 'klokast-instance.json')

    def git(self, *args):
        subprocess.run(['git', '-C', str(self.instance), *args], check=True,
                       capture_output=True, text=True)

    def run_transfer(self, *, check=False, corrupt_destination=False, standby=False):
        tasks = transfer_tasks()
        boundary = next(i for i, task in enumerate(tasks)
                        if task.get('ansible.builtin.import_tasks') == 'root-secret-files.yml')
        tasks = copy.deepcopy(tasks[:boundary])
        # Use the real validation and rsync tasks with local test paths. Replace
        # only the installed executable and privilege escalation for this runner.
        def local_commands(items):
            for task in items:
                if 'block' in task:
                    local_commands(task['block'])
                if 'ansible.builtin.command' in task:
                    task['ansible.builtin.command']['argv'][0] = str(self.binary)
                    task['become'] = False
        local_commands(tasks)
        if corrupt_destination:
            sync = next(i for i, task in enumerate(tasks) if 'ansible.posix.synchronize' in task)
            tasks.insert(sync + 1, {'name': 'Simulate a damaged destination checkout',
                                   'ansible.builtin.copy': {
                                       'dest': str(self.destination / 'instance/klokast-instance.json'),
                                       'content': '{', 'mode': '0600'}})
        marker = self.work / 'validated'
        tasks.append({'name': 'Record arrival at the credential transfer boundary',
                      'ansible.builtin.copy': {'dest': str(marker), 'content': 'ok', 'mode': '0600'}})
        playbook = self.work / 'transfer.yml'
        playbook.write_text(yaml.safe_dump([{
            'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
            'vars': {'ansible_python_interpreter': sys.executable,
                     'ops_controller_source_private_root': str(self.source),
                     'ops_infra_private_root': str(self.destination),
                     'ops_infra_user': getpass.getuser(),
                     'ops_private_state_transfer_root_secrets': not standby},
            'tasks': tasks,
        }], sort_keys=False))
        config = self.work / 'ansible.cfg'
        config.write_text('[defaults]\nretry_files_enabled = False\n')
        env = dict(os.environ, ANSIBLE_CONFIG=str(config),
                   ANSIBLE_LOCAL_TEMP=str(self.work / 'ansible-local'),
                   ANSIBLE_REMOTE_TEMP=str(self.work / 'ansible-remote'))
        result = subprocess.run(['ansible-playbook', '-vv', '-i', 'localhost,', str(playbook),
                                 *(['--check'] if check else [])],
                                env=env, capture_output=True, text=True, timeout=120)
        self.output = result.stdout + result.stderr
        return result.returncode, marker.exists()

    def test_instance_only_transfer_succeeds(self):
        self.assertEqual(self.run_transfer(), (0, True), self.output)
        self.assertTrue((self.destination / 'instance/.git').is_dir())
        self.assertEqual((self.destination / 'instance/klokast-instance.json').read_bytes(),
                         self.document.read_bytes())
        self.assertFalse((self.destination / 'platform-resources.yml').exists())

    def test_legacy_yaml_content_does_not_control_success(self):
        (self.source / 'platform-resources.yml').write_text('not valid YAML: [')
        self.assertEqual(self.run_transfer(), (0, True), self.output)

    def test_invalid_sources_stop_before_sync(self):
        for invalid in ('missing', 'invalid', 'untracked'):
            with self.subTest(invalid=invalid):
                original = self.document.read_text()
                if invalid == 'missing':
                    self.document.unlink()
                elif invalid == 'invalid':
                    self.document.write_text('{"private-test-marker":')
                else:
                    self.git('update-index', '--force-remove', 'klokast-instance.json')
                code, reached = self.run_transfer()
                self.assertNotEqual(code, 0, self.output)
                self.assertFalse(reached)
                self.assertTrue(self.existing.exists())
                self.assertFalse((self.destination / 'instance').exists())
                self.assertIn('Source Instance validation failed', self.output)
                self.assertNotIn('private-test-marker', self.output)
                self.document.write_text(original)
                self.git('add', 'klokast-instance.json')

    def test_invalid_destination_stops_before_credentials(self):
        code, reached = self.run_transfer(corrupt_destination=True)
        self.assertNotEqual(code, 0, self.output)
        self.assertFalse(reached)
        self.assertIn('Destination Instance validation failed', self.output)

    def test_standby_validates_without_active_authority(self):
        self.assertEqual(self.run_transfer(standby=True), (0, True), self.output)

    def test_check_mode_validates_source_without_copying(self):
        self.assertEqual(self.run_transfer(check=True), (0, False), self.output)
        self.assertTrue(self.existing.exists())
        self.assertFalse((self.destination / 'instance').exists())
        self.document.write_text('{')
        code, reached = self.run_transfer(check=True)
        self.assertNotEqual(code, 0, self.output)
        self.assertFalse(reached)
        self.assertIn('Source Instance validation failed', self.output)


if __name__ == '__main__':
    unittest.main()
