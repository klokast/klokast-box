#!/usr/bin/env python3
"""Check the dom0 builder tool guard against APK solver and cleanup drift."""

import importlib.util
import json
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


HELPER = (
    Path(__file__).resolve().parents[1]
    / "roles/klokast-cli-builder/files/dom0-builder-tools.py"
)


def load_helper():
    loader = SourceFileLoader("dom0_builder_tools", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Dom0BuilderToolsTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_helper()

    def test_simulation_rejects_changes_to_preexisting_packages(self):
        baseline = {"xen": "4.20.0-r0"}
        with self.assertRaisesRegex(self.mod.ToolError, "existing package"):
            self.mod.check_simulation(
                "(1/1) Upgrading xen (4.20.1-r0)\nOK: 1 packages\n", baseline, adding=True
            )
        with self.assertRaisesRegex(self.mod.ToolError, "existing package"):
            self.mod.check_simulation(
                "(1/1) Purging xen (4.20.0-r0)\nOK: 0 packages\n", baseline, adding=False
            )
        with self.assertRaisesRegex(self.mod.ToolError, "unrecognized"):
            self.mod.check_simulation("Installing xen without a count\n", baseline, adding=True)

    def test_exact_temporary_transaction_restores_world_and_versions(self):
        operation = "ab1234cd5678"
        baseline = {"alpine-base": "3.23.4-r0", "xen": "4.20.0-r0", "xen-hypervisor": "4.20.0-r0"}
        added = {tool: "1.0-r0" for tool in self.mod.TOOLS}
        added[self.mod.VIRTUAL] = "20260930.1"
        world = "alpine-base\nxen\nxen-hypervisor\n"
        state = {"packages": dict(baseline), "world": world}

        def fake_command(argv):
            if argv[1] == "query":
                return json.dumps([{"name": name, "version": version} for name, version in state["packages"].items()])
            if argv[1] == "add" and "--simulate" in argv:
                names = [*self.mod.TOOLS, self.mod.VIRTUAL]
                return "".join(f"({index}/{len(names)}) Installing {name} (1.0-r0)\n" for index, name in enumerate(names, 1)) + "OK: simulated\n"
            if argv[1] == "--cache-dir" and argv[4] == "add":
                self.assertTrue(Path(argv[2]).is_relative_to(state_dir))
                state["packages"].update(added)
                world_path.write_text(world + self.mod.VIRTUAL + "\n", encoding="utf-8")
                return ""
            if "del" in argv and "--simulate" in argv:
                names = [*self.mod.TOOLS, self.mod.VIRTUAL]
                return "".join(f"({index}/{len(names)}) Purging {name} (1.0-r0)\n" for index, name in enumerate(names, 1)) + "OK: simulated\n"
            if "del" in argv:
                state["packages"] = dict(baseline)
                world_path.write_text(world, encoding="utf-8")
                return ""
            self.fail(f"unexpected APK command: {argv}")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world_path = root / "world"
            world_path.write_text(world, encoding="utf-8")
            expected = root / "expected"
            expected.write_text(world, encoding="utf-8")
            state_dir = root / "state"
            with patch.object(self.mod, "WORLD_FILE", world_path), patch.object(
                self.mod, "STATE_DIR", state_dir
            ), patch.object(self.mod, "STATE_FILE", state_dir / "state.json"), patch.object(
                self.mod, "CACHE_DIR", state_dir / "cache"
            ), patch.object(
                self.mod, "UNLOCK_FILE", root / "unlock"
            ), patch.object(self.mod, "command", side_effect=fake_command):
                self.mod.begin(operation, expected)
                self.assertTrue((state_dir / "state.json").is_file())
                self.assertFalse((root / "unlock").exists())
                self.assertEqual(state["packages"]["xen"], baseline["xen"])
                self.mod.end(operation, expected)
                self.assertFalse(state_dir.exists())
                self.assertEqual(state["packages"], baseline)
                self.assertEqual(world_path.read_text(encoding="utf-8"), world)

    def test_begin_refuses_installed_tool_and_prior_unlock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = root / "expected"
            expected.write_text("xen\n", encoding="utf-8")
            world = root / "world"
            world.write_text("xen\n", encoding="utf-8")
            unlock = root / "unlock"
            with patch.object(self.mod, "WORLD_FILE", world), patch.object(
                self.mod, "STATE_DIR", root / "state"
            ), patch.object(self.mod, "UNLOCK_FILE", unlock), patch.object(
                self.mod, "installed_packages", return_value={"xen": "4.20-r0", "curl": "1-r0"}
            ):
                with self.assertRaisesRegex(self.mod.ToolError, "must all be absent"):
                    self.mod.begin("ab1234cd5678", expected)
                unlock.write_text(self.mod.UNLOCK_CONTENT, encoding="utf-8")
                with self.assertRaisesRegex(self.mod.ToolError, "already unlocked"):
                    self.mod.begin("ab1234cd5678", expected)
                self.assertTrue(unlock.is_file())

    def test_recovery_keeps_record_when_xen_version_has_drifted(self):
        operation = "ab1234cd5678"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_dir = root / "state"
            state_dir.mkdir()
            (state_dir / "state.json").write_text(
                json.dumps({"operation": operation, "packages": {"xen": "4.20.0-r0"}, "world": "xen\n"}),
                encoding="utf-8",
            )
            world = root / "world"
            world.write_text("xen\n" + self.mod.VIRTUAL + "\n", encoding="utf-8")
            expected = root / "expected"
            expected.write_text("xen\n", encoding="utf-8")
            with patch.object(self.mod, "WORLD_FILE", world), patch.object(
                self.mod, "STATE_DIR", state_dir
            ), patch.object(self.mod, "STATE_FILE", state_dir / "state.json"), patch.object(
                self.mod, "UNLOCK_FILE", root / "unlock"
            ), patch.object(self.mod, "installed_packages", return_value={"xen": "4.20.1-r0"}):
                with self.assertRaisesRegex(self.mod.ToolError, "pre-existing package drift"):
                    self.mod.end(operation, expected)
                self.assertTrue((state_dir / "state.json").is_file())
                self.assertEqual(world.read_text(encoding="utf-8"), "xen\n" + self.mod.VIRTUAL + "\n")


if __name__ == "__main__":
    unittest.main()
