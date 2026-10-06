#!/usr/bin/env python3
import unittest
from pathlib import Path
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "ansible" / "roles" / "router"
ROUTER_VARS = (
    REPO_ROOT / "ansible" / "inventory" / "group_vars" / "router.yml"
).read_text(encoding="utf-8")
ROUTER_TEMPLATE = (ROLE / "templates" / "nftables.nft.j2").read_text(
    encoding="utf-8"
)
ROUTER_VERIFY = (
    REPO_ROOT / "ansible" / "roles" / "router-verification" / "tasks" / "main.yml"
).read_text(encoding="utf-8")
ROUTER_PLAYBOOK = (
    REPO_ROOT / "ansible" / "playbooks" / "31-vm-router.yml"
).read_text(encoding="utf-8")
ANSIBLE_CONFIG = (REPO_ROOT / "ansible" / "ansible.cfg").read_text(encoding="utf-8")


class RouterRoleTest(unittest.TestCase):
    def test_overlay_ipv6_rule_precedes_established_forward_traffic(self):
        forward = ROUTER_TEMPLATE.split("    chain forward {", 1)[1].split(
            "    chain output {", 1
        )[0]
        self.assertLess(
            forward.index('include "/etc/klokast/overlay-ipv6.nft"'),
            forward.index("ct state { established, related } accept"),
        )

    def test_sysctl_service_is_enabled_in_boot_runlevel(self):
        tasks = "\n".join((ROLE / "tasks" / name).read_text(encoding="utf-8")
                          for name in ("render.yml", "activate.yml"))
        self.assertIn("net.ipv4.ip_forward", tasks)
        self.assertIn("/sbin/rc-update", tasks)
        self.assertIn("- sysctl", tasks)
        self.assertIn("- boot", tasks)
        self.assertIn("name: sysctl", tasks)

    def test_tailscale_wan_egress_uses_source_ports_and_stun(self):
        rules = yaml.safe_load(ROUTER_VARS)['router_tailscale_udp_egress_rules']
        expected = [
            {'interface': '{{ platform_zones.' + role + '.router_interface }}',
             'source': '{{ platform_zones.' + role + '.vm_ipv4_address }}',
             'source_ports': [41641], 'destination_ports': [3478]}
            for role in ('bak', 'dmz', 'iot')
        ]
        expected.append({
            'interface': '{{ platform_control_zones.ops.router_interface }}',
            'source': '{{ platform_control_zones.ops.vm_ipv4_address }}',
            'source_ports': [41641, 41642, 41643], 'destination_ports': [3478]})
        self.assertCountEqual(rules, expected)
        self.assertIn("udp sport", ROUTER_TEMPLATE)
        self.assertIn("rule.source_ports", ROUTER_TEMPLATE)
        self.assertIn("udp dport", ROUTER_TEMPLATE)
        self.assertIn("rule.destination_ports", ROUTER_TEMPLATE)
        self.assertIn("managed-vm-tailscale-source-egress", ROUTER_TEMPLATE)
        self.assertIn("managed-vm-tailscale-stun-egress", ROUTER_TEMPLATE)
        self.assertNotIn("managed-vm-tailscale-udp{{ rule.port }}-egress", ROUTER_TEMPLATE)

    def test_router_verification_rejects_legacy_tailscale_wan_rule(self):
        self.assertIn("managed-vm-tailscale-source-egress", ROUTER_VERIFY)
        self.assertIn("managed-vm-tailscale-stun-egress", ROUTER_VERIFY)
        self.assertIn(
            "'\"managed-vm-tailscale-udp41641-egress\" not in verify_router_nft_conf.stdout'",
            ROUTER_VERIFY,
        )
        self.assertIn(
            "'\"managed-vm-tailscale-udp41641-egress\" not in verify_router_nft_ruleset.stdout'",
            ROUTER_VERIFY,
        )

    def test_controller_temp_paths_are_user_scoped(self):
        self.assertIn("fact_caching_connection = ~/.ansible/facts", ANSIBLE_CONFIG)
        self.assertIn("local_tmp = ~/.ansible/tmp", ANSIBLE_CONFIG)
        self.assertNotIn("fact_caching_connection = /tmp/", ANSIBLE_CONFIG)
        self.assertNotIn("local_tmp = /tmp/", ANSIBLE_CONFIG)
        self.assertNotIn('ansible_remote_tmp: /tmp/', ROUTER_PLAYBOOK)


if __name__ == "__main__":
    unittest.main()
