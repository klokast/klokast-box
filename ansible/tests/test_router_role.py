#!/usr/bin/env python3
import unittest
from pathlib import Path


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


class RouterRoleTest(unittest.TestCase):
    def test_sysctl_service_is_enabled_in_boot_runlevel(self):
        tasks = (ROLE / "tasks" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("net.ipv4.ip_forward", tasks)
        self.assertIn("/sbin/rc-update", tasks)
        self.assertIn("- sysctl", tasks)
        self.assertIn("- boot", tasks)
        self.assertIn("name: sysctl", tasks)

    def test_tailscale_wan_egress_uses_source_ports_and_stun(self):
        self.assertIn("source_ports: [41641]", ROUTER_VARS)
        self.assertIn("source_ports: [41641, 41642, 41643]", ROUTER_VARS)
        self.assertEqual(ROUTER_VARS.count("destination_ports: [3478]"), 5)
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


if __name__ == "__main__":
    unittest.main()
