#!/usr/bin/env python3
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DOM0_VARS = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "dom0.yml"
ALPINE_BASE_TASKS = REPO_ROOT / "ansible" / "roles" / "alpine-base" / "tasks" / "main.yml"
DOM0_STORAGE_TASKS = REPO_ROOT / "ansible" / "roles" / "dom0-storage" / "tasks" / "main.yml"
DOM0_HEALTH_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "dom0-health-verification" / "tasks" / "main.yml"
)
ARCHITECTURE_DOC = REPO_ROOT / "doc" / "architecture.md"
DOM0_PLAYBOOK_OVERVIEW = (
    REPO_ROOT / "ansible" / "overview-playbooks" / "playbooks-2x-dom0.md"
)


class Dom0LbuPolicyTest(unittest.TestCase):
    def test_dom0_data_mount_is_not_requested_as_lbu_include(self):
        data = yaml.safe_load(DOM0_VARS.read_text(encoding="utf-8"))

        self.assertEqual(data["dom0_data_mount_path"], "/mnt/dom0_data")
        self.assertNotIn(
            "{{ dom0_data_mount_path }}",
            data.get("dom0_lbu_include_paths", []),
        )

    def test_dom0_storage_removes_include_and_adds_exclude(self):
        text = DOM0_STORAGE_TASKS.read_text(encoding="utf-8")

        self.assertIn("Remove the legacy dom0 data lbu include", text)
        self.assertIn("Ensure dom0 data stays outside lbu payloads", text)
        self.assertIn('line: "-{{ dom0_data_mount_path | regex_replace', text)

    def test_alpine_base_adds_dom0_data_lbu_exclude_before_storage_phase(self):
        text = ALPINE_BASE_TASKS.read_text(encoding="utf-8")

        self.assertIn("Ensure the dom0 data mountpoint stays excluded from lbu payloads", text)
        self.assertIn('line: "-{{ dom0_data_mount_path | regex_replace', text)

    def test_dom0_health_rejects_dom0_data_lbu_include(self):
        text = DOM0_HEALTH_TASKS.read_text(encoding="utf-8")

        self.assertIn("dom0 data mount is included in lbu protected paths", text)
        self.assertIn("dom0 data mount is not excluded from lbu protected paths", text)

    def test_docs_state_dom0_data_lbu_boundary(self):
        docs = "\n".join(
            [
                ARCHITECTURE_DOC.read_text(encoding="utf-8"),
                DOM0_PLAYBOOK_OVERVIEW.read_text(encoding="utf-8"),
            ]
        )

        self.assertIn("Diskless persistence boundary", docs)
        self.assertIn("Diskless persistence invariant", docs)
        self.assertIn("-mnt/dom0_data", docs)
        self.assertIn("+mnt/dom0_data", docs)


if __name__ == "__main__":
    unittest.main()
