#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ansible/bin/check-huawei-tailscale-pinhole"
ROUTER_HELPER = (ROOT / "ansible/bin/overlay-ipv6-router").read_text(encoding="utf-8")
ROUTER_PLAY = (ROOT / "ansible/playbooks/83-overlay-ipv6-router.yml").read_text(encoding="utf-8")


def load_helper():
    loader = SourceFileLoader("huawei_pinhole_check_test", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class HuaweiPinholeCheckTest(unittest.TestCase):
    def setUp(self):
        self.module = load_helper()
        self.address = self.module.ipaddress.IPv6Address("2408:8207:6c74:3f90::1234")

    def test_direct_ping_requires_the_current_ipv6_udp_endpoint(self):
        good = {
            "rc": 0,
            "stdout": "pong from peer (100.64.0.2) via [2408:8207:6c74:3f90::1234]:41641 in 10ms",
        }
        self.assertEqual(self.module.ping_status(good, self.address), (True, "direct IPv6 UDP 41641"))
        for output in (
            "pong from peer (100.64.0.2) via DERP(hkg) in 10ms",
            "pong from peer (100.64.0.2) via 192.0.2.1:41641 in 10ms",
            "pong from peer (100.64.0.2) via [2408:8207:6c74:3f90::9999]:41641 in 10ms",
        ):
            with self.subTest(output=output):
                self.assertFalse(self.module.ping_status({"rc": 0, "stdout": output}, self.address)[0])

    def test_derp_status_requires_a_reachable_preferred_region(self):
        good = {"rc": 0, "stdout": json.dumps({"PreferredDERP": 18, "RegionLatency": {"18": 6_400_000}})}
        self.assertEqual(self.module.derp_status(good), (True, "region 18, 6.4 ms"))
        for result in (
            {"rc": 1, "stdout": ""},
            {"rc": 0, "stdout": "not json"},
            {"rc": 0, "stdout": json.dumps({"PreferredDERP": 0, "RegionLatency": {}})},
            {"rc": 0, "stdout": json.dumps({"PreferredDERP": 18, "RegionLatency": {}})},
        ):
            with self.subTest(result=result):
                self.assertFalse(self.module.derp_status(result)[0])

    def test_recorded_baseline_contains_exact_address_and_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            baseline = Path(temporary) / "huawei-tailscale-pinhole.json"
            with patch.object(self.module, "BASELINE", baseline):
                prefix = self.module.record_baseline("k001", self.address)
                loaded_address, loaded_prefix = self.module.load_baseline("k001")
            self.assertEqual(prefix, self.module.ipaddress.IPv6Network("2408:8207:6c74:3f90::/64"))
            self.assertEqual(loaded_address, self.address)
            self.assertEqual(loaded_prefix, prefix)
            self.assertEqual(baseline.stat().st_mode & 0o777, 0o600)

    def test_dangling_baseline_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            baseline = Path(temporary) / "huawei-tailscale-pinhole.json"
            baseline.symlink_to(Path(temporary) / "missing")
            with patch.object(self.module, "BASELINE", baseline), self.assertRaisesRegex(
                self.module.CheckError, "baseline path is unsafe"
            ):
                self.module.load_baseline("k001")

    def test_router_workflow_collects_read_only_connectivity_evidence(self):
        self.assertIn('"connectivity-check"', ROUTER_HELPER)
        self.assertIn("Check DERP reachability from each router", ROUTER_PLAY)
        self.assertIn("tailscale\n          - netcheck", ROUTER_PLAY)
        self.assertIn("klokast.overlay-connectivity-check.v1", ROUTER_PLAY)
        self.assertIn("overlay_ipv6_router_operation == 'connectivity-check'", ROUTER_PLAY)


if __name__ == "__main__":
    unittest.main()
