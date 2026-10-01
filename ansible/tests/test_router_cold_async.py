"""Run the actual cold supervisor result tasks against harmless local jobs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / 'ansible/playbooks/tasks/router-cold-supervisor-result.yml'


@unittest.skipUnless(shutil.which('ansible-playbook'), 'requires controller Ansible')
class ColdAsyncTests(unittest.TestCase):
    def exercise(self, rc=0, *, uncertain=False):
        with tempfile.TemporaryDirectory(prefix='router-cold-async-') as temporary:
            work = Path(temporary)
            cache = work / 'async'
            cache.mkdir(mode=0o700)
            unrelated = cache / 'unrelated'
            unrelated.write_text('retain\n')
            tasks = [{
                'ansible.builtin.command': {'argv': ['/bin/sh', '-c', 'exit ' + str(rc)]},
                'async': 30, 'poll': 0, 'register': 'launched',
            }, {
                'ansible.builtin.async_status': {'jid': '{{ launched.ansible_job_id }}'},
                'register': 'finished', 'until': 'finished.finished | default(false) | bool',
                'retries': 10, 'delay': 1, 'failed_when': False,
            }, {
                'ansible.builtin.set_fact': {'router_cold_worker': {
                    'kind': 'test-worker', 'ansible_job_id': '{{ launched.ansible_job_id }}'}},
            }]
            if uncertain:
                # The native async wrapper's timeout record has no command rc.
                tasks.append({'ansible.builtin.copy': {
                    'dest': '{{ ansible_async_dir }}/{{ launched.ansible_job_id }}',
                    'content': json.dumps({'failed': True, 'msg': 'Timeout exceeded'}), 'mode': '0600'}})
            tasks += [
                {'ansible.builtin.import_tasks': str(TASKS)},
                # Retry must read durable completion after exact cache cleanup.
                {'ansible.builtin.import_tasks': str(TASKS)},
                {'ansible.builtin.assert': {'that': ['router_cold_observed_status.rc == 0']}},
            ]
            playbook = work / 'test.json'
            playbook.write_text(json.dumps([{'hosts': 'localhost', 'connection': 'local',
                'gather_facts': False, 'vars': {'ansible_python_interpreter': shutil.which('python3'),
                    'ansible_async_dir': str(cache), 'router_cold_directory': str(work)},
                'tasks': tasks}]))
            config = work / 'ansible.cfg'
            config.write_text('[defaults]\nretry_files_enabled = false\n')
            env = dict(os.environ, ANSIBLE_CONFIG=str(config), ANSIBLE_LOCAL_TEMP=str(work / 'local'),
                       ANSIBLE_REMOTE_TEMP=str(work / 'remote'), ANSIBLE_NOCOLOR='1')
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(playbook), '-vv'],
                cwd=work, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertEqual(unrelated.read_text(), 'retain\n')
            remaining = [p for p in cache.iterdir() if p != unrelated]
            saved = work / 'supervisor-completion.json'
            if uncertain:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('completion is uncertain', result.stdout)
                self.assertEqual(len(remaining), 1, result.stdout)
                self.assertFalse(saved.exists(), result.stdout)
            else:
                self.assertEqual(result.returncode == 0, rc == 0, result.stdout)
                if rc:
                    self.assertIn('"assertion": "router_cold_observed_status.rc == 0"', result.stdout)
                self.assertEqual(remaining, [], result.stdout)
                self.assertEqual(json.loads(saved.read_text())['result']['rc'], rc)

    def test_completed_success_is_retained_and_collection_retry_works(self):
        self.exercise()

    def test_completed_failure_is_retained_and_only_its_cache_is_cleaned(self):
        self.exercise(7)

    def test_uncertain_timeout_retains_cache_without_claiming_completion(self):
        self.exercise(uncertain=True)


if __name__ == '__main__':
    unittest.main()
