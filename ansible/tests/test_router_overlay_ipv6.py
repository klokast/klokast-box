"""Closed signed-source and exact router IPv6 reconstruction checks."""

import ipaddress
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import router_overlay_ipv6 as overlay


class RouterOverlayIPv6Test(unittest.TestCase):
    def source(self):
        value = {
            "kind": "klokast.router-overlay-ipv6-source.v1",
            "box": "boxa", "peer_box": "boxb",
            "delegated_prefix": "2001:4860:1234:5678::/64",
            "router_next_hop": overlay.stable_next_hop("boxa"),
            "freebox_gateway_id_sha256": "a" * 64,
            "delegation_slot": 2,
            "authority_state_sha256": "b" * 64,
            "repair_receipt_sha256": "c" * 64,
            "intent_sha256": "d" * 64,
        }
        return {**value, "source_sha256": overlay.digest(value)}

    def test_rebuilds_only_the_signed_ops_path(self):
        source = self.source()
        files = overlay.files(source, "boxa", wan="eth0", ops="eth5", ops_ipv4="192.168.100.10")
        self.assertEqual(set(files), {
            "etc/klokast/overlay-ipv6.nft", "etc/network/if-up.d/91-klokast-ops-ipv6",
            "etc/sysctl.d/91-klokast-ops-ipv6.conf", "etc/dnsmasq.d/91-klokast-ops-ipv6.conf"})
        self.assertIn("ip6 saddr 2001:4860:1234:5678::/64 udp sport 41641 accept",
                      files["etc/klokast/overlay-ipv6.nft"])
        self.assertIn("ip saddr 192.168.100.10 udp sport 41641 drop",
                      files["etc/klokast/overlay-ipv6.nft"])
        self.assertIn("ip -6 address replace 2001:4860:1234:5678::1/64 dev eth5",
                      files["etc/network/if-up.d/91-klokast-ops-ipv6"])
        self.assertIn("net.ipv6.conf.eth0.accept_ra = 2",
                      files["etc/sysctl.d/91-klokast-ops-ipv6.conf"])
        self.assertEqual(files["etc/dnsmasq.d/91-klokast-ops-ipv6.conf"],
                         "enable-ra\ndhcp-range=::,constructor:eth5,ra-only,64,12h\n")
        self.assertEqual(str(ipaddress.IPv6Address(source["router_next_hop"])),
                         source["router_next_hop"])

    def test_rejects_tampering_and_non_ops_selection(self):
        source = self.source()
        for change in (
            {"peer_box": "boxa"}, {"delegated_prefix": "2001:4860:1234:9::/64"},
            {"router_next_hop": "fe80::1"}, {"command": "shell"},
            {"source_sha256": "0" * 64},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                overlay.validate_source({**source, **change}, "boxa")
        with self.assertRaises(ValueError):
            overlay.validate_source(source, "boxb")
        with self.assertRaises(ValueError):
            overlay.files(source, "boxa", wan="eth0; touch /tmp/x", ops="eth5",
                          ops_ipv4="192.168.100.10")


if __name__ == "__main__":
    unittest.main()
