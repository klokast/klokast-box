#!/usr/bin/env python3
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DOM0_VARS = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "dom0.yml"
SYSTEM_IDENTITY_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "system-identity" / "tasks" / "main.yml"
)
DOM0_BASE_VERIFY_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "dom0-base-verification" / "tasks" / "main.yml"
)
DOM0_HEALTH_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "dom0-health-verification" / "tasks" / "main.yml"
)
PROVISION_BOX = REPO_ROOT / "ansible" / "bin" / "provision-box"
BOOTSTRAP_DOM0 = REPO_ROOT / "ansible" / "bin" / "bootstrap-dom0"
ARCHITECTURE_DOC = REPO_ROOT / "doc" / "architecture.md"
PLATFORM_DEPLOY_DOC = REPO_ROOT / "doc" / "platform-deploy.md"
DOM0_PLAYBOOK_OVERVIEW = (
    REPO_ROOT / "ansible" / "overview-playbooks" / "playbooks-2x-dom0.md"
)
PLATFORM_CHECK_OVERVIEW = (
    REPO_ROOT / "ansible" / "overview-playbooks" / "playbooks-7x-platform-map.md"
)


class Dom0ConsoleAccessTest(unittest.TestCase):
    def test_dom0_vars_reference_private_console_hashes(self):
        data = yaml.safe_load(DOM0_VARS.read_text(encoding="utf-8"))

        self.assertEqual(data["dom0_admin_user_name"], "neo")
        self.assertEqual(data["dom0_console_password_hashes"], {})
        self.assertIn("dom0_console_password_hashes[node_name]", data["dom0_admin_password_hash"])
        self.assertTrue(data["dom0_lock_root_password"])
        self.assertEqual(data["dom0_local_users"][0]["password_hash"], "{{ dom0_admin_password_hash }}")

    def test_system_identity_sets_neo_password_and_locks_root(self):
        text = SYSTEM_IDENTITY_TASKS.read_text(encoding="utf-8")

        self.assertIn("Require a private dom0 console password hash", text)
        self.assertIn("password: \"{{ item.password_hash }}\"", text)
        self.assertIn("password_lock: false", text)
        self.assertIn("update_password: always", text)
        self.assertIn("Lock the root password on dom0", text)
        self.assertIn("/usr/bin/passwd -l root", text)

    def test_dom0_verification_rejects_missing_console_access(self):
        base_text = DOM0_BASE_VERIFY_TASKS.read_text(encoding="utf-8")
        health_text = DOM0_HEALTH_TASKS.read_text(encoding="utf-8")

        self.assertIn("Collect dom0 console shadow entries", base_text)
        self.assertIn("is search('^' ~ dom0_admin_user_name ~ ':\\\\$'", base_text)
        self.assertIn("is search('^root:[!*]'", base_text)
        self.assertIn("permit nopass :wheel", base_text)

        self.assertIn("console password is not a usable hash", health_text)
        self.assertIn("root password is not locked", health_text)
        self.assertIn("doas policy does not permit passwordless wheel escalation", health_text)

    def test_provisioning_wrappers_load_private_console_vars(self):
        for path in (PROVISION_BOX, BOOTSTRAP_DOM0):
            text = path.read_text(encoding="utf-8")
            self.assertIn("DOM0_CONSOLE_VARS=\"$PLATFORM_PRIVATE_ROOT/dom0-console.yml\"", text)
            self.assertIn("phase_uses_dom0_console_vars", text)
            self.assertIn('cmd+=(-e "@$DOM0_CONSOLE_VARS")', text)
            self.assertIn("missing private dom0 console vars", text)

    def test_docs_state_console_access_invariant(self):
        docs = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ARCHITECTURE_DOC,
                PLATFORM_DEPLOY_DOC,
                DOM0_PLAYBOOK_OVERVIEW,
                PLATFORM_CHECK_OVERVIEW,
            )
        )

        self.assertIn("NanoKVM recovery requires local console login as `neo`", docs)
        self.assertIn("blank root console access is forbidden", docs)
        self.assertIn("dom0-console.yml", docs)
        self.assertIn("root` password is locked", docs)
        self.assertIn("NanoKVM recovery invariant", docs)


if __name__ == "__main__":
    unittest.main()
