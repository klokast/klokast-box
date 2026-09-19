#!/usr/bin/env python3
"""Exercise the installed Ansible async implementation without touching guests."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
RESTART_RESULT = (
    REPO_ROOT / "ansible/roles/dom0-tailscale/tasks/restart-result.yml"
)


@unittest.skipUnless(shutil.which("ansible-playbook"), "requires controller Ansible")
class AsyncCleanupTest(unittest.TestCase):
    def run_job(self, command, *, poll=0, timeout=30, missing=False, interrupted=False):
        with tempfile.TemporaryDirectory(prefix="klokast-async-test-") as tmp:
            root = Path(tmp)
            cache = root / "async"
            cache.mkdir(mode=0o700)
            unrelated = cache / "unrelated-job"
            unrelated.write_text("retain this evidence\n", encoding="utf-8")
            tasks = [] if missing else [{
                "name": "Launch harmless test command",
                "ansible.builtin.command": {"argv": ["/bin/sh", "-c", command]},
                "async": timeout,
                "poll": poll,
                "register": "tailscale_restart_job",
            }]
            if interrupted:
                tasks.append({"ansible.builtin.fail": {
                    "msg": "Test interruption before result collection",
                }})
            elif poll == 0:
                tasks.append({"ansible.builtin.import_tasks": str(RESTART_RESULT)})
            playbook = root / "test.json"
            playbook.write_text(json.dumps([{
                "hosts": "localhost",
                "connection": "local",
                "gather_facts": False,
                "vars": {
                    "ansible_async_dir": str(cache),
                    "ansible_python_interpreter": shutil.which("python3"),
                    **({"tailscale_restart_job": {"ansible_job_id": "missing-job"}}
                       if missing else {}),
                },
                "tasks": tasks,
            }]), encoding="utf-8")
            # Isolate controller caches and configuration from production.
            config = root / "ansible.cfg"
            config.write_text("[defaults]\nretry_files_enabled = false\n", encoding="utf-8")
            env = dict(os.environ, ANSIBLE_CONFIG=str(config),
                       ANSIBLE_LOCAL_TEMP=str(root / "local"),
                       ANSIBLE_REMOTE_TEMP=str(root / "remote"),
                       ANSIBLE_NOCOLOR="1")
            result = subprocess.run(
                ["ansible-playbook", "-i", "localhost,", str(playbook), "-vv"],
                cwd=root, env=env, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=60,
            )
            if interrupted:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("Test interruption before result collection", result.stdout)
                jobs = [p for p in cache.iterdir() if p != unrelated]
                self.assertEqual(len(jobs), 1, result.stdout)
                resumed = json.loads(playbook.read_text())
                resumed[0]["vars"]["tailscale_restart_job"] = {
                    "ansible_job_id": jobs[0].name,
                }
                resumed[0]["tasks"] = [{
                    "ansible.builtin.import_tasks": str(RESTART_RESULT),
                }]
                playbook.write_text(json.dumps(resumed), encoding="utf-8")
                result = subprocess.run(
                    ["ansible-playbook", "-i", "localhost,", str(playbook), "-vv"],
                    cwd=root, env=env, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, timeout=60,
                )
            self.assertEqual(unrelated.read_text(), "retain this evidence\n")
            records = {
                p.name: json.loads(p.read_text())
                for p in cache.iterdir() if p != unrelated
            }
            return result, records

    def test_detached_success_cleans_only_its_record(self):
        result, records = self.run_job("exit 0")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(records, {}, result.stdout)

    def test_detached_failure_cleans_record_and_still_fails(self):
        result, records = self.run_job("exit 7")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('"rc": 7', result.stdout)
        self.assertEqual(records, {}, result.stdout)

    def test_wrapper_timeout_preserves_uncertain_result(self):
        result, records = self.run_job("sleep 10", timeout=1)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(records), 1, result.stdout)
        record = next(iter(records.values()))
        self.assertIn("Timeout", record.get("msg", ""), result.stdout)
        self.assertNotIn("rc", record, result.stdout)

    def test_missing_record_fails_without_cleanup(self):
        result, records = self.run_job("", missing=True)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("could not find job", result.stdout)
        self.assertEqual(records, {}, result.stdout)
        self.assertIn("skipping: [localhost]", result.stdout)

    def test_new_process_collects_result_after_interrupted_play(self):
        result, records = self.run_job("sleep 2", interrupted=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(records, {}, result.stdout)

    def test_native_polling_cleans_success(self):
        result, records = self.run_job("exit 0", poll=1)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(records, {}, result.stdout)

    def test_native_polling_cleans_command_failure(self):
        result, records = self.run_job("exit 7", poll=1)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('"rc": 7', result.stdout)
        self.assertEqual(records, {}, result.stdout)


if __name__ == "__main__":
    unittest.main()
