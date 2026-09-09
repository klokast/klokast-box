"""The normal inventory reader must never bypass its checked source."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
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

    def legacy_status(self):
        return {k: v for k, v in dict(self.status, source="legacy_engine_inventory").items() if k != "rendered"}

    def controller_status(self):
        return dict(self.legacy_status(), kind="klokast.controller-identity-status.v1", source="instance_specification_v1",
                    controllers={role: {"box": box, "hostname": box + "-ops"} for role, box in (("active", "boxa"), ("standby", "boxb"))})

    def test_legacy_reader_parses_real_boxes_with_retained_host_variables_and_cleans_up(self):
        paths = []
        run = subprocess.run
        for changed in (False, True):
            controller_calls = 0
            def invoke(command):
                nonlocal controller_calls
                if str(command[0]) == "/usr/bin/doas":
                    if command[2] == "inventory-source-status": return self.legacy_status()
                    controller_calls += 1
                    controller = self.controller_status()
                    if changed and controller_calls == 2: controller["authority_state_sha256"] = "c" * 64
                    return controller
                self.assertEqual(command[:3], ["ansible-inventory", "-i", ROOT / "ansible/inventory/hosts.yml"])
                self.assertEqual(command[-1], "--list")
                for box, path in zip(("boxa", "boxb"), (Path(command[4]), Path(command[6]))):
                    self.assertIn(box + "-ops:", path.read_text())
                    self.assertIn("fixture.ts.net", path.read_text())
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                    paths.append(path)
                return self.graph
            def command(args, **kwargs):
                if Path(args[0]).name == "magicdns-suffix": return Mock(returncode=0, stdout="fixture.ts.net\n")
                return run(args, **kwargs)
            with patch.object(self.m, "REPO", ROOT), patch.object(self.m, "invoke", side_effect=invoke), patch.object(self.m.subprocess, "run", side_effect=command):
                if changed:
                    with self.assertRaisesRegex(self.m.InventoryError, "controller source changed"): self.m.read_inventory()
                else: self.assertEqual(self.m.read_inventory(), self.graph)
            self.assertTrue(paths)
            self.assertTrue(all(not p.parent.exists() for p in paths))

    def test_legacy_reader_rejects_wrong_or_incomplete_controllers_before_rendering(self):
        for change in (
            lambda c: c.update(engine_commit="c" * 40),
            lambda c: c.update(authority_state_sha256="c" * 64),
            lambda c: c.update(extra=True),
            lambda c: c["controllers"].pop("standby"),
            lambda c: c["controllers"].update(standby=c["controllers"]["active"]),
            lambda c: c["controllers"]["active"].update(box="../boxa"),
            lambda c: c["controllers"]["active"].update(hostname="other-ops"),
        ):
            controller = self.controller_status(); change(controller)
            with patch.object(self.m, "invoke", return_value=controller), patch.object(self.m.subprocess, "run") as command, self.assertRaises(self.m.InventoryError):
                self.m.read_legacy_inventory(self.legacy_status())
            command.assert_not_called()

    def test_legacy_reader_refuses_source_change_after_parsing(self):
        with patch.object(self.m, "invoke", side_effect=[self.legacy_status(), dict(self.legacy_status(), authority_state_sha256="c" * 64)]), patch.object(self.m, "read_legacy_inventory", return_value=self.graph), self.assertRaisesRegex(self.m.InventoryError, "inventory source changed"):
            self.m.read_inventory()

    @unittest.skipUnless(shutil.which("ansible-inventory") and shutil.which("ansible-playbook"), "requires controller Ansible")
    def test_real_fact_cache_changes_do_not_change_inventory_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inventory = root / "hosts.json"
            inventory.write_text(json.dumps({"all": {"hosts": {"fixture": {"ansible_connection": "local", "declared_limit": 42, "ansible_memtotal_mb": 123}}}}))
            playbook = root / "cache.json"
            playbook.write_text(json.dumps([{"hosts": "fixture", "gather_facts": False, "tasks": [{"ansible.builtin.set_fact": {"cached_observation": "{{ observation }}", "cacheable": True}}]}]))
            config = root / "ansible.cfg"
            config.write_text("[defaults]\nstdout_callback = default\n")
            env = {**os.environ, "ANSIBLE_CONFIG": str(config), "ANSIBLE_CACHE_PLUGIN": "jsonfile", "ANSIBLE_CACHE_PLUGIN_CONNECTION": str(root / "facts")}
            command = ["ansible-inventory", "-i", str(inventory), "--list"]
            first = None
            for value in ("before-router-verification", "after-router-verification"):
                subprocess.run(["ansible-playbook", "-i", str(inventory), str(playbook), "-e", "observation=" + value], env=env, capture_output=True, text=True, check=True)
                cached = json.loads(subprocess.check_output(command, env=env, text=True))
                self.assertEqual(cached["_meta"]["hostvars"]["fixture"]["cached_observation"], value)
                with patch.dict(os.environ, env, clear=True):
                    actual = self.m.invoke(command)
                variables = actual["_meta"]["hostvars"]["fixture"]
                self.assertNotIn("cached_observation", variables)
                self.assertEqual(variables["declared_limit"], 42)
                self.assertEqual(variables["ansible_memtotal_mb"], 123)
                if first is None: first = actual
                else: self.assertEqual(actual, first)

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
