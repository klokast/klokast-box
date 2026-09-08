"""The normal inventory reader must never bypass its checked source."""
import copy
import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

from test_platform_registry import module, ROOT


class PlatformInventoryTest(unittest.TestCase):
    def setUp(self):
        self.m = module("platform-inventory")
        self.graph = {"all": {"children": ["ops"]}, "ops": {"hosts": ["boxa-ops"]}, "_meta": {"hostvars": {"boxa-ops": {"ops_airunner_enabled": True}}}}
        self.status = {"schema_version": 1, "kind": "klokast.inventory-source-status.v1", "source": "instance_specification_v1", "authority_state_sha256": "a"*64, "engine_commit": "b"*40, "rendered": {"valid": True, "kind": "klokast.inventory.v1", "projection": {"inventory": self.graph}}}

    def test_adopted_reader_never_parses_legacy_inventory(self):
        with patch.object(self.m.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(self.status))) as command:
            self.assertEqual(self.m.read_inventory(), self.graph)
            command.assert_called_once_with(["/usr/bin/doas", str(self.m.HELPER), "inventory-source-status"], text=True, capture_output=True, check=False)

    def test_reader_refuses_helper_failure_unknown_source_and_contract(self):
        values = [{**self.status, "source":"unknown"}, {**self.status, "extra":True}, {**self.status, "rendered":None}, {**self.status, "rendered": {"valid":False}}]
        for value in values:
            with patch.object(self.m.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(value))), self.assertRaises(self.m.InventoryError): self.m.read_inventory()
        for reply in (Mock(returncode=1, stdout=""), Mock(returncode=0, stdout="invalid")):
            with patch.object(self.m.subprocess, "run", return_value=reply), self.assertRaises(self.m.InventoryError): self.m.read_inventory()
        with patch.object(self.m.subprocess, "run", side_effect=FileNotFoundError), self.assertRaises(self.m.InventoryError): self.m.read_inventory()
        with patch.object(self.m, "read_inventory") as read, redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): self.m.main(["--list", "--inventory", "/legacy"])
        read.assert_not_called()

    def test_all_normal_consumers_use_source_inventory_without_legacy_host_vars(self):
        for path in list((ROOT / "ansible/bin").iterdir()) + list((ROOT / "apps").glob("*/bin/*")):
            if not path.is_file() or path.name in {"platform-inventory", "platform-builder"}: continue
            text = path.read_text()
            self.assertNotIn('inventory/hosts.yml', text, str(path))
            self.assertNotIn('"inventory" / "hosts.yml"', text, str(path))
        root = ROOT / "ansible/execution-inventory"
        self.assertFalse((root / "host_vars").exists())
        for path in (root / "group_vars").iterdir():
            self.assertTrue(path.is_symlink())
            self.assertEqual(path.resolve().parent, ROOT / "ansible/inventory/group_vars")
        self.assertNotIn("yii.yml", [p.name for p in (root / "group_vars").iterdir()])
        config = (ROOT / "ansible/ansible.cfg").read_text()
        self.assertIn("inventory = execution-inventory/hosts", config)
        self.assertIn("any_unparsed_is_failed = true", config)


if __name__ == "__main__": unittest.main()
