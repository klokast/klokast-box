"""Resource runtime regression tests."""
import argparse
from platform_resource_test_support import ResourceTestCase, REPO_ROOT, load_reconcile_module
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
import yaml
import platform_resource_model as model
import platform_resource_compiler as compiler
import platform_resource_runtime as runtime


class ResourceRuntimeTest(ResourceTestCase):
    def test_shared_guest_apply_holds_the_installed_update_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / 'operation.lock'
            lock.touch()
            lock.chmod(0o660)
            original_lstat, original_fstat = Path.lstat, os.fstat
            def lstat(path):
                value = list(original_lstat(path))
                if Path(path) == lock.parent:
                    value[4] = 0
                return os.stat_result(value)
            def fstat(descriptor):
                value = list(original_fstat(descriptor))
                value[4] = 0
                return os.stat_result(value)
            with patch.object(runtime, 'VM_UPDATE_INSTALL_LOCK', lock), \
                    patch.object(Path, 'lstat', autospec=True, side_effect=lstat), \
                    patch.object(os, 'fstat', side_effect=fstat):
                with runtime.vm_update_installation_lock():
                    with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        with runtime.vm_update_installation_lock():
                            pass

    def test_apply_converges_router_topology_before_resources(self):
        compiled = self.compiled_for_run()
        with patch.object(runtime.subprocess, "run") as run:
            runtime.run_ansible("apply", compiled, "example.ts.net", "abc123", repo_root=REPO_ROOT)

        playbook_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "ansible-playbook"
        ]
        self.assertEqual(len(playbook_calls), 2)
        first_cmd = playbook_calls[0]
        second_cmd = playbook_calls[1]
        self.assertIn("31-vm-router.yml", " ".join(map(str, first_cmd)))
        self.assertEqual(first_cmd[first_cmd.index("--limit") + 1], "boxa,boxb")
        self.assertIn("80-platform-resources.yml", " ".join(map(str, second_cmd)))
        self.assertEqual(second_cmd[second_cmd.index("--limit") + 1], "boxa-router")

    def test_verify_does_not_converge_router_topology(self):
        compiled = self.compiled_for_run()
        with patch.object(runtime.subprocess, "run") as run:
            runtime.run_ansible("verify", compiled, "example.ts.net", repo_root=REPO_ROOT)

        playbook_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "ansible-playbook"
        ]
        self.assertEqual(len(playbook_calls), 1)
        cmd = playbook_calls[0]
        self.assertNotIn("31-vm-router.yml", " ".join(map(str, cmd)))
        self.assertIn("81-platform-resources-verify.yml", " ".join(map(str, cmd)))
        self.assertEqual(cmd[cmd.index("--limit") + 1], "boxa-router")

    def test_apply_uses_tailscale_ssh_for_podman_resource_hosts(self):
        compiled = self.compiled_with_podman_resource_for_run()
        with patch.object(runtime.subprocess, "run") as run:
            runtime.run_ansible("apply", compiled, "example.ts.net", "abc123", repo_root=REPO_ROOT)

        playbook_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "ansible-playbook"
        ]
        resource_cmd = [
            call
            for call in playbook_calls
            if "80-platform-resources.yml" in " ".join(map(str, call))
        ][0]
        self.assertEqual(resource_cmd[resource_cmd.index("--limit") + 1], "boxa-router")

        tailscale_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "tailscale"
        ]
        self.assertTrue(tailscale_calls)
        self.assertTrue(all("neo@boxa-bak" in call for call in tailscale_calls))
        joined_playbooks = "\n".join(" ".join(map(str, call)) for call in playbook_calls)
        self.assertNotIn("boxa-bak", joined_playbooks)

        last_applied_uploads = [
            call
            for call in run.call_args_list
            if '"inventory_hostname": "boxa-bak"' in (call.kwargs.get("input") or "")
        ]
        self.assertEqual(len(last_applied_uploads), 1)
        self.assertIn('"approved_commit": "abc123"', last_applied_uploads[0].kwargs["input"])
        self.assertIn('"registry_sha256": "sha256-test"', last_applied_uploads[0].kwargs["input"])

    def test_verify_uses_tailscale_ssh_without_last_applied_upload(self):
        compiled = self.compiled_with_podman_resource_for_run()
        with patch.object(runtime.subprocess, "run") as run:
            runtime.run_ansible("verify", compiled, "example.ts.net", repo_root=REPO_ROOT)

        playbook_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "ansible-playbook"
        ]
        resource_cmd = playbook_calls[0]
        self.assertEqual(resource_cmd[resource_cmd.index("--limit") + 1], "boxa-router")

        tailscale_calls = [
            call
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "tailscale"
        ]
        self.assertEqual(len(tailscale_calls), 3)
        self.assertTrue(all("neo@boxa-bak" in call.args[0] for call in tailscale_calls))
        self.assertFalse(
            any('"inventory_hostname": "boxa-bak"' in (call.kwargs.get("input") or "") for call in tailscale_calls)
        )

    def test_podman_remote_script_requires_firewall_baseline(self):
        script = runtime.podman_resource_remote_script()
        self.assertIn("missing Podman VM firewall baseline", script)
        self.assertIn("/usr/sbin/nft -c -f /etc/nftables.nft", script)
        self.assertIn('if [ "$changed" = "1" ]; then', script)
        self.assertIn("/usr/sbin/nft -f /etc/nftables.nft", script)

    def test_failed_podman_verification_cleans_only_its_staged_files(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / ".cache"
            parent = cache / "klokast-platform-resources"
            parent.mkdir(parents=True)
            script = runtime.podman_resource_remote_script()
            script = script.replace("/home/neo/.cache/klokast-platform-resources", str(parent))
            script = script.replace("/home/neo/.cache", str(cache))
            script = script.replace(
                "if [ ! -x /usr/sbin/nft ] || [ ! -f /etc/nftables.nft ]; then",
                "if true; then",
            )

            for extra in (False, True):
                with self.subTest(unexpected_file=extra):
                    stage = parent / ("verify-with-extra" if extra else "verify-clean")
                    stage.mkdir()
                    files = [stage / name for name in (
                        "desired.json", "klokast-app-resources-reconcile", "last-applied.json")]
                    for path in files:
                        path.write_text("staged")
                    if extra:
                        (stage / "unexpected").write_text("keep")
                    result = subprocess.run(
                        ["sh", "-s", "--", str(stage), *(str(path) for path in files),
                         "verify", "boxa", "dmz"], input=script, text=True,
                        capture_output=True, check=False,
                    )
                    self.assertEqual(result.returncode, 42, result.stderr)
                    self.assertTrue(all(not path.exists() for path in files))
                    self.assertEqual(stage.exists(), extra)
                    if extra:
                        self.assertEqual((stage / "unexpected").read_text(), "keep")
                        self.assertIn("unexpected content", result.stderr)

            outside = Path(directory) / "outside"
            outside.mkdir()
            (outside / "desired.json").write_text("keep")
            result = subprocess.run(
                ["sh", "-s", "--", str(outside), str(outside / "desired.json"),
                 str(outside / "helper"), str(outside / "last-applied.json"),
                 "verify", "boxa", "dmz"], input=script, text=True,
                capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 64)
            self.assertEqual((outside / "desired.json").read_text(), "keep")

    def test_failed_podman_upload_requests_exact_remote_cleanup(self):
        failure = subprocess.CalledProcessError(1, "tailscale ssh")
        with patch.object(runtime, "upload_tailscale_ssh_text", side_effect=failure), \
                patch.object(runtime, "run_tailscale_ssh") as remote:
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.run_podman_resource_host(
                    "verify", "boxa-dmz", {"registry_sha256": "known"},
                    "{}", None, [], "verify-a1", repo_root=REPO_ROOT
                )
        self.assertEqual(remote.call_count, 1)
        arguments = remote.call_args.args[1]
        self.assertEqual(arguments[:4], [
            "sh", "-s", "--", "/home/neo/.cache/klokast-platform-resources/verify-a1"])
        self.assertEqual(arguments[7], "cleanup")

    def test_app_scoped_apply_skips_unrelated_app_vm_convergence(self):
        compiled = self.compiled_for_run()
        compiled["apps"] = {
            "static-site": {"boxes": ["boxa"]},
            "user-shell": {"boxes": ["boxa"]},
        }
        compiled["app_vm_specs"] = [
            {"app": "user-shell", "inventory_hostname": "boxa-usr-alice"}
        ]

        with patch.object(runtime.subprocess, "run") as run:
            runtime.run_ansible(
                "apply",
                compiled,
                "example.ts.net",
                "abc123",
                scope_apps=["static-site"], repo_root=REPO_ROOT
            )

        playbook_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0] and call.args[0][0] == "ansible-playbook"
        ]
        joined_calls = "\n".join(" ".join(map(str, call)) for call in playbook_calls)
        self.assertEqual(len(playbook_calls), 2)
        self.assertIn("31-vm-router.yml", joined_calls)
        self.assertIn("80-platform-resources.yml", joined_calls)
        self.assertNotIn("79-platform-app-vms.yml", joined_calls)
        self.assertNotIn("app-vms.yml", joined_calls)

    def test_torrent_compiles_dedicated_alpine_app_vm(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "apps": {
                    "torrent": {
                        "enabled": True,
                        "placement": {"active_master": "boxb"},
                        "app_vms": {
                            "torrent": {
                                "boxb": {"vm_ipv4_address": "192.168.200.30"}
                            }
                        },
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["torrent"], repo_root=REPO_ROOT)
        self.assertEqual(compiled["apps"]["torrent"]["boxes"], ["boxb"])
        specs = compiled["app_vm_specs"]
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["inventory_hostname"], "boxb-torrent")
        self.assertEqual(spec["node_domain_role"], "dmz")
        self.assertEqual(spec["advertised_tags"], ["tag:vm", "tag:torrent"])
        self.assertEqual(spec["guest_spec"]["guest_os"], "alpine")
        self.assertEqual(spec["guest_spec"]["container_runtime"], "none")
        self.assertEqual(spec["guest_spec"]["installed"]["required_lvs"], ["/dev/vg0/lv_torrent_torrent"])
        self.assertIn("address 192.168.200.30/", spec["guest_spec"]["network_interfaces"])

        egress_tcp = self.claims_for_comment(compiled, "app-torrent-vpn-egress-torrent-tcp")
        self.assertEqual(egress_tcp[0]["normalized"]["source"], "192.168.200.30")
        self.assertEqual(egress_tcp[0]["normalized"]["out_interface"], "eth0")
        bootstrap = self.claims_for_comment(
            compiled, "app-torrent-app-vm-bootstrap-ssh-torrent-dmz-router"
        )
        self.assertEqual(bootstrap[0]["normalized"]["destination"], "192.168.200.30")
        underlay_ops_to_app = self.claims_for_comment(
            compiled,
            "app-torrent-app-vm-tailscale-underlay-torrent-dmz-ops-to-app-router",
        )
        self.assertEqual(underlay_ops_to_app[0]["normalized"]["protocol"], "udp")
        self.assertEqual(underlay_ops_to_app[0]["normalized"]["source"], "192.168.125.10")
        self.assertEqual(underlay_ops_to_app[0]["normalized"]["destination"], "192.168.200.30")
        self.assertEqual(underlay_ops_to_app[0]["normalized"]["ports"], [41641])
        underlay_app_to_ops = self.claims_for_comment(
            compiled,
            "app-torrent-app-vm-tailscale-underlay-torrent-dmz-app-to-ops-router",
        )
        self.assertEqual(underlay_app_to_ops[0]["normalized"]["protocol"], "udp")
        self.assertEqual(underlay_app_to_ops[0]["normalized"]["source"], "192.168.200.30")
        self.assertEqual(underlay_app_to_ops[0]["normalized"]["destination"], "192.168.125.10")
        self.assertEqual(underlay_app_to_ops[0]["normalized"]["ports"], [41641])

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "app-vms.yml"
            runtime.render_app_vm_inventory(compiled, output, "example.ts.net")
            inventory = yaml.safe_load(output.read_text(encoding="utf-8"))
        hosts = inventory["all"]["hosts"]
        self.assertEqual(hosts["boxb-torrent"]["ansible_become_method"], "doas")
        self.assertEqual(hosts["boxb-torrent"]["platform_app_vm_guest_os"], "alpine")
        self.assertEqual(hosts["boxb-torrent"]["platform_app_vm_zone"], "dmz")
        self.assertEqual(hosts["boxb-torrent"]["platform_app_vm_interface"], "eth0")
        self.assertIn("vm_admin_authorized_key_file", hosts["boxb-torrent"])
        self.assertIn("vm_bootstrap_private_key_file", hosts["boxb-torrent"])
        self.assertIn("vm_bootstrap_known_hosts_file", hosts["boxb-torrent"])
        self.assertEqual(hosts["boxb-torrent"]["vm_local_users"][0]["name"], "neo")
        self.assertIn("boxb-torrent", inventory["all"]["children"]["torrent_app_vms"]["hosts"])
        self.assertIn("boxb-torrent", inventory["all"]["children"]["dmz_app_vms"]["hosts"])
        self.assertNotIn("dmz", inventory["all"]["children"])

    def test_household_vpn_compiles_dedicated_alpine_app_vm(self):
        path = self.write_registry(
            {
                "schema_version": 1,
                "boxes": {
                    "boxb": {
                        "access": {
                            "available_capabilities": [
                                "overlay",
                                "local-lan",
                                "vpn-egress",
                            ],
                            "enabled_capabilities": [
                                "overlay",
                                "local-lan",
                                "vpn-egress",
                            ],
                        }
                    }
                },
                "apps": {
                    "household-vpn": {
                        "enabled": True,
                        "placement": {"active_master": "boxb"},
                        "app_vms": {
                            "gateway": {
                                "boxb": {"vm_ipv4_address": "192.168.200.40"}
                            }
                        },
                    }
                },
            }
        )
        compiled = compiler.compile_registry(path, ["household-vpn"], repo_root=REPO_ROOT)
        self.assertEqual(compiled["apps"]["household-vpn"]["boxes"], ["boxb"])
        specs = compiled["app_vm_specs"]
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["inventory_hostname"], "boxb-household-vpn")
        self.assertEqual(spec["node_domain_role"], "dmz")
        self.assertEqual(spec["advertised_tags"], ["tag:vm", "tag:household-vpn"])
        self.assertEqual(spec["guest_spec"]["guest_os"], "alpine")
        self.assertEqual(spec["guest_spec"]["container_runtime"], "none")
        self.assertEqual(
            spec["guest_spec"]["installed"]["required_lvs"],
            ["/dev/vg0/lv_household_vpn_household_vpn"],
        )
        self.assertIn("address 192.168.200.40/", spec["guest_spec"]["network_interfaces"])

        egress_tcp = self.claims_for_comment(
            compiled, "app-household-vpn-vpn-egress-gateway-tcp"
        )
        self.assertEqual(egress_tcp[0]["normalized"]["source"], "192.168.200.40")
        self.assertEqual(egress_tcp[0]["normalized"]["out_interface"], "eth0")
        bootstrap = self.claims_for_comment(
            compiled, "app-household-vpn-app-vm-bootstrap-ssh-gateway-dmz-router"
        )
        self.assertEqual(bootstrap[0]["normalized"]["destination"], "192.168.200.40")

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "app-vms.yml"
            runtime.render_app_vm_inventory(compiled, output, "example.ts.net")
            inventory = yaml.safe_load(output.read_text(encoding="utf-8"))
        self.assertIn(
            "boxb-household-vpn",
            inventory["all"]["children"]["household_vpn_app_vms"]["hosts"],
        )
        self.assertIn(
            "boxb-household-vpn",
            inventory["all"]["children"]["dmz_app_vms"]["hosts"],
        )

    def test_tailscale_ssh_quotes_remote_command_arguments(self):
        calls = []
        original_run = runtime.subprocess.run

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))

        runtime.subprocess.run = fake_run
        try:
            runtime.run_tailscale_ssh(
                "boxb-bak.tail",
                ["sh", "-c", "umask 077 && cat > /tmp/a b"],
                input_text="payload", repo_root=REPO_ROOT
            )
        finally:
            runtime.subprocess.run = original_run

        self.assertEqual(
            calls[0][0],
            [
                "tailscale",
                "ssh",
                "neo@boxb-bak.tail",
                "sh -c 'umask 077 && cat > /tmp/a b'",
            ],
        )
        self.assertEqual(calls[0][1]["input"], "payload")

    def test_platform_resources_inventory_does_not_override_dom0_remote_tmp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "boxa.yml"
            original_run = runtime.subprocess.run

            def fake_run(argv, **_kwargs):
                rendered_output = Path(argv[argv.index("--output") + 1])
                rendered_output.write_text(
                    """---
all:
  children:
    k001_dom0:
      hosts:
        boxa-dom0:
          node_name: boxa
""",
                    encoding="utf-8",
                )

            runtime.subprocess.run = fake_run
            try:
                runtime.render_inventory("boxa", output, "example.ts.net", repo_root=REPO_ROOT)
            finally:
                runtime.subprocess.run = original_run

            inventory = yaml.safe_load(output.read_text(encoding="utf-8"))

        self.assertNotIn(
            "ansible_remote_tmp",
            inventory["all"]["children"]["k001_dom0"]["hosts"]["boxa-dom0"],
        )

    def test_platform_resources_inventory_passes_dom0_bridge_ports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "boxb.yml"
            calls = []
            original_run = runtime.subprocess.run

            def fake_run(argv, **_kwargs):
                calls.append(argv)
                rendered_output = Path(argv[argv.index("--output") + 1])
                rendered_output.write_text("---\nall: {}\n", encoding="utf-8")

            runtime.subprocess.run = fake_run
            try:
                runtime.render_inventory(
                    "boxb",
                    output,
                    "example.ts.net",
                    {"boxb": {"dom0_bridge_ports": {"iot": ["eth3"]}}}, repo_root=REPO_ROOT
                )
            finally:
                runtime.subprocess.run = original_run

        self.assertIn("--dom0-bridge-port", calls[0])
        index = calls[0].index("--dom0-bridge-port")
        self.assertEqual(calls[0][index + 1], "iot=eth3")

    def test_platform_resources_vars_marks_desired_json_unsafe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "extra-vars.yml"
            runtime.write_platform_resources_vars(
                output,
                {"plain": "value"},
                '{"config_path": "{{ xen_guest_config_dir }}/usr-alice.cfg"}',
            )
            text = output.read_text(encoding="utf-8")

        self.assertIn("plain: value\n", text)
        self.assertIn("platform_resources_desired_json: !unsafe |-\n", text)
        self.assertIn('  {"config_path": "{{ xen_guest_config_dir }}/usr-alice.cfg"}\n', text)

    def test_app_scoped_reconcile_mutates_only_selected_resource_key_files(self):
        reconciler = load_reconcile_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "app-resources"
            self.configure_reconciler_root(reconciler, root)
            selected_rule = {
                "node": "boxa",
                "app": "selected",
                "resource": "web",
                "in_interface": "eth2",
                "out_interface": "eth3",
                "source": "192.168.200.10",
                "destination": "192.168.100.10",
                "protocol": "tcp",
                "ports": [8080],
                "comment": "selected-web",
            }
            other_rule = dict(selected_rule)
            other_rule.update(
                {
                    "app": "other",
                    "resource": "admin",
                    "ports": [9443],
                    "comment": "other-admin",
                }
            )
            desired = self.desired_for_rules([selected_rule, other_rule])
            args = argparse.Namespace(
                scope_app=[],
                node_name="boxa",
                node_role="router",
            )
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired, args)
            other_files = sorted((root / "router-forward.d").glob("*.nft"))
            other_content_before = {
                path.name: path.read_text(encoding="utf-8") for path in other_files
            }

            selected_rule_changed = dict(selected_rule)
            selected_rule_changed["ports"] = [8081]
            desired_changed = self.desired_for_rules([selected_rule_changed, other_rule])
            args.scope_app = ["selected"]
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired_changed, args)

            other_after = {
                path.name: path.read_text(encoding="utf-8")
                for path in (root / "router-forward.d").glob("*.nft")
                if "other" in path.read_text(encoding="utf-8")
            }
            self.assertEqual(
                {
                    name: content
                    for name, content in other_content_before.items()
                    if "other" in content
                },
                other_after,
            )
            rendered = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / "router-forward.d").glob("*.nft")
            )
            self.assertIn("8081", rendered)
            self.assertNotIn(" 8080 accept", rendered)

    def test_full_and_app_scoped_apply_converge_to_same_snippets(self):
        reconciler = load_reconcile_module()
        rule_a = {
            "node": "boxa",
            "app": "app-a",
            "resource": "web",
            "in_interface": "eth2",
            "out_interface": "eth3",
            "source": "192.168.200.10",
            "destination": "192.168.100.10",
            "protocol": "tcp",
            "ports": [8080],
            "comment": "app-a-web",
        }
        rule_b = dict(rule_a)
        rule_b.update({"app": "app-b", "ports": [9443], "comment": "app-b-web"})
        desired = self.desired_for_rules([rule_a, rule_b])
        with tempfile.TemporaryDirectory() as full_tmp, tempfile.TemporaryDirectory() as scoped_tmp:
            full_root = Path(full_tmp) / "app-resources"
            scoped_root = Path(scoped_tmp) / "app-resources"
            args = argparse.Namespace(scope_app=[], node_name="boxa", node_role="router")

            self.configure_reconciler_root(reconciler, full_root)
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired, args)
            full_files = {
                path.name: path.read_text(encoding="utf-8")
                for path in (full_root / "router-forward.d").glob("*.nft")
            }

            self.configure_reconciler_root(reconciler, scoped_root)
            args.scope_app = ["app-a"]
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired, args)
            args.scope_app = ["app-b"]
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired, args)
            scoped_files = {
                path.name: path.read_text(encoding="utf-8")
                for path in (scoped_root / "router-forward.d").glob("*.nft")
            }

            self.assertEqual(full_files, scoped_files)

    def test_scoped_verification_checks_only_selected_live_rules(self):
        reconciler = load_reconcile_module()
        selected = {
            'node': 'boxa', 'app': 'platform', 'resource': 'runner-web',
            'in_interface': 'eth5', 'out_interface': 'eth0',
            'source': '192.168.175.11', 'destination': '',
            'protocol': 'tcp', 'ports': [443], 'comment': 'runner-web',
        }
        other = dict(selected, app='other', resource='other-web', ports=[8443])
        desired = self.desired_for_rules([selected, other])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.configure_reconciler_root(reconciler, root / 'resources')
            metadata = root / 'metadata'; metadata.mkdir()
            for name in ('desired.json', 'last-applied.json'):
                (metadata / name).write_text(json.dumps(desired))
            nft = root / 'nft'; nft.touch()
            args = argparse.Namespace(scope_app=['platform'], node_name='boxa',
                                      node_role='router', metadata_root=metadata, nft=nft)
            with redirect_stdout(io.StringIO()):
                reconciler.apply_resources(desired, args)
            identity = next(item['rendered_rule_identity'] for item in
                            desired['app_resource_effective_files'] if item['owners'] == ['platform'])
            with patch.object(reconciler.subprocess, 'run', return_value=
                              subprocess.CompletedProcess([], 0, identity, '')), redirect_stdout(io.StringIO()):
                reconciler.verify_resources(desired, args)
                args.scope_app = []
                with self.assertRaises(SystemExit):
                    reconciler.verify_resources(desired, args)
                args.scope_app = ['platform']
            with patch.object(reconciler.subprocess, 'run', return_value=
                              subprocess.CompletedProcess([], 0, '', '')), redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit):
                reconciler.verify_resources(desired, args)

    def test_box_access_runs_only_one_router_playbook(self):
        compiled = {
            "compiler_version": model.COMPILER_VERSION,
            "registry_sha256": "a" * 64,
            "managed_iot_devices": [],
            "box_configs": {
                "boxa": {
                    "access": model.default_box_access(),
                    "dhcp_reservations": [],
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".run" / "platform-resources").mkdir(parents=True)
            with patch.object(
                runtime, "render_inventory"
            ), patch.object(runtime.subprocess, "run") as runner:
                runtime.run_box_access(
                    "apply", compiled, "boxa", "example.ts.net", "b" * 40, repo_root=root
                )
        command = runner.call_args.args[0]
        self.assertIn(str(root / "ansible" / "playbooks" / "32-platform-box-access.yml"), command)
        self.assertEqual(command[command.index("--limit") + 1], "boxa-router")
        joined = " ".join(command)
        self.assertNotIn("platform-resources.yml", joined)
        self.assertNotIn("shared-guests", joined)
        self.assertNotIn("dom0", joined)

    def test_box_access_check_mode_is_limited_to_the_same_router_playbook(self):
        compiled = {
            "compiler_version": model.COMPILER_VERSION,
            "registry_sha256": "a" * 64,
            "managed_iot_devices": [],
            "box_configs": {
                "boxb": {
                    "access": model.default_box_access(),
                    "dhcp_reservations": [],
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".run" / "platform-resources").mkdir(parents=True)
            with patch.object(
                runtime, "render_inventory"
            ), patch.object(runtime.subprocess, "run") as runner:
                runtime.run_box_access(
                    "apply", compiled, "boxb", "example.ts.net", "b" * 40,
                    check_mode=True, repo_root=root,
                )
        command = runner.call_args.args[0]
        self.assertIn("--check", command)
        self.assertEqual(command[command.index("--limit") + 1], "boxb-router")

    def test_box_access_check_mode_runs_read_only_verification(self):
        playbook = yaml.safe_load(
            (REPO_ROOT / "ansible" / "playbooks" / "32-platform-box-access.yml").read_text(
                encoding="utf-8"
            )
        )
        tasks = playbook[0]["tasks"]
        verification = next(
            task
            for task in tasks
            if task.get("name") == "Verify the selected router configuration and declared paths"
        )
        controller_ping = next(
            task
            for task in tasks
            if task.get("name") == "Check controller reachability to the selected router"
        )
        self.assertIs(verification["check_mode"], False)
        self.assertIs(controller_ping["check_mode"], False)

    def test_box_access_probe_accepts_direct_and_derp_but_not_unknown_replies(self):
        play = yaml.safe_load((REPO_ROOT / "ansible/playbooks/32-platform-box-access.yml").read_text())[0]
        tasks = play["tasks"]
        probe = next(task for task in tasks if task.get("register") == "platform_box_access_controller_ping")
        self.assertEqual(probe["ansible.builtin.command"]["argv"], [
            "tailscale", "ping", "--c", "1", "--until-direct=false", "--timeout=5s", "{{ ansible_host }}",
        ])
        self.assertNotIn("failed_when", probe)
        self.assertNotIn("ignore_errors", probe)
        self.assertEqual(probe["delegate_to"], "localhost")
        validation = next(task for task in tasks if task["name"] == "Require a recognized reply from the selected router")
        notice = tasks[-1]
        self.assertEqual(validation["ansible.builtin.assert"]["that"], [
            "platform_box_access_controller_ping.stdout_lines | length == 1",
            "platform_box_access_controller_ping.stdout is match(platform_box_access_reply_pattern)",
        ])
        pattern = play["vars"]["platform_box_access_reply_pattern"].replace("{{ platform_box_access_router_hostname | regex_escape }}", re.escape("boxa-router")).replace("{{ platform_magicdns_suffix | regex_escape }}", re.escape("example.ts.net"))
        self.assertNotIn("{{", pattern)
        self.assertEqual(notice["when"], "'via DERP(' in platform_box_access_controller_ping.stdout")
        def accepted(output):
            return len(output.splitlines()) == 1 and re.match(pattern, output) is not None
        for endpoint in ("DERP(nue)", "192.0.2.1:41641", "[2001:db8::1]:41641"):
            output = f"pong from boxa-router (100.64.0.1) via {endpoint} in 291ms"
            with self.subTest(endpoint=endpoint):
                self.assertTrue(accepted(output))
                self.assertEqual("via DERP(" in output, endpoint.startswith("DERP"))
        valid = "pong from boxa-router (100.64.0.1) via DERP(nue) in 291ms"
        self.assertTrue(accepted(valid.replace("boxa-router", "boxa-router.example.ts.net")))
        for output in ("", "ping timed out", "100.64.0.1 is local Tailscale IP", valid.replace("boxa-router", "boxb-router"), valid.replace("DERP(nue)", "unknown"), valid + "\n" + valid, valid + " unexpected"):
            with self.subTest(output=output):
                self.assertFalse(accepted(output))
        role = next(task for task in tasks if task.get("ansible.builtin.import_role", {}).get("name") == "router-verification")
        self.assertLess(tasks.index(role), tasks.index(probe))
        self.assertNotIn("ignore_errors", role)

    def test_explicit_ipv6_repair_keeps_its_no_derp_requirement(self):
        for name in ("83-overlay-ipv6-router.yml", "84-overlay-ipv6-ops.yml"):
            source = (REPO_ROOT / "ansible/playbooks" / name).read_text()
            self.assertIn("--until-direct", source)
            self.assertIn("41641", source)


if __name__ == "__main__":
    unittest.main()
