#!/usr/bin/env python3
import unittest
import importlib.util
import tempfile
import os
import subprocess
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml


ROOT = Path(__file__).resolve().parents[2]
ROUTER_VARS = (ROOT / "ansible/inventory/group_vars/router.yml").read_text(encoding="utf-8")
ROUTER_PLAY = (ROOT / "ansible/playbooks/83-overlay-ipv6-router.yml").read_text(encoding="utf-8")
OPS_PLAY = (ROOT / "ansible/playbooks/84-overlay-ipv6-ops.yml").read_text(encoding="utf-8")
ROUTER_NFT = (ROOT / "ansible/roles/router/templates/nftables.nft.j2").read_text(encoding="utf-8")


class OverlayIPv6RoleTest(unittest.TestCase):
    def run_collision_probe(self, before="", after="", *, ping_rc=1, ip_rc=0, repeat=False):
        play = yaml.safe_load(ROUTER_PLAY)[0]
        task = next(t for t in play["tasks"] if t["name"] == "Check the stable WAN next hop is not in use")
        script = task["ansible.builtin.shell"].replace(
            "{{ overlay_ipv6_next_hop | quote }}", "'fe80::1234'"
        ).replace("{{ router_wan_interface }}", "eth0")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ip = root / "ip"
            ip.write_text("""#!/bin/sh
case "$*" in
  *'address show'*) exit 0 ;;
  *'neigh show'*)
    [ "$PROBE_IP_RC" = 0 ] || exit "$PROBE_IP_RC"
    if [ -f "$PROBE_MARKER" ]; then
      printf '%s\\n' "$PROBE_AFTER"
    else
      printf '%s\\n' "$PROBE_BEFORE"
    fi ;;
  *) exit 2 ;;
esac
""")
            ping = root / "ping"
            ping.write_text("#!/bin/sh\n: >\"$PROBE_MARKER\"\nexit \"$PROBE_PING_RC\"\n")
            ip.chmod(0o700)
            ping.chmod(0o700)
            env = {
                **os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "PROBE_MARKER": str(root / "probed"), "PROBE_BEFORE": before,
                "PROBE_AFTER": after, "PROBE_PING_RC": str(ping_rc), "PROBE_IP_RC": str(ip_rc),
            }
            results = [subprocess.run(["/bin/sh", "-s"], input=script, text=True, capture_output=True, env=env)]
            if repeat:
                results.append(subprocess.run(["/bin/sh", "-s"], input=script, text=True, capture_output=True, env=env))
            return results

    def test_failed_collision_probe_does_not_poison_repeated_preflight(self):
        for state in ("FAILED", "INCOMPLETE"):
            with self.subTest(state=state):
                results = self.run_collision_probe(after=f"fe80::1234 {state}", repeat=True)
                for result in results:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "unused")

    def test_collision_probe_refuses_known_neighbours_and_inspection_failures(self):
        for state in ("REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT", "FAILED", "UNKNOWN"):
            entry = f"fe80::1234 lladdr 00:11:22:33:44:55 {state}"
            for stage in ("before", "after"):
                with self.subTest(state=state, stage=stage):
                    result = self.run_collision_probe(**{stage: entry})[0]
                    self.assertNotEqual(result.returncode, 0)
        for options in ({"ping_rc": 0}, {"ip_rc": 2}, {"before": "fe80::1234 UNKNOWN"}):
            with self.subTest(options=options):
                self.assertNotEqual(self.run_collision_probe(**options)[0].returncode, 0)

    def load_ops_helper(self):
        loader = SourceFileLoader("overlay_ops_test", str(ROOT / "ansible/bin/overlay-ipv6-ops"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def test_ops_helper_uses_local_connection_only_on_selected_host(self):
        module = self.load_ops_helper()
        args = SimpleNamespace(box="boxa", magicdns_suffix="example.ts.net", check=True)
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[0] == "ansible-playbook":
                variables = yaml.safe_load(Path(command[command.index("-e") + 1][1:]).read_text())
                self.assertEqual(variables["ansible_connection"], "local")
                self.assertEqual(variables["ansible_host"], "localhost")
                self.assertEqual(variables["ansible_python_interpreter"], "/usr/bin/python3")
                self.assertEqual(command[command.index("--limit") + 1], "boxa-ops")
                self.assertIn("--check", command)
            return Mock(returncode=0)
        with tempfile.TemporaryDirectory() as temporary, patch.object(module, "REPO_ROOT", Path(temporary)), patch.object(
            module.socket, "gethostname", return_value="boxa-ops.example.ts.net"
        ), patch.object(module.subprocess, "run", side_effect=run):
            module.run_playbook(args, {"overlay_ipv6_ops_operation": "snapshot"})
        self.assertEqual(len(calls), 2)
        with patch.object(module.socket, "gethostname", return_value="boxb-ops"), patch.object(
            module.subprocess, "run"
        ) as run, self.assertRaisesRegex(module.HelperError, "selected controller host"):
            module.run_playbook(args, {})
        run.assert_not_called()

    def test_default_remains_ipv4_only(self):
        self.assertIn("router_enable_ipv6_downstream: false", ROUTER_VARS)
        self.assertIn("router_enable_ops_ipv6_downstream: false", ROUTER_VARS)
        self.assertIn('include "/etc/klokast/overlay-ipv6.nft"', ROUTER_NFT)
        self.assertNotIn("enable-ra", ROUTER_NFT)

    def test_router_scope_is_one_ops_prefix_only(self):
        self.assertIn("constructor:{{ router_ops_interface }},ra-only,64", ROUTER_PLAY)
        self.assertIn("net.ipv6.conf.all.forwarding = 1", ROUTER_PLAY)
        self.assertIn("accept_ra = 2", ROUTER_PLAY)
        self.assertIn("ops-ipv6-tailscale-source-egress", ROUTER_PLAY)
        self.assertIn("udp sport 41641", ROUTER_PLAY)
        self.assertIn("udp dport 3478", ROUTER_PLAY)
        self.assertIn("meta l4proto ipv6-icmp", ROUTER_PLAY)
        self.assertNotIn("router_dmz_interface }} inet6", ROUTER_PLAY)
        self.assertNotIn("router_backend_interface }} inet6", ROUTER_PLAY)
        self.assertIn("Check the stable WAN next hop is not in use", ROUTER_PLAY)

    def test_ops_slaac_preserves_ipv4_and_requires_direct_ipv6(self):
        self.assertIn("Persist ops SLAAC kernel settings", OPS_PLAY)
        self.assertNotIn("blockinfile", OPS_PLAY)
        self.assertNotIn("service:\n        name: networking", OPS_PLAY)
        self.assertIn("ip -4 route show default", OPS_PLAY)
        self.assertIn("IPv6:[[:space:]]+yes", OPS_PLAY)
        self.assertIn("via \\[[0-9A-Fa-f:]+\\]:41641", OPS_PLAY)
        self.assertIn("ops-native-ipv6-tailscale-input", OPS_PLAY)

    def test_rollback_covers_files_firewall_and_runtime(self):
        self.assertIn("klokast.overlay-ipv6-router-preimage.v1", ROUTER_PLAY)
        self.assertIn("Restore exact router firewall runtime state", ROUTER_PLAY)
        self.assertIn("klokast.overlay-ipv6-ops-preimage.v1", OPS_PLAY)
        self.assertIn("Restore exact ops firewall runtime state", OPS_PLAY)
        self.assertIn("verify-recovery", ROUTER_PLAY)
        self.assertIn("verify-recovery", OPS_PLAY)


if __name__ == "__main__":
    unittest.main()
