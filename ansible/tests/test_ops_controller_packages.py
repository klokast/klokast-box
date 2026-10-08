#!/usr/bin/env python3
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_VARS = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "ops.yml"
CONVERGE_PLAYBOOK = REPO_ROOT / "ansible" / "playbooks" / "67-ops-controller-converge.yml"
CONTROLLER_TASKS = REPO_ROOT / "ansible" / "roles" / "ops-controller" / "tasks" / "main.yml"
VERIFY_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "ops-controller-verification" / "tasks" / "main.yml"
)
TAILSCALE_DIST_SIGN_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "ops-controller" / "tasks" / "tailscale-distsign.yml"
)
WRAPPER = REPO_ROOT / "ansible" / "bin" / "converge-ops-controller"


AUTHORIZED_PACKAGES = [
    "alpine-base",
    "ansible",
    "bash",
    "ca-certificates",
    "curl",
    "doas",
    "git",
    "iproute2",
    "jq",
    "nftables",
    "openssh",
    "openssh-client-default",
    "openssl",
    "podman",
    "py3-bcrypt",
    "py3-cryptography",
    "py3-jinja2",
    "py3-pygithub",
    "py3-yaml",
    "python3",
    "rsync",
    "skopeo",
    "sudo",
    "tailscale",
    "tailscale-openrc",
    "tmux",
    "wget",
]


class OpsControllerPackagePolicyTest(unittest.TestCase):
    def test_inventory_defines_one_exact_authorized_package_list(self):
        variables = yaml.safe_load(OPS_VARS.read_text(encoding="utf-8"))
        self.assertEqual(variables["ops_controller_packages"], AUTHORIZED_PACKAGES)
        self.assertNotIn("ops_controller_removed_packages", variables)

    def test_existing_controller_playbook_does_not_override_package_policy(self):
        plays = yaml.safe_load(CONVERGE_PLAYBOOK.read_text(encoding="utf-8"))
        self.assertNotIn("ops_controller_packages", plays[0].get("vars", {}))

    def test_controller_role_audits_before_install_and_prunes_only_with_approval(self):
        tasks = yaml.safe_load((REPO_ROOT / "ansible/roles/ops-controller/tasks/packages.yml").read_text(encoding="utf-8"))
        names = [task.get("name") for task in tasks]
        self.assertLess(
            names.index("Require explicit approval before pruning APK world drift"),
            names.index("Ensure ops controller packages are installed"),
        )
        self.assertLess(
            names.index("Ensure ops controller packages are installed"),
            names.index("Remove reviewed unauthorized APK world entries"),
        )
        package_tasks = [
            task
            for task in tasks
            if "community.general.apk" in task
            and task["community.general.apk"].get("state") == "present"
        ]
        self.assertEqual(len(package_tasks), 1)
        self.assertEqual(
            package_tasks[0]["community.general.apk"]["name"],
            "{{ ops_controller_packages }}",
        )
        text = (REPO_ROOT / "ansible/roles/ops-controller/tasks/packages.yml").read_text(encoding="utf-8")
        self.assertIn("ops_controller_prune_package_drift", text)
        self.assertIn("['/sbin/apk', 'del', '--simulate']", text)
        self.assertIn("['/sbin/apk', 'del'] + ops_controller_apk_world_drift", text)
        self.assertIn("ops_controller_apk_world_after_packages", text)
        self.assertIn("[@<>=~].*$", text)
        absent_package_tasks = [
            task
            for task in tasks
            if "community.general.apk" in task
            and task["community.general.apk"].get("state") == "absent"
        ]
        self.assertEqual(absent_package_tasks, [])

    def test_verification_requires_exact_world_and_runtime_tools(self):
        text = VERIFY_TASKS.read_text(encoding="utf-8")
        self.assertIn("ops_controller_check_apk_world_packages", text)
        self.assertIn("ops_controller_packages | unique | sort | list", text)
        self.assertIn("- podman", text)
        self.assertIn("- skopeo", text)
        self.assertIn("import bcrypt", text)

    def test_controller_template_and_convergence_install_verified_distsign_toolchain(self):
        tasks = yaml.safe_load(CONTROLLER_TASKS.read_text(encoding="utf-8"))
        install = next(task for task in tasks if task.get("name") == "Install the current Go and Tailscale signature verifier toolchain")
        self.assertEqual(install["ansible.builtin.import_tasks"], "tailscale-distsign.yml")
        self.assertIn("ops-controller-tailscale-distsign", install["tags"])
        source = yaml.safe_load(TAILSCALE_DIST_SIGN_TASKS.read_text(encoding="utf-8"))
        self.assertTrue(any(task.get("ansible.builtin.import_tasks") == "go-toolchain.yml" for task in source))
        compiler = yaml.safe_load((REPO_ROOT / "ansible/roles/ops-controller/tasks/go-toolchain.yml").read_text())
        self.assertTrue(any(task.get("name") == "Download the checksum-verified current stable Go toolchain" for task in compiler))
        self.assertTrue(any(task.get("name") == "Build the checksum-frozen verifier in a networkless user namespace" for task in source))
        verification = VERIFY_TASKS.read_text(encoding="utf-8")
        self.assertIn("ops_controller_check_distsign.binary_sha256 == ops_controller_check_distsign_binary.stat.checksum", verification)
        self.assertIn("ops_controller_check_distsign.go_version ~ ' linux/amd64'", verification)

    def test_wrapper_exposes_explicit_prune_flag(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("--prune-package-drift", text)
        self.assertIn('PRUNE_PACKAGE_DRIFT=0', text)
        self.assertIn('"ops_controller_prune_package_drift"', text)


if __name__ == "__main__":
    unittest.main()
