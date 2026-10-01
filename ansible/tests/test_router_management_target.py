"""Exercise real Ansible peer selection with synthetic Tailnet command replies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('ansible-playbook'), 'requires controller Ansible')
class ManagementTargetTests(unittest.TestCase):
    def exercise(self, *, guest_id='new-device', duplicate_id=False):
        peers = {'old': {'ID': 'old-device', 'HostName': 'boxa-router',
                         'Online': False, 'TailscaleIPs': ['100.64.0.1']},
                 'new': {'ID': 'new-device', 'HostName': 'boxa-router',
                         'Online': True, 'TailscaleIPs': ['100.64.0.2']}}
        if duplicate_id:
            peers['old']['ID'] = 'new-device'
        replies = {'router_management_status': {'BackendState': 'Running', 'Peer': peers},
                   'router_management_guest': {'BackendState': 'Running', 'Self': {
                       'ID': guest_id, 'HostName': 'boxa-router', 'TailscaleIPs': ['100.64.0.2']}}}
        tasks = yaml.safe_load((ROOT / 'ansible/roles/router-management-target/tasks/main.yml').read_text())
        # Keep all production selectors and assertions; replace only the two
        # external Tailnet calls, so these tests need no Platform credentials.
        for task in tasks:
            if task.get('register') in replies:
                script = 'print(' + repr(json.dumps(replies[task['register']])) + ')'
                task['ansible.builtin.command']['argv'] = [shutil.which('python3'), '-c', script]
        tasks.append({'ansible.builtin.assert': {'that': [
            "ansible_host == '100.64.0.2'", "router_management_address == '100.64.0.2'"]}})
        with tempfile.TemporaryDirectory(prefix='router-management-test-') as temporary:
            work = Path(temporary)
            playbook = work / 'test.json'
            playbook.write_text(json.dumps([{'hosts': 'localhost', 'connection': 'local',
                'gather_facts': False, 'vars': {'ansible_python_interpreter': shutil.which('python3'),
                    'router_management_machine_id': 'new-device',
                    'router_management_hostname': 'boxa-router'}, 'tasks': tasks}]))
            config = work / 'ansible.cfg'
            config.write_text('[defaults]\nretry_files_enabled = false\n')
            env = dict(os.environ, ANSIBLE_CONFIG=str(config), ANSIBLE_LOCAL_TEMP=str(work / 'local'),
                       ANSIBLE_REMOTE_TEMP=str(work / 'remote'), ANSIBLE_NOCOLOR='1')
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(playbook)],
                cwd=work, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertEqual(result.returncode == 0,
                             guest_id == 'new-device' and not duplicate_id, result.stdout)

    def test_same_hostname_selects_only_the_recorded_machine(self):
        self.exercise()

    def test_guest_must_confirm_the_selected_identity(self):
        self.exercise(guest_id='old-device')

    def test_ambiguous_machine_id_is_refused(self):
        self.exercise(duplicate_id=True)


if __name__ == '__main__':
    unittest.main()
