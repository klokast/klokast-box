#!/usr/bin/env python3
import importlib.util
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ansible" / "bin" / "render-node-inventory"


def load_module():
    loader = SourceFileLoader("render_node_inventory", str(SCRIPT))
    spec = importlib.util.spec_from_loader("render_node_inventory", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RenderNodeInventoryTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_rendered_inventory_contains_ops_control_vm_group(self):
        inventory = yaml.safe_load(
            self.mod.render_inventory("k001", "example.ts.net", "/dev/nvme0n1")
        )
        children = inventory["all"]["children"]

        self.assertEqual(inventory["all"]["vars"]["platform_magicdns_suffix"], "example.ts.net")
        self.assertIn("ops", children)
        self.assertIn("control_vms", children)
        self.assertEqual(children["ops"]["children"], {"k001_ops": {}})
        self.assertEqual(children["control_vms"]["children"], {"ops": {}})
        self.assertIn("k001_ops", children["k001"]["children"])
        self.assertIn("k001-ops", children["k001_ops"]["hosts"])

    def test_validate_node_name_rejects_ops_role_hostname(self):
        with self.assertRaises(SystemExit):
            self.mod.validate_node_name("k001-ops")


if __name__ == "__main__":
    unittest.main()
