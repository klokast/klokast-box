#!/usr/bin/env python3
import json
import unittest
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment
from ansible.plugins.filter.core import FilterModule
from ansible.plugins.filter.mathstuff import FilterModule as MathFilters


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
DEVELOPMENT_TOOLS_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "ops-controller" / "tasks" / "development-tools.yml"
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
    def test_managed_image_policy_keeps_boot_packages_before_pruning(self):
        policy_path = REPO_ROOT / 'ansible/roles/ops-controller/tasks/package-policy.yml'
        tasks = yaml.safe_load(policy_path.read_text())
        policy = next(t for t in tasks if 'ansible.builtin.set_fact' in t)
        environment = NativeEnvironment()
        environment.filters.update(FilterModule().filters())
        environment.filters.update(MathFilters().filters())
        environment.globals['lookup'] = lambda kind, path: Path(path).read_text()
        packages = environment.from_string(policy['ansible.builtin.set_fact']['ops_controller_packages']).render(
            ops_controller_packages=AUTHORIZED_PACKAGES,
            playbook_dir=str(REPO_ROOT / 'ansible/playbooks'))
        profile = json.loads((REPO_ROOT / 'ansible/update-profiles/ops-alpine-v1.json').read_text())
        self.assertEqual(set(packages), set(AUTHORIZED_PACKAGES) | set(profile['packages']))
        self.assertTrue({'linux-virt', 'mkinitfs', 'e2fsprogs', 'e2fsprogs-extra'} <= set(packages))
        self.assertEqual(policy['when'], 'ops_controller_template_marker.stat.exists')
        main = yaml.safe_load(CONTROLLER_TASKS.read_text())
        imports = [t.get('ansible.builtin.import_tasks') for t in main]
        self.assertLess(imports.index('package-policy.yml'), imports.index('packages.yml'))
        tools = yaml.safe_load((REPO_ROOT / 'ansible/playbooks/67-ops-controller-tools.yml').read_text())[0]
        imports = [Path(t['ansible.builtin.import_tasks']).name for t in tools['tasks'] if 'ansible.builtin.import_tasks' in t]
        self.assertLess(imports.index('package-policy.yml'), imports.index('packages.yml'))

    def test_health_checks_reject_missing_running_kernel_modules(self):
        checks = yaml.safe_load(VERIFY_TASKS.read_text())
        self.assertEqual(checks[0]['ansible.builtin.import_tasks'], '../../ops-controller/tasks/package-policy.yml')
        check = next(t for t in checks if t.get('name') == 'Verify managed controller kernel modules and filesystem recovery tools')
        program = check['ansible.builtin.command']['argv'][-1]
        # Execute the real health probe with a missing module database.
        from unittest.mock import patch
        with patch('pathlib.Path.is_file', return_value=False):
            with self.assertRaisesRegex(AssertionError, 'running kernel modules are absent'):
                exec(program, {})
        self.assertIn("shutil.which('fsck.ext4')", program)
        self.assertEqual(check['when'], 'ops_controller_template_marker.stat.exists')

    def test_controller_tools_include_apply_helpers_and_scope_freebox_to_france(self):
        variables = yaml.safe_load(OPS_VARS.read_text(encoding="utf-8"))
        wrapper_template = NativeEnvironment().from_string(
            variables["ops_controller_secret_authority_wrappers"]
        )
        self.assertEqual(
            wrapper_template.render(platform_download_sources={"country": "CN"}),
            ["ksa-static-site", "ksa-instance-key"],
        )
        self.assertEqual(
            wrapper_template.render(platform_download_sources={"country": "FR"}),
            ["ksa-static-site", "ksa-instance-key", "ksa-freebox-credential", "freebox-ipv6-broker"],
        )

        tasks = yaml.safe_load(DEVELOPMENT_TOOLS_TASKS.read_text(encoding="utf-8"))
        names = {task.get("name") for task in tasks}
        self.assertTrue({
            "Install root-owned Secret Authority wrapper executables",
            "Install the root-only Tailnet policy mutation helper",
            "Install the checked Tailnet policy renderer",
            "Install the checked Platform resource compiler",
            "Install the checked overlay IPv6 network helpers",
        } <= names)
        installed_destinations = {
            task["ansible.builtin.copy"]["dest"]
            for task in tasks if "ansible.builtin.copy" in task
        }
        self.assertIn("/usr/local/libexec/klokast/ts-policy-mutate-internal", installed_destinations)
        self.assertIn("/usr/local/sbin/render-tailscale-policy", installed_destinations)
        self.assertFalse(any("tailscale-policy.env" in str(task) for task in tasks))

        checks = yaml.safe_load(VERIFY_TASKS.read_text(encoding="utf-8"))
        check = next(task for task in checks if task.get("name") == "Inspect checked and installed Apply toolchain components")
        require = next(task for task in checks if task.get("name") == "Require exact checked and installed Apply toolchain bytes")
        self.assertIn("platform_download_sources.country == 'FR'", check["when"])
        self.assertIn("platform_download_sources.country == 'FR'", require["when"])
        active = next(task for task in checks if task.get("name") == "Require active-controller Tailnet policy credentials to be locked down")
        standby = next(task for task in checks if task.get("name") == "Require standby-controller Tailnet policy credentials to be absent")
        self.assertEqual(active["when"], "ops_controller_check_ha.active | bool")
        self.assertEqual(standby["when"], "not (ops_controller_check_ha.active | bool)")
        self.assertIn("not (item.stat.exists | default(false))", standby["ansible.builtin.assert"]["that"])

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
        tools = yaml.safe_load((REPO_ROOT / "ansible/playbooks/67-ops-controller-tools.yml").read_text())
        imports = [task.get("ansible.builtin.import_tasks", "") for task in tools[0]["tasks"]]
        self.assertIn("../roles/ops-controller/tasks/tailscale-distsign.yml", imports)
        self.assertNotIn("../roles/ops-controller/tasks/go-toolchain.yml", imports)
        source = yaml.safe_load(TAILSCALE_DIST_SIGN_TASKS.read_text(encoding="utf-8"))
        self.assertTrue(any(task.get("ansible.builtin.import_tasks") == "go-toolchain.yml" for task in source))
        compiler = yaml.safe_load((REPO_ROOT / "ansible/roles/ops-controller/tasks/go-toolchain.yml").read_text())
        self.assertTrue(any(task.get("name") == "Download the checksum-verified current stable Go toolchain" for task in compiler))
        self.assertTrue(any(task.get("name") == "Build the checksum-frozen verifier in a networkless user namespace" for task in source))
        verification = VERIFY_TASKS.read_text(encoding="utf-8")
        self.assertIn("ops_controller_check_distsign.binary_sha256 == ops_controller_check_distsign_binary.stat.checksum", verification)
        self.assertIn("ops_controller_check_distsign.go_version ~ ' linux/amd64'", verification)

    def test_verifier_uses_compiled_sources_and_checks_source_drift(self):
        source = yaml.safe_load(TAILSCALE_DIST_SIGN_TASKS.read_text())
        self.assertIn('ansible.builtin.assert', source[0])
        fetch = next(t for t in source if t.get('name') == 'Fetch and verify exact Go module dependencies without credentials')
        self.assertIn('"GOPROXY": platform_download_sources.go_proxy', fetch['environment'])
        self.assertIn('tailscale-build', fetch['ansible.builtin.command']['argv'])
        record = next(t for t in source if t.get('name') == 'Record the verifier source and binary identity')
        self.assertIn("'download_sources': platform_download_sources", record['ansible.builtin.copy']['content'])
        checks = yaml.safe_load(VERIFY_TASKS.read_text())
        check = next(t for t in checks if t.get('name') == 'Require the installed compiler and matching verifier build record')
        self.assertIn('ops_controller_check_distsign.download_sources | default({}) == platform_download_sources',
                      check['ansible.builtin.assert']['that'])

    def test_wrapper_exposes_explicit_prune_flag(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("--prune-package-drift", text)
        self.assertIn('PRUNE_PACKAGE_DRIFT=0', text)
        self.assertIn('"ops_controller_prune_package_drift"', text)


if __name__ == "__main__":
    unittest.main()
