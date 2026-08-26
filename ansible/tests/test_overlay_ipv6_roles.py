#!/usr/bin/env python3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROUTER_VARS = (ROOT / "ansible/inventory/group_vars/router.yml").read_text(encoding="utf-8")
ROUTER_PLAY = (ROOT / "ansible/playbooks/83-overlay-ipv6-router.yml").read_text(encoding="utf-8")
OPS_PLAY = (ROOT / "ansible/playbooks/84-overlay-ipv6-ops.yml").read_text(encoding="utf-8")
ROUTER_NFT = (ROOT / "ansible/roles/router/templates/nftables.nft.j2").read_text(encoding="utf-8")


class OverlayIPv6RoleTest(unittest.TestCase):
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
