#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "ansible" / "roles" / "ops-airunner"
TASKS = (ROLE_ROOT / "tasks" / "main.yml").read_text(encoding="utf-8")
SERVICE = (ROLE_ROOT / "templates" / "airunner.init.j2").read_text(encoding="utf-8")
CONTAINERFILE = (ROLE_ROOT / "files" / "Containerfile").read_text(encoding="utf-8")
ENTRYPOINT = (ROLE_ROOT / "files" / "airunner-entrypoint").read_text(encoding="utf-8")
PLAYBOOK = (REPO_ROOT / "ansible" / "playbooks" / "68-ops-airunner.yml").read_text(
    encoding="utf-8"
)
RETIRE_PLAYBOOK = (
    REPO_ROOT / "ansible" / "playbooks" / "68-ops-airunner-candidate-retire.yml"
).read_text(encoding="utf-8")
FIREWALL = (
    REPO_ROOT / "ansible" / "roles" / "podman-vm-firewall" / "templates" / "nftables.nft.j2"
).read_text(encoding="utf-8")
VERIFY = (
    REPO_ROOT / "ansible" / "roles" / "ops-airunner-verification" / "tasks" / "main.yml"
).read_text(encoding="utf-8")
OPS_VARS = (REPO_ROOT / "ansible" / "inventory" / "group_vars" / "ops.yml").read_text(
    encoding="utf-8"
)
SECRETS_PLAYBOOK = (
    REPO_ROOT / "ansible" / "playbooks" / "66-ops-controller-secrets.yml"
).read_text(encoding="utf-8")
OPS_CONTROLLER_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "ops-controller" / "tasks" / "main.yml"
).read_text(encoding="utf-8")


class OpsAirunnerRoleTest(unittest.TestCase):
    def test_container_runs_with_own_tailscale_networking(self):
        self.assertIn("--network {{ ops_airunner_network_name }}", SERVICE)
        self.assertNotIn("--network host", SERVICE)
        self.assertIn("--cap-add NET_ADMIN", SERVICE)
        self.assertIn("--cap-add NET_RAW", SERVICE)
        self.assertIn("--device /dev/net/tun", SERVICE)
        self.assertIn("ops_airunner_tailscale_state_dir", SERVICE)
        self.assertIn("podman_vm_firewall_forward_egress_interfaces", FIREWALL)
        self.assertIn("map('regex_replace', '^', '--dns=')", TASKS)
        self.assertNotIn("--dns=\\\\1", TASKS)
        self.assertIn("firewall_driver = \"{{ ops_airunner_netavark_firewall_driver }}\"", TASKS)
        self.assertIn("ops_airunner_netavark_firewall_driver: nftables", OPS_VARS)
        self.assertIn("ops_airunner_network_gateway: 10.89.0.1", OPS_VARS)
        self.assertIn("comment: airunner-podman-dns-tcp", OPS_VARS)
        self.assertIn("comment: airunner-podman-dns-udp", OPS_VARS)
        self.assertIn(
            "(ops_airunner_network.subnets | first).gateway == ops_airunner_network_gateway",
            TASKS,
        )

    def test_airunner_image_starts_tailscaled(self):
        self.assertIn("tailscale", CONTAINERFILE)
        self.assertIn("ENTRYPOINT", CONTAINERFILE)
        self.assertIn("/usr/sbin/tailscaled", ENTRYPOINT)
        self.assertIn("/run/tailscale/tailscaled.sock", ENTRYPOINT)
        self.assertIn("--tun=tailscale0", ENTRYPOINT)
        self.assertIn("AIRUNNER_TAILSCALE_UDP_PORT", ENTRYPOINT)
        self.assertIn('--port="$tailscale_udp_port"', ENTRYPOINT)
        self.assertNotIn("--tun=userspace-networking", ENTRYPOINT)

    def test_airunner_image_has_interactive_terminal_tools(self):
        self.assertIn("mosh", CONTAINERFILE)
        self.assertIn("tmux", CONTAINERFILE)
        for package in (
            "build-essential",
            "jq",
            "less",
            "python3-yaml",
            "ripgrep",
            "shellcheck",
            "vim",
        ):
            self.assertIn(package, CONTAINERFILE)
        for controller_package in ("ansible", "bubblewrap", "golang-go"):
            self.assertNotIn(controller_package, CONTAINERFILE)
        self.assertIn("ENV LANG=C.UTF-8", CONTAINERFILE)
        self.assertIn("ENV LC_ALL=C.UTF-8", CONTAINERFILE)
        self.assertIn("locales", CONTAINERFILE)
        self.assertIn("en_GB.UTF-8 UTF-8", CONTAINERFILE)
        self.assertIn("locale-gen", CONTAINERFILE)

    def test_airunner_role_enrolls_expected_tag(self):
        self.assertIn("ts-authkey-airunner", OPS_VARS)
        self.assertIn("tag:airunner", OPS_VARS)
        self.assertIn("ops_airunner_tailscale_authkey_wrapper", TASKS)
        self.assertIn("--advertise-tags={{ ops_airunner_tailscale_advertise_tags | quote }}", TASKS)
        self.assertIn('summary["tags"] == [expected_tag]', TASKS)
        self.assertIn("- doas\n      - \"{{ ops_airunner_tailscale_authkey_wrapper }}\"", TASKS)
        self.assertIn("ops-controller-tailscale-wrappers", OPS_CONTROLLER_TASKS)

    def test_blue_green_instances_follow_platform_naming(self):
        self.assertIn('"{{ node_name }}-ops-airunner"', OPS_VARS)
        self.assertIn('"{{ node_name }}-ops-airunner-candidate"', OPS_VARS)
        self.assertIn("service_name: airunner", OPS_VARS)
        self.assertIn("service_name: airunner-candidate", OPS_VARS)
        self.assertIn("tailscale_udp_port: 41642", OPS_VARS)
        self.assertIn("tailscale_udp_port: 41643", OPS_VARS)
        self.assertIn("ops_airunner_legacy_service_name: klokast-airunner", OPS_VARS)
        self.assertIn("ops_airunner_instance in ops_airunner_instances", PLAYBOOK)
        self.assertIn("ops_airunner_instances | dict2items", SECRETS_PLAYBOOK)
        self.assertNotIn('"{{ ops_airunner_name }}"', SECRETS_PLAYBOOK)

    def test_airunner_fixed_udp_port_is_propagated_and_verified(self):
        self.assertIn("ops_airunner_tailscale_udp_port", TASKS)
        self.assertIn("AIRUNNER_TAILSCALE_UDP_PORT={{ ops_airunner_tailscale_udp_port }}", SERVICE)
        self.assertIn(
            'ops_airunner_tailscale_udp_port: "{{ ops_airunner_selected.tailscale_udp_port }}"',
            PLAYBOOK,
        )
        self.assertIn("ops_airunner_verify_expected_udp_port", VERIFY)
        self.assertIn("--format=json", VERIFY)
        self.assertIn("ops_airunner_verify_netcheck", VERIFY)
        self.assertIn("--port=", VERIFY)

    def test_canonical_replacement_requires_candidate_verification(self):
        self.assertIn("ops_airunner_require_candidate_ready", PLAYBOOK)
        self.assertIn("ops-airunner-verification", PLAYBOOK)
        self.assertIn("ops_airunner_verify_workspace_features: false", PLAYBOOK)
        self.assertIn("ops_airunner_legacy_service_path", PLAYBOOK)
        self.assertIn("ops_airunner_selected.manage_legacy_service | bool", PLAYBOOK)

    def test_candidate_convergence_preserves_canonical_container(self):
        self.assertIn("ops_airunner_canonical_before", PLAYBOOK)
        self.assertIn("ops_airunner_canonical_after_command", PLAYBOOK)
        self.assertIn(
            "Candidate convergence changed the canonical airunner container",
            PLAYBOOK,
        )

    def test_runtime_verification_checks_boundary_and_mosh(self):
        self.assertIn("HostConfig.NetworkMode != 'host'", VERIFY)
        self.assertIn(
            "ops_airunner_verify_network_name in ops_airunner_verify_inspect.NetworkSettings.Networks",
            VERIFY,
        )
        self.assertIn("CAP_NET_ADMIN", VERIFY)
        self.assertIn("CAP_NET_RAW", VERIFY)
        self.assertIn("/dev/net/tun", VERIFY)
        self.assertIn("mosh-server", VERIFY)
        self.assertIn("airunner-verify-", VERIFY)
        self.assertIn("ops_airunner_verify_archive_command.rc == 0", VERIFY)
        self.assertIn("history-limit 100000", VERIFY)
        self.assertIn(".codex/auth.json", VERIFY)
        self.assertIn('"HOME={{ ops_airunner_verify_home }}"', VERIFY)
        self.assertEqual(VERIFY.count("expand_argument_vars: false"), 3)
        self.assertIn("/home/smith", VERIFY)
        self.assertIn("git", VERIFY)

    def test_airunner_home_is_reproducibly_managed(self):
        self.assertIn("Generate the airunner GitHub deploy key when missing", TASKS)
        self.assertIn("ops_airunner_git_ssh_key_path", TASKS)
        self.assertIn("HostKeyAlias {{ ops_github_ssh_host_key_alias }}", TASKS)
        self.assertIn("insertbefore: BOF", TASKS)
        self.assertIn("Register this public key as a write-enabled deploy key", TASKS)
        self.assertIn("Clone the airunner repository when absent", TASKS)
        self.assertIn("--single-branch", TASKS)
        self.assertIn('- "HOME={{ ops_airunner_home }}"', TASKS)
        self.assertNotIn('become_user: "{{ ops_airunner_user }}"', TASKS)
        self.assertIn("refusing to replace it", TASKS)
        self.assertIn("refusing to rewrite it", TASKS)
        self.assertIn("klokast airunner interactive shell", TASKS)
        self.assertIn("tmux new-session -A -s main", TASKS)
        self.assertIn("history-limit 100000", TASKS)

    def test_agent_commands_use_agent_accessible_working_directory(self):
        task_names = (
            "Generate the airunner GitHub deploy key when missing",
            "Check whether GitHub accepts the airunner deploy key",
            "Configure airunner Git defaults",
            "Clone the airunner repository when absent",
            "Read the airunner repository origin",
        )
        for task_name in task_names:
            task_start = TASKS.index(f"- name: {task_name}")
            task_end = TASKS.find("\n- name:", task_start + 1)
            task = TASKS[task_start : task_end if task_end >= 0 else None]
            self.assertIn('chdir: "{{ ops_airunner_home }}"', task, task_name)

    def test_image_activation_compares_image_ids(self):
        self.assertIn("Inspect the airunner image before build", TASKS)
        self.assertIn("Inspect the desired airunner image after build", TASKS)
        self.assertIn("ops_airunner_running_image.stdout != ops_airunner_image_after.stdout", TASKS)
        self.assertNotIn("ops_airunner_build.changed", TASKS)

    def test_candidate_retirement_is_guarded_and_scoped(self):
        self.assertIn("after canonical mosh verified", RETIRE_PLAYBOOK)
        self.assertIn("ops_airunner_instances.candidate", RETIRE_PLAYBOOK)
        self.assertIn("tailscale-stale-device", RETIRE_PLAYBOOK)
        self.assertIn("candidate Tailnet identity is absent", RETIRE_PLAYBOOK)
        self.assertNotIn("ops_airunner_canonical.state_root }}\"\n        state: absent", RETIRE_PLAYBOOK)

    def test_interactive_wrapper_enters_agent_user(self):
        self.assertIn("-u {{ ops_airunner_user }}", (ROLE_ROOT / "templates" / "airunner-shell.j2").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
