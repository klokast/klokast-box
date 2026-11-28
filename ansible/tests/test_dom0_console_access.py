#!/usr/bin/env python3
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DOM0_VARS = REPO_ROOT / "ansible" / "inventory-policy" / "group_vars" / "dom0.yml"
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

    def test_provisioning_runner_loads_private_console_vars(self):
        text = PROVISION_BOX.read_text(encoding="utf-8")
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
        self.assertIn("root password is locked", docs)
        self.assertIn("dom0-console.yml", docs)
        self.assertIn("root` password is locked", docs)
        self.assertIn("NanoKVM recovery invariant", docs)


class Dom0VisibleConsoleTest(unittest.TestCase):
    def test_provisioning_xen_entry_uses_the_declared_linux_arguments(self):
        import jinja2
        variables = yaml.safe_load(DOM0_VARS.read_text(encoding="utf-8"))
        template = (REPO_ROOT / "ansible/roles/xen-host/templates/grub-xen.cfg.j2").read_text(encoding="utf-8")
        text = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(template).render(
            dom0_linux_args=variables["dom0_linux_args"], efi_partition_label="ALPINE_EFI",
            diskless_modloop_kernel_param=variables["diskless_modloop_kernel_param"],
            diskless_alpine_repo_kernel_param="", diskless_apkovl_kernel_param="")
        self.assertIn("console=tty1", variables["dom0_linux_args"].split())
        self.assertIn("    module2 /boot/vmlinuz-lts " + variables["dom0_linux_args"]
                      + " modloop=/boot/modloop-lts alpine_dev=LABEL=ALPINE_EFI\n", text)
        # The rescue entry boots Linux without Xen: tty0 is its only console.
        self.assertIn("    linux /boot/vmlinuz-lts modules=loop,squashfs,sd-mod,usb-storage,nvme quiet "
                      "modloop=/boot/modloop-lts alpine_dev=LABEL=ALPINE_EFI\n", text)

    def test_no_task_removes_the_tty0_getty_outside_the_dom0_update(self):
        # The declared console=tty1 removes the cause. Only the dom0 update
        # selection removes the getty that older boot entries left.
        self.assertNotIn("tty0", SYSTEM_IDENTITY_TASKS.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
