#!/usr/bin/env python3
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


class DebianRoleAptIdempotenceTest(unittest.TestCase):
    def load_tasks(self, role_name):
        path = REPO_ROOT / "ansible" / "roles" / role_name / "tasks" / "main.yml"
        return yaml.safe_load(path.read_text())

    def task_named(self, tasks, name):
        for task in tasks:
            if task.get("name") == name:
                return task
        self.fail(f"missing task: {name}")

    def assert_task_precedes(self, tasks, earlier_name, later_name):
        task_names = [task.get("name") for task in tasks]
        self.assertLess(task_names.index(earlier_name), task_names.index(later_name))

    def assert_set_fact_keys(self, task, expected_keys):
        self.assertIn("ansible.builtin.set_fact", task)
        self.assertEqual(set(task["ansible.builtin.set_fact"]), set(expected_keys))

    def assert_apt_task_is_missing_package_gated(self, task, missing_fact):
        self.assertIn("ansible.builtin.apt", task)
        self.assertEqual(task.get("when"), f"{missing_fact} | length > 0")
        self.assertIn(missing_fact, task["ansible.builtin.apt"]["name"])
        self.assertTrue(task["ansible.builtin.apt"]["update_cache"])

    def test_debian_vm_base_apt_refresh_is_missing_package_gated(self):
        tasks = self.load_tasks("debian-vm-base")
        self.task_named(tasks, "Collect Debian base VM package status")
        self.assert_task_precedes(
            tasks,
            "Derive installed Debian base VM packages",
            "Derive missing Debian base VM packages",
        )
        self.assert_set_fact_keys(
            self.task_named(tasks, "Derive installed Debian base VM packages"),
            ["debian_vm_base_installed_packages"],
        )
        self.assert_set_fact_keys(
            self.task_named(tasks, "Derive missing Debian base VM packages"),
            ["debian_vm_base_missing_packages"],
        )
        self.assert_apt_task_is_missing_package_gated(
            self.task_named(tasks, "Ensure Debian base VM packages are installed"),
            "debian_vm_base_missing_packages",
        )

    def test_debian_docker_host_apt_refresh_is_missing_package_gated(self):
        tasks = self.load_tasks("debian-docker-host")
        self.task_named(tasks, "Collect Debian Docker package status")
        self.assert_task_precedes(
            tasks,
            "Derive Debian Docker package installation state",
            "Derive missing Debian Docker packages",
        )
        self.assert_set_fact_keys(
            self.task_named(tasks, "Derive Debian Docker package installation state"),
            ["debian_docker_installed_packages"],
        )
        self.assert_set_fact_keys(
            self.task_named(tasks, "Derive missing Debian Docker packages"),
            ["debian_docker_prerequisites_missing", "debian_docker_engine_missing"],
        )
        self.assert_apt_task_is_missing_package_gated(
            self.task_named(tasks, "Ensure Docker apt prerequisites are installed"),
            "debian_docker_prerequisites_missing",
        )
        self.assert_apt_task_is_missing_package_gated(
            self.task_named(tasks, "Install Docker Engine on Debian"),
            "debian_docker_engine_missing",
        )
        self.assertEqual(
            self.task_named(tasks, "Install Docker apt signing key").get("when"),
            "not debian_docker_apt_signing_key_stat.stat.exists",
        )

    def test_debian_vm_firewall_apt_refresh_is_missing_package_gated(self):
        tasks = self.load_tasks("debian-vm-firewall")
        self.task_named(tasks, "Collect Debian VM firewall package status")
        self.assert_task_precedes(
            tasks,
            "Derive installed Debian VM firewall packages",
            "Derive missing Debian VM firewall packages",
        )
        self.assert_set_fact_keys(
            self.task_named(tasks, "Derive installed Debian VM firewall packages"),
            ["debian_vm_firewall_installed_packages"],
        )
        self.assert_set_fact_keys(
            self.task_named(tasks, "Derive missing Debian VM firewall packages"),
            ["debian_vm_firewall_missing_packages"],
        )
        self.assert_apt_task_is_missing_package_gated(
            self.task_named(tasks, "Ensure Debian VM firewall packages are installed"),
            "debian_vm_firewall_missing_packages",
        )


if __name__ == "__main__":
    unittest.main()
