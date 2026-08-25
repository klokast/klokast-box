#!/usr/bin/env python3
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DOM0_VARS = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "dom0.yml"
ALL_VARS = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all.yml"
POLICY_TASKS = REPO_ROOT / "ansible" / "roles" / "dom0-apk-policy" / "tasks" / "main.yml"
POLICY_HANDLERS = (
    REPO_ROOT / "ansible" / "roles" / "dom0-apk-policy" / "handlers" / "main.yml"
)
UNLOCK_TASKS = (
    REPO_ROOT
    / "ansible"
    / "roles"
    / "dom0-apk-policy"
    / "tasks"
    / "maintenance-unlock.yml"
)
GUARD_TEMPLATE = (
    REPO_ROOT
    / "ansible"
    / "roles"
    / "dom0-apk-policy"
    / "templates"
    / "20-klokast-dom0-world-guard.sh.j2"
)
PHASE_20 = REPO_ROOT / "ansible" / "playbooks" / "20-dom0-base-bootstrap.yml"
PHASE_22 = REPO_ROOT / "ansible" / "playbooks" / "22-dom0-base-verify.yml"
DEBIAN_ROOTFS = (
    REPO_ROOT / "ansible" / "roles" / "debian-app-vm-rootfs" / "tasks" / "main.yml"
)
APP_VM_DOM0 = REPO_ROOT / "ansible" / "playbooks" / "tasks" / "platform-app-vm-dom0.yml"


class Dom0ApkPolicyTest(unittest.TestCase):
    def setUp(self):
        self.variables = yaml.safe_load(DOM0_VARS.read_text(encoding="utf-8"))
        self.all_variables = yaml.safe_load(ALL_VARS.read_text(encoding="utf-8"))

    def test_world_is_a_small_exact_runtime_allowlist(self):
        world = self.variables["dom0_world_packages"]
        maintenance = self.variables["dom0_maintenance_package_allowlist"]
        forbidden = self.variables["dom0_forbidden_steady_state_packages"]

        self.assertEqual(len(world), len(set(world)))
        self.assertFalse(set(world).intersection(maintenance))
        self.assertIn("lvm2", world)
        self.assertIn("openssl", world)
        self.assertIn("xen", world)
        self.assertIn("xen-hypervisor", world)
        self.assertIn("python3", world)
        for package in (
            "curl",
            "e2fsprogs-extra",
            "gzip",
            "kpartx",
            "sfdisk",
            "tar",
            "util-linux",
            "xorriso",
        ):
            self.assertNotIn(package, world)

        # Xen needs libcurl on the live Alpine 3.23 repository. Keep it out of
        # the explicit world while permitting APK to retain it as a runtime
        # dependency.
        self.assertNotIn("libcurl", world)
        self.assertNotIn("libcurl", forbidden)

        # Alpine's diskless initramfs adds openssl at every boot for modloop
        # signature verification. The exact persisted world must include it,
        # or every reboot creates immediate world drift.
        self.assertNotIn("openssl", forbidden)

    def test_policy_renders_and_verifies_exact_world(self):
        text = POLICY_TASKS.read_text(encoding="utf-8")

        self.assertIn("Install the complete steady-state dom0 package set together", text)
        self.assertIn("dest: /etc/apk/world", text)
        self.assertIn("dom0_world_packages | sort | join", text)
        self.assertIn("Assert the dom0 APK world is the exact allowlist", text)
        self.assertIn("Assert dom0 runs the managed Alpine release branch", text)
        self.assertIn("Pin dom0 APK repositories to the managed Alpine release branch", text)
        self.assertIn("Reconcile installed packages to the managed Alpine release branch", text)
        self.assertIn("--cache-predownload", text)
        self.assertIn("--timeout", text)
        self.assertIn("--available", text)
        self.assertIn("Probe forbidden steady-state dom0 packages", text)
        self.assertIn("Collect pending diskless changes after dom0 APK convergence", text)
        self.assertIn("ansible.builtin.command: lbu status", text)
        self.assertIn("dom0_apk_lbu_status_after.stdout | trim | length > 0", text)
        self.assertIn("always:", text)
        self.assertIn("Lock dom0 APK transactions after convergence", text)

        handlers = POLICY_HANDLERS.read_text(encoding="utf-8")
        self.assertIn("lbu commit -d", handlers)
        self.assertNotIn("/sbin/lbu", handlers)

    def test_dom0_repository_branch_is_fixed(self):
        branch = self.all_variables["alpine_release_branch"]
        repositories = self.all_variables["alpine_repositories"]

        self.assertEqual(branch, "v3.23")
        self.assertTrue(repositories)
        self.assertTrue(all("latest-stable" not in item for item in repositories))
        self.assertTrue(all("{{ alpine_release_branch }}" in item for item in repositories))

    def test_guard_fails_closed_without_ram_unlock(self):
        guard = GUARD_TEMPLATE.read_text(encoding="utf-8")
        unlock = UNLOCK_TASKS.read_text(encoding="utf-8")

        self.assertIn('pre-commit)', guard)
        self.assertIn("/run/openrc/softlevel", guard)
        self.assertIn("transaction is locked", guard)
        self.assertIn("invalid maintenance unlock", guard)
        self.assertIn("mode: \"0600\"", unlock)
        self.assertIn("difference(dom0_maintenance_package_allowlist)", unlock)
        self.assertIn("Lock APK after a failed maintenance package transaction", unlock)

    def test_phase_20_converges_packages_before_identity_policy(self):
        text = PHASE_20.read_text(encoding="utf-8")

        self.assertLess(text.index("name: dom0-apk-policy"), text.index("name: system-identity"))

    def test_dom0_uses_release_pinned_https_mirrors(self):
        repositories = self.variables["alpine_repositories"]

        self.assertEqual(len(repositories), 2)
        self.assertTrue(all(value.startswith("https://") for value in repositories))
        self.assertTrue(all("dl-cdn.alpinelinux.org" in value for value in repositories))
        self.assertTrue(all("{{ alpine_release_branch }}" in value for value in repositories))
        self.assertTrue(all("latest-stable" not in value for value in repositories))

    def test_phase_22_repairs_existing_steady_state_dom0(self):
        text = PHASE_22.read_text(encoding="utf-8")

        self.assertIn("Converge the exact steady-state dom0 package policy", text)
        self.assertLess(text.index("name: dom0-apk-policy"), text.rindex("name: dom0-tailscale"))

    def test_debian_rootfs_tools_are_transient_and_include_gzip(self):
        role = DEBIAN_ROOTFS.read_text(encoding="utf-8")
        parent = APP_VM_DOM0.read_text(encoding="utf-8")

        self.assertNotIn("community.general.apk", role)
        self.assertIn("'gzip'", parent)
        self.assertIn("tasks_from: maintenance-lock.yml", parent)
        self.assertLess(
            parent.index("tasks_from: maintenance-lock.yml"),
            parent.index("Persist platform app VM state on the diskless dom0 host"),
        )

    def test_persisting_build_playbooks_lock_before_lbu_commit(self):
        for relative in (
            "30-vm-router-alpine-build.yml",
            "40-vm-golden-image.yml",
            "41-vm-backend.yml",
            "42-vm-dmz.yml",
            "43-vm-iot.yml",
            "65-vm-ops.yml",
        ):
            with self.subTest(playbook=relative):
                text = (REPO_ROOT / "ansible" / "playbooks" / relative).read_text(
                    encoding="utf-8"
                )
                self.assertIn("tasks_from: maintenance-unlock.yml", text)
                self.assertIn("tasks_from: maintenance-lock.yml", text)
                self.assertLess(
                    text.index("tasks_from: maintenance-lock.yml"),
                    text.index("lbu commit -d"),
                )


if __name__ == "__main__":
    unittest.main()
