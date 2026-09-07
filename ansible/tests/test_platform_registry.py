import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]


def module(name):
    loader = SourceFileLoader("registry_test_" + name.replace("-", "_"), str(ROOT / "ansible/bin" / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    value = importlib.util.module_from_spec(spec)
    loader.exec_module(value)
    return value


class PlatformRegistryTest(unittest.TestCase):
    def setUp(self):
        self.m = module("platform-registry")

    def status(self, adopted=False):
        result = {"schema_version": 1, "kind": "klokast.registry-source-status.v1", "source": "instance_specification_v1" if adopted else "legacy_platform_resources", "authority_state_sha256": "a" * 64, "engine_commit": "b" * 40}
        if adopted:
            result["rendered"] = {"valid": True, "projection": {"registry": {"schema_version": 1, "boxes": {}, "apps": {}}, "registry_sha256": "c" * 64}}
        return result

    def test_read_uses_selected_source_and_refuses_override_or_write(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.yml"
            path.write_text("schema_version: 1\napps: {old: {enabled: false}}\n")
            with patch.object(m, "REGISTRY", path):
                legacy = m.read_registry(path, self.status())
                self.assertIn("old", legacy["registry"]["apps"])
                m.assert_writable(self.status())
                current = m.read_registry(path, self.status(True))
                self.assertEqual(current["registry"]["apps"], {})
                self.assertEqual(current["registry_path"], str(m.INSTANCE))
                with self.assertRaisesRegex(m.RegistryError, "overrides"): m.read_registry(path.with_name("other.yml"), self.status(True))
                with self.assertRaisesRegex(m.RegistryError, "publish instance intent"): m.assert_writable(self.status(True))
                self.assertEqual(m.read_registry(path, self.status()), legacy)

    def test_no_fallback_on_helper_failure_or_unknown_contract(self):
        m = self.m
        for result in (Mock(returncode=1, stdout=""), Mock(returncode=0, stdout="truncated"), Mock(returncode=0, stdout=json.dumps({**self.status(), "extra": True})), Mock(returncode=0, stdout=json.dumps({**self.status(), "source": "unknown"})), Mock(returncode=0, stdout=json.dumps({**self.status(True), "rendered": None}))):
            with patch.object(m.subprocess, "run", return_value=result), redirect_stderr(io.StringIO()):
                if '"rendered": null' in result.stdout:
                    with self.assertRaises(m.RegistryError): m.read_registry(m.REGISTRY, m.source_status())
                else:
                    with self.assertRaises(m.RegistryError): m.source_status()
        with patch.object(m.subprocess, "run", side_effect=FileNotFoundError), self.assertRaises(m.RegistryError): m.source_status()

    def test_compiler_compatibility_mode_is_read_only_and_normal_read_has_provenance(self):
        resources = module("platform-resources")
        for command in ("apply", "grant", "apply-box-access", "apply-shared-guests", "inventory", "diff"):
            args = SimpleNamespace(command=command, compatibility_registry=True, registry="/unused")
            with patch.object(resources.subprocess, "run") as run, redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): resources.registry_source_input(args)
            run.assert_not_called()
        for command in ("show", "show-box-configs", "show-box-access-vars", "verify-box-access"):
            self.assertIsNone(resources.registry_source_input(SimpleNamespace(command=command, compatibility_registry=True)))
        args = SimpleNamespace(command="show", compatibility_registry=False, registry="/legacy")
        current = {"schema_version": 1, "kind": "klokast.registry-read.v1", "source": "instance_specification_v1", "registry_path": "/private/instance.json", "registry_sha256": "c" * 64, "registry": {"schema_version": 1, "boxes": {}, "apps": {}}}
        with patch.object(resources.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(current))) as run:
            resolved = resources.registry_source_input(args)
        self.assertEqual(resolved, current)
        self.assertEqual(run.call_args.args[0][0], "/usr/local/sbin/platform-registry")
        topology = resources.load_topology()
        with patch.object(resources, "load_topology", return_value=topology), patch.object(resources, "load_yaml", side_effect=AssertionError("compiler read legacy input")), patch.object(resources, "sha256_file", side_effect=AssertionError("compiler hashed legacy input")):
            result = resources.compile_box_registry_plan(Path("/legacy"), registry_input=current)
        self.assertEqual(result["registry_sha256"], "c" * 64)
        self.assertEqual(result["registry_path"], "/private/instance.json")

    def test_lifecycle_writers_refuse_before_read_write_or_runtime(self):
        for name, operation in (("platform-guest", "set_runtime_state"), ("platform-app", "mutate_runtime_state"), ("platform-app", "remove_one")):
            value = module(name)
            with patch.object(value, "require_active_controller"), patch.object(value, "assert_registry_writable", side_effect=SystemExit("instance source")), patch.object(value, "load_registry") as read, patch.object(value, "save_registry") as write, self.assertRaises(SystemExit):
                getattr(value, operation)(Mock(), False if operation == "remove_one" else "stopped")
            read.assert_not_called()
            write.assert_not_called()


if __name__ == "__main__": unittest.main()
