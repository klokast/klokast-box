#!/usr/bin/env python3
import unittest
import importlib.util
import tempfile
import os
import re
import shutil
import shlex
import subprocess
from contextlib import ExitStack
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
try:
    from jinja2 import Environment
except ImportError:
    Environment = None


ROOT = Path(__file__).resolve().parents[2]
ROUTER_VARS = (ROOT / "ansible/inventory/group_vars/router.yml").read_text(encoding="utf-8")
ROUTER_PLAY = (ROOT / "ansible/playbooks/83-overlay-ipv6-router.yml").read_text(encoding="utf-8")
OPS_PLAY = (ROOT / "ansible/playbooks/84-overlay-ipv6-ops.yml").read_text(encoding="utf-8")
ROUTER_NFT = (ROOT / "ansible/roles/router/templates/nftables.nft.j2").read_text(encoding="utf-8")


class OverlayIPv6RoleTest(unittest.TestCase):
    def run_collision_probe(self, before="", after="", *, local_addresses="", ping_rc=1, ip_rc=0, repeat=False):
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
  *'address show'*) printf '%s\n' "$PROBE_LOCAL_ADDRESSES" ;;
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
                "PROBE_LOCAL_ADDRESSES": local_addresses,
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

    def test_existing_next_hop_is_reused_only_with_exact_prefix(self):
        address = "4: eth0    inet6 fe80::1234/64 scope link"
        result = self.run_collision_probe(local_addresses=address)[0]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "local")
        address = "4: eth0    inet6 fe80::1234/128 scope link"
        result = self.run_collision_probe(local_addresses=address)[0]
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected local prefix", result.stderr)
        tasks = yaml.safe_load(ROUTER_PLAY)[0]["tasks"]
        add = next(t for t in tasks if t["name"] == "Add the stable WAN next hop without restarting IPv4")
        self.assertIn("overlay_ipv6_next_hop_collision.stdout | trim == 'unused'", add["when"])

    def test_existing_ops_router_address_is_reused_only_with_exact_prefix(self):
        tasks = yaml.safe_load(ROUTER_PLAY)[0]["tasks"]
        check = next(t for t in tasks if t["name"] == "Check the ops delegated router address")
        add = next(t for t in tasks if t["name"] == "Add the ops delegated router address without restarting IPv4")
        self.assertIn("overlay_ipv6_ops_router_address.stdout | trim == 'unused'", add["when"])
        script = check["ansible.builtin.shell"].replace(
            "{{ ((overlay_ipv6_prefix | regex_replace('/64$', '')) ~ '1') | quote }}",
            "'2001:db8:1234:1::1'",
        ).replace("{{ router_ops_interface }}", "eth6")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ip = root / "ip"
            ip.write_text('#!/bin/sh\nprintf "%s\\n" "$CURRENT_ADDRESSES"\n')
            ip.chmod(0o700)
            for address, expected, succeeds in (
                ("8: eth6 inet6 2001:db8:1234:1::1/64 scope global", "local", True),
                ("8: eth6 inet6 2001:db8:1234:1::1/128 scope global", "unexpected local prefix", False),
                ("8: eth6 inet6 fe80::1/64 scope link", "unused", True),
            ):
                with self.subTest(address=address):
                    result = subprocess.run(
                        ["/bin/sh", "-s"], input=script, text=True, capture_output=True,
                        env={**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                             "CURRENT_ADDRESSES": address},
                    )
                    self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                    self.assertIn(expected, result.stdout if succeeds else result.stderr)

    def load_ops_helper(self, role="ops"):
        loader = SourceFileLoader(f"overlay_{role}_test", str(ROOT / f"ansible/bin/overlay-ipv6-{role}"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def test_ops_helper_uses_local_connection_only_on_selected_host(self):
        module = self.load_ops_helper()
        args = SimpleNamespace(box="boxa", magicdns_suffix="example.ts.net", check=True, command="snapshot")
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
            return Mock(returncode=0, stdout="", stderr="")
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

    def test_helpers_keep_private_logs_and_report_failed_task(self):
        for role in ("ops", "router"):
            for returncode in (0, 2):
                with self.subTest(role=role, returncode=returncode):
                    module = self.load_ops_helper(role)
                    args = SimpleNamespace(box="boxa", peer_box=None, magicdns_suffix="example.ts.net", check=False, command="apply")
                    stdout = (
                        "TASK [Wait for one SLAAC address] ********\n"
                        "fatal: [boxa-router]: FAILED! => private diagnostic\n"
                        "TASK [Later skipped task] ********\n"
                        "skipping: [boxa-router]\n"
                        "PLAY RECAP ********\nfailed=1\n"
                    )
                    stderr = "private stderr\n"
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        patches = [patch.object(module, "REPO_ROOT", root), patch.object(module.subprocess, "run", side_effect=[
                            Mock(returncode=0), Mock(returncode=returncode, stdout=stdout, stderr=stderr),
                        ])]
                        if role == "ops":
                            patches.append(patch.object(module.socket, "gethostname", return_value="boxa-ops"))
                        with ExitStack() as stack:
                            for item in patches:
                                stack.enter_context(item)
                            arguments = (args, {}) if role == "ops" else (args, {}, "boxa-router")
                            if returncode:
                                with self.assertRaises(module.HelperError) as failure:
                                    module.run_playbook(*arguments)
                                message = str(failure.exception)
                                self.assertIn("Wait for one SLAAC address", message)
                                self.assertNotIn("Later skipped task", message)
                                self.assertNotIn("private diagnostic", message)
                                self.assertNotIn("private stderr", message)
                                self.assertEqual(len(message.splitlines()), 1)
                            else:
                                module.run_playbook(*arguments)
                        run_dir = root / ".run" / f"overlay-ipv6-{role}"
                        logs = list(run_dir.glob("apply-*.log"))
                        self.assertEqual(len(logs), 1)
                        self.assertEqual(logs[0].read_text(), stdout + "\n" + stderr)
                        self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)
                        self.assertEqual(run_dir.stat().st_mode & 0o777, 0o700)
                        self.assertEqual(list(run_dir.glob("run-*")), [])

    def test_router_loads_exact_advertisement_inside_rollback_scope(self):
        play = yaml.safe_load(ROUTER_PLAY)[0]
        tasks = play["tasks"]
        task = next(t for t in tasks if t["name"] == "Load the exact ops advertisement file from the dnsmasq entry point")
        line = task["ansible.builtin.lineinfile"]
        self.assertEqual(line["path"], "/etc/dnsmasq.conf")
        self.assertIn(line["path"], play["vars"]["overlay_ipv6_router_files"])
        self.assertEqual(line["line"], "conf-file=/etc/dnsmasq.d/91-klokast-ops-ipv6.conf")
        self.assertIn(line["line"].split("=", 1)[1], play["vars"]["overlay_ipv6_router_files"])
        self.assertIn("--test --conf-file=%s", line["validate"])
        self.assertIn("overlay_ipv6_router_operation == 'apply'", task["when"])
        self.assertIn("overlay_ipv6_dnsmasq_load_path.stdout == 'missing'", task["when"])
        names = [t["name"] for t in tasks]
        self.assertLess(names.index("Advertise only the delegated ops prefix"), names.index(task["name"]))
        self.assertLess(names.index(task["name"]), names.index("Restart dnsmasq to start ops router advertisements"))

    def test_router_advertisement_load_path_rejects_duplicates(self):
        tasks = yaml.safe_load(ROUTER_PLAY)[0]["tasks"]
        task = next(t for t in tasks if t["name"] == "Classify the exact dnsmasq advertisement load path")
        script = task["ansible.builtin.shell"]
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "dnsmasq.conf"
            script = script.replace("/etc/dnsmasq.conf", str(config))
            cases = (
                ("conf-dir=/etc/dnsmasq.d,*.conf\n", "managed-dir", 0),
                ("conf-file=/etc/dnsmasq.d/91-klokast-ops-ipv6.conf\n", "explicit", 0),
                ("domain-needed\n", "missing", 0),
                ("conf-dir=/etc/dnsmasq.d,*.conf\nconf-file=/etc/dnsmasq.d/91-klokast-ops-ipv6.conf\n", "", 1),
                ("conf-dir=/etc/dnsmasq.d\n", "", 1),
            )
            for content, expected, failure in cases:
                with self.subTest(content=content):
                    config.write_text(content)
                    result = subprocess.run(["/bin/sh", "-s"], input=script, text=True,
                                            capture_output=True)
                    self.assertEqual(result.returncode != 0, bool(failure), result.stderr)
                    if not failure:
                        self.assertEqual(result.stdout.strip(), expected)

    def test_router_loads_firewall_fragment_inside_rollback_scope(self):
        play = yaml.safe_load(ROUTER_PLAY)[0]
        tasks = play["tasks"]
        install = next(t for t in tasks if t["name"] == "Install the narrow ops-only IPv6 forwarding rules")
        content = install["ansible.builtin.copy"]["content"]
        self.assertEqual(content.count('comment "ops-tailscale-wan-ipv4-suppression"'), 1)
        self.assertLess(content.index('ops-tailscale-wan-ipv4-suppression'),
                        content.index('ops-ipv6-tailscale-source-egress'))
        retire = next(t for t in tasks if t["name"] == "Remove the legacy inline ops Tailscale IPv4 suppression rule")
        self.assertEqual(retire["ansible.builtin.lineinfile"]["state"], "absent")
        task = next(t for t in tasks if t["name"] == "Load the exact IPv6 rules file from the router forward chain")
        line = task["ansible.builtin.lineinfile"]
        self.assertIn(line["path"], play["vars"]["overlay_ipv6_router_files"])
        self.assertEqual(line["line"].strip(), 'include "/etc/klokast/overlay-ipv6.nft"')
        self.assertEqual(line["validate"], '/usr/sbin/nft -c -f %s')
        names = [t["name"] for t in tasks]
        self.assertLess(names.index("Install the narrow ops-only IPv6 forwarding rules"), names.index(task["name"]))
        self.assertLess(names.index(task["name"]), names.index("Load the prepared IPv6 firewall rules"))
        self.assertLess(names.index("Restart dnsmasq after exact router restoration"), names.index("Restore router runtime sysctl and managed addresses"))

    @unittest.skipIf(Environment is None, "Jinja2 is available on the controller")
    def test_recovery_removes_expanded_slaac_but_preserves_preimage_addresses(self):
        environment = Environment()
        environment.filters['quote'] = shlex.quote
        for role, source, name in (("router", ROUTER_PLAY, "Restore router runtime sysctl and managed addresses"),
                                   ("ops", OPS_PLAY, "Restore ops runtime sysctl and delegated addresses")):
            task = next(t for t in yaml.safe_load(source)[0]['tasks'] if t['name'] == name)
            interface = 'eth6' if role == 'router' else 'eth0'
            old = '2001:db8:1234:1::88/64'
            added = '2001:db8:1234:1:216:3eff:fe71:6001/64'
            runtime = f'forwarding=0\naccept_ra=1\nautoconf=1\n8: {interface} inet6 {old} scope global'
            variables = {'overlay_ipv6_prefix': '2001:db8:1234:1::/64', 'overlay_ipv6_next_hop': 'fe80::1234',
                         'router_wan_interface': 'eth0', 'router_ops_interface': 'eth6',
                         'platform_control_zones': {'ops': {'vm_interface': 'eth0'}},
                         f'overlay_ipv6_{role}_preimage': {'runtime': runtime}}
            script = environment.from_string(task['ansible.builtin.shell']).render(**variables)
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root/'ip').write_text('''#!/bin/sh
case "$*" in
  *'to fe80::1234/128'*) printf '2: eth0 inet6 fe80::1234/64 scope link\\n' ;;
  *'to 2001:db8:1234:1::/64'*) printf '%s\\n' "$CURRENT_ADDRESSES" ;;
  '-6 address del '*) printf '%s\\n' "$*" >>"$DELETIONS" ;;
  *) exit 2 ;;
esac
''')
                (root/'sysctl').write_text('#!/bin/sh\nexit 0\n')
                for path in (root/'ip', root/'sysctl'): path.chmod(0o700)
                current = f'8: {interface} inet6 {old} scope global\n8: {interface} inet6 {added} scope global deprecated'
                result = subprocess.run(['/bin/sh', '-s'], input=script, text=True, capture_output=True,
                                        env={**os.environ, 'PATH': str(root)+os.pathsep+os.environ['PATH'],
                                             'CURRENT_ADDRESSES': current, 'DELETIONS': str(root/'deleted')})
                self.assertEqual(result.returncode, 0, result.stderr)
                deletions = (root/'deleted').read_text().splitlines()
                self.assertIn(f'-6 address del {added} dev {interface}', deletions)
                self.assertFalse(any(old in line for line in deletions))
                self.assertEqual(len(deletions), 2 if role == 'router' else 1)

    def test_recovery_ping_accepts_derp_and_refuses_unreachable_peer(self):
        for source in (ROUTER_PLAY, OPS_PLAY):
            task = next(t for t in yaml.safe_load(source)[0]['tasks'] if t['name'].startswith('Verify ') and 'recovery' in t['name'])
            command = next(line.strip() for line in task['ansible.builtin.shell'].splitlines() if line.strip().startswith('tailscale ping'))
            command = re.sub(r'{{.*?}}', 'peer', command)
            stub = '''set -eu
tailscale() {
  case "$*" in *--until-direct=false*) ;; *) return 1 ;; esac
  [ "$PEER_REACHABLE" = yes ] || return 1
  echo 'pong from peer (100.64.0.2) via DERP(nue) in 200ms'
}
'''
            for reachable in ('yes', 'no'):
                result = subprocess.run(['/bin/sh','-s'],input=stub+command+'\n',text=True,capture_output=True,
                                        env={**os.environ,'PEER_REACHABLE':reachable})
                self.assertEqual(result.returncode == 0, reachable == 'yes')

    @unittest.skipIf(Environment is None, "Jinja2 is available on the controller")
    def test_router_prerequisite_selects_exact_ipv6_reply_within_bounded_probe(self):
        tasks = yaml.safe_load(ROUTER_PLAY)[0]["tasks"]
        ping = next(t for t in tasks if t["name"] == "Prove the active router reaches the peer directly over IPv6")
        self.assertEqual(ping["ansible.builtin.command"]["argv"][2:5], ["--until-direct=false", "--c", "10"])
        select = next(t for t in tasks if t["name"] == "Select exact direct IPv6 replies from the peer router")
        environment = Environment()
        environment.tests["search"] = lambda value, pattern: re.search(pattern, value) is not None
        environment.filters["regex_escape"] = re.escape
        template = environment.from_string(select["ansible.builtin.set_fact"]["overlay_ipv6_router_direct_ipv6_pings"])
        direct = "pong from peer (100.64.0.2) via [2001:db8::2]:41641 in 291ms"
        other = ["pong from peer (100.64.0.2) via DERP(hkg) in 450ms",
                 "pong from peer (100.64.0.2) via 192.0.2.1:41641 in 230ms",
                 "pong from peer (100.64.0.2) via [2001:db8::3]:41641 in 300ms"]
        variables = {"overlay_ipv6_peer_box": "boxb", "hostvars": {"boxb-router": {
            "overlay_ipv6_peer_global": {"stdout": "2001:db8::2"}}}}
        for lines, expected in ((other[:1] + [direct] + other[1:], [direct]), (other, [])):
            with self.subTest(lines=lines):
                rendered = template.render(**variables, overlay_ipv6_router_direct_ping={"stdout_lines": lines})
                self.assertEqual(yaml.safe_load(rendered), expected)

    @unittest.skipUnless(shutil.which("ansible-playbook"), "Ansible is available on the controller")
    def test_router_prerequisite_selects_exact_ipv6_reply_in_ansible(self):
        tasks = yaml.safe_load(ROUTER_PLAY)[0]["tasks"]
        selection = next(t for t in tasks if t["name"] == "Select exact direct IPv6 replies from the peer router")
        expression = selection["ansible.builtin.set_fact"]["overlay_ipv6_router_direct_ipv6_pings"].replace(
            "hostvars[overlay_ipv6_peer_box ~ '-router'].overlay_ipv6_peer_global.stdout", "peer_global"
        )
        direct = "pong from peer (100.64.0.2) via [2001:db8::2]:41641 in 291ms"
        other = "pong from peer (100.64.0.2) via 192.0.2.1:41641 in 230ms"
        play = [{"hosts": "localhost", "connection": "local", "gather_facts": False,
                 "vars": {"peer_global": "2001:db8::2", "overlay_ipv6_router_direct_ping": {
                     "stdout_lines": [other, direct, other]}},
                 "tasks": [
                     {"ansible.builtin.set_fact": {"overlay_ipv6_router_direct_ipv6_pings": expression}},
                     {"ansible.builtin.assert": {"that": ["overlay_ipv6_router_direct_ipv6_pings == [" + repr(direct) + "]"]}},
                     {"ansible.builtin.set_fact": {"overlay_ipv6_router_direct_ping": {"stdout_lines": [other]}}},
                     {"ansible.builtin.set_fact": {"overlay_ipv6_router_direct_ipv6_pings": expression}},
                     {"ansible.builtin.assert": {"that": ["overlay_ipv6_router_direct_ipv6_pings | length == 0"]}},
                 ]}]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "probe.yml"
            path.write_text(yaml.safe_dump(play), encoding="utf-8")
            result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(path)],
                                    text=True, capture_output=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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

    def test_ops_direct_ipv4_is_suppressed_before_established_forwarding(self):
        tasks = yaml.safe_load(ROUTER_PLAY)[0]["tasks"]
        task = next(t for t in tasks if t["name"] == "Install the narrow ops-only IPv6 forwarding rules")
        rule = task["ansible.builtin.copy"]
        self.assertEqual(rule["dest"], "/etc/klokast/overlay-ipv6.nft")
        self.assertIn('iifname "{{ router_ops_interface }}"', rule["content"])
        self.assertIn('oifname "{{ router_wan_interface }}"', rule["content"])
        self.assertIn('ip saddr {{ platform_control_zones.ops.vm_ipv4_address }}', rule["content"])
        self.assertIn('udp sport 41641 drop', rule["content"])
        self.assertLess(ROUTER_NFT.index('include "/etc/klokast/overlay-ipv6.nft"'),
                        ROUTER_NFT.index('ct state { established, related } accept',
                                         ROUTER_NFT.index('chain forward {')))

    def test_ops_slaac_preserves_ipv4_and_requires_direct_ipv6(self):
        self.assertIn("Persist ops SLAAC kernel settings", OPS_PLAY)
        self.assertNotIn("blockinfile", OPS_PLAY)
        self.assertNotIn("service:\n        name: networking", OPS_PLAY)
        self.assertIn("ip -4 route show default", OPS_PLAY)
        self.assertIn("IPv6:[[:space:]]+yes", OPS_PLAY)
        self.assertIn("via \\[[0-9A-Fa-f:]+\\]:41641", OPS_PLAY)
        self.assertIn("ops-native-ipv6-tailscale-input", OPS_PLAY)

    def test_ops_verification_requires_direct_ipv6_pong_within_bounded_probe(self):
        tasks = yaml.safe_load(OPS_PLAY)[0]["tasks"]
        task = next(t for t in tasks if t["name"] == "Verify native IPv6 and direct peer routing")
        script = task["ansible.builtin.shell"]
        start = script.index('result=')
        end = script.index('nft list ruleset')
        probe = script[start:end].replace("{{ overlay_ipv6_peer_box }}", "boxb").replace("{{ platform_magicdns_suffix }}", "example.ts.net")
        prefix = 'set -eu\ntailscale() { printf "%s\\n" "$PING_OUTPUT"; return "$PING_RC"; }\n'
        direct = "pong from boxb-router (100.64.0.2) via [2001:db8::2]:41641 in 291ms"
        relay = "pong from boxb-router (100.64.0.2) via DERP(hkg) in 450ms"
        cases = [(direct, 0, True), (relay + "\n" + direct, 0, True), (relay, 0, False),
                 (direct + "\n" + relay, 0, True), (direct.replace("41641", "41642"), 0, False),
                 (direct.replace("[2001:db8::2]", "192.0.2.1"), 0, False),
                 (direct, 1, False), (direct + "\nextra output", 0, True)]
        for output, code, success in cases:
            with self.subTest(output=output, code=code):
                result = subprocess.run(["/bin/sh", "-s"], input=prefix + probe, text=True, capture_output=True,
                                        env={**os.environ, "PING_OUTPUT": output, "PING_RC": str(code)})
                self.assertEqual(result.returncode == 0, success, result.stderr)

    def test_rollback_covers_files_firewall_and_runtime(self):
        self.assertIn("klokast.overlay-ipv6-router-preimage.v1", ROUTER_PLAY)
        self.assertIn("Restore exact router firewall runtime state", ROUTER_PLAY)
        self.assertIn("klokast.overlay-ipv6-ops-preimage.v1", OPS_PLAY)
        self.assertIn("Restore exact ops firewall runtime state", OPS_PLAY)
        self.assertIn("verify-recovery", ROUTER_PLAY)
        self.assertIn("verify-recovery", OPS_PLAY)


if __name__ == "__main__":
    unittest.main()
