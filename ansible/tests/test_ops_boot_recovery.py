"""Exercise boot-recovery cleanup with the real local Ansible modules."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

REPO = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO / 'ansible/playbooks/65-ops-recover-boot.yml'


@unittest.skipUnless(shutil.which('ansible-playbook'), 'requires controller Ansible')
class BootRecoveryCleanupTests(unittest.TestCase):
    def check_cleanup(self, *, finished, failed=False, apk_finished=True):
        plays = yaml.safe_load(PLAYBOOK.read_text())
        recovery = next(t for t in plays[1]['tasks'] if 'always' in t)
        with tempfile.TemporaryDirectory(prefix='ops-recovery-test-') as temporary:
            root = Path(temporary)
            cache = root / 'jobs'; cache.mkdir(mode=0o700)
            inputs = root / 'inputs'; inputs.mkdir(mode=0o700)
            (inputs / 'inputs.json').write_text('{}')
            job = cache / 'exact-job'; job.write_text('{}')
            unrelated = cache / 'unrelated-job'; unrelated.write_text('retain')
            cleanup = copy.deepcopy(recovery['always'])
            for task in cleanup:
                if 'vars' in task:
                    task['vars']['ansible_async_dir'] = str(cache)
            variables = {
                'ansible_python_interpreter': shutil.which('python3'),
                'ops_boot_recovery_work': {'path': str(inputs)},
                'ops_boot_recovery_restart': {'ansible_job_id': job.name},
                'ops_boot_recovery_apk': {'finished': int(apk_finished)},
            }
            if finished:
                variables['ops_boot_recovery_status'] = {'finished': 1, 'rc': 7 if failed else 0}
            body = [{'ansible.builtin.fail': {'msg': 'original recovery failure'}}] if failed else [
                {'ansible.builtin.debug': {'msg': 'recovery completed'}}]
            scenario = root / 'scenario.json'
            scenario.write_text(json.dumps([{
                'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                'vars': variables, 'tasks': [{'block': body, 'always': cleanup}],
            }]))
            config = root / 'ansible.cfg'
            config.write_text('[defaults]\nretry_files_enabled = false\n')
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(scenario)],
                env=dict(os.environ, ANSIBLE_CONFIG=str(config),
                         ANSIBLE_LOCAL_TEMP=str(root / 'local'), ANSIBLE_REMOTE_TEMP=str(root / 'remote')),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertEqual(result.returncode != 0, failed, result.stdout)
            if failed:
                self.assertIn('original recovery failure', result.stdout)
            self.assertEqual(job.exists(), not finished, result.stdout)
            self.assertEqual(inputs.exists(), not (finished and apk_finished), result.stdout)
            self.assertEqual(unrelated.read_text(), 'retain')

    def test_completed_success_cleans_only_its_files(self):
        self.check_cleanup(finished=True)

    def test_completed_failure_cleans_its_files_and_preserves_failure(self):
        self.check_cleanup(finished=True, failed=True)

    def test_connection_loss_retains_uncertain_job_and_inputs(self):
        self.check_cleanup(finished=False, failed=True)

    def test_uncertain_package_job_retains_its_input_directory(self):
        self.check_cleanup(finished=True, failed=True, apk_finished=False)


if __name__ == '__main__':
    unittest.main()
