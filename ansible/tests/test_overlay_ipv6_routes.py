import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "overlay_routes", Path(__file__).resolve().parents[1] / "lib/overlay_ipv6_routes.py")
routes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routes)
PREFIX = "2001:db8:1234:1::/64"


class OverlayRoutesTest(unittest.TestCase):
    def test_removes_only_new_repair_routes(self):
        prefix = {"dst": PREFIX, "protocol": "kernel", "metric": 256, "flags": [], "pref": "medium"}
        default = {"dst": "default", "gateway": "fe80::1", "protocol": "ra", "metric": 1024, "flags": [], "metrics": [{"hoplimit": 64}]}
        other = {"dst": "2001:db8:2::/64", "protocol": "kernel", "metric": 256}
        static = {"dst": "default", "gateway": "fe80::2", "protocol": "static", "metric": 512}
        current = [prefix, default, other, static]
        self.assertEqual(routes.deletions([], current, "eth0", PREFIX), [
            ["ip", "-6", "route", "del", PREFIX, "dev", "eth0", "proto", "kernel", "metric", "256"],
            ["ip", "-6", "route", "del", "default", "via", "fe80::1", "dev", "eth0", "proto", "ra", "metric", "1024"],
        ])
        self.assertEqual(routes.deletions([prefix, default], current, "eth0", PREFIX), [])

    def test_refuses_unknown_new_route_shape(self):
        route = {"dst": PREFIX, "protocol": "kernel", "metric": 256}
        for extra in ({"nexthops": []}, {"flags": ["linkdown"]}, {"dev": "eth1"}, {"table": "other"}, {"metric": "invalid"}, {"metrics": [{"unknown": 64}]}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                routes.deletions([], [{**route, **extra}], "eth0", PREFIX)
        with self.assertRaises(ValueError):
            routes.deletions({}, [], "eth0", PREFIX)

    def test_snapshot_excludes_only_expiry(self):
        with patch.object(routes.subprocess, "check_output", return_value='[{"dst":"default","protocol":"ra","expires":50,"metric":1024}]'):
            self.assertEqual(routes.snapshot("eth0"), [{"dst": "default", "protocol": "ra", "metric": 1024}])


if __name__ == "__main__":
    unittest.main()
