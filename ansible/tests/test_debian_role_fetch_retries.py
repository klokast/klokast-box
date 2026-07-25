#!/usr/bin/env python3
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


class DebianRoleFetchRetriesTest(unittest.TestCase):
    def load_tasks(self, role_name):
        path = REPO_ROOT / "ansible" / "roles" / role_name / "tasks" / "main.yml"
        return yaml.safe_load(path.read_text())

    def task_named(self, tasks, name):
        for task in tasks:
            if task.get("name") == name:
                return task
        self.fail(f"missing task: {name}")

    def assert_get_url_retries(self, task, register_name):
        self.assertIn("ansible.builtin.get_url", task)
        self.assertEqual(task.get("register"), register_name)
        self.assertEqual(task.get("until"), f"{register_name} is succeeded")
        self.assertEqual(task.get("retries"), 5)
        self.assertEqual(task.get("delay"), 5)

    def test_docker_apt_key_fetch_retries_transient_network_failures(self):
        tasks = self.load_tasks("debian-docker-host")
        task = self.task_named(tasks, "Install Docker apt signing key")
        self.assert_get_url_retries(task, "debian_docker_apt_signing_key")

    def test_tailscale_apt_signing_key_fetch_retries_transient_network_failures(self):
        tasks = self.load_tasks("debian-tailscale-client")
        self.assert_get_url_retries(
            self.task_named(tasks, "Install the Tailscale apt signing key"),
            "debian_tailscale_apt_signing_key",
        )

    def test_tailscale_apt_source_list_is_rendered_without_remote_fetch(self):
        tasks = self.load_tasks("debian-tailscale-client")
        task = self.task_named(tasks, "Install the Tailscale apt source list")
        self.assertIn("ansible.builtin.copy", task)
        self.assertNotIn("ansible.builtin.get_url", task)
        self.assertIn(
            "https://pkgs.tailscale.com/{{ debian_tailscale_track }}/debian",
            task["ansible.builtin.copy"]["content"],
        )


if __name__ == "__main__":
    unittest.main()
