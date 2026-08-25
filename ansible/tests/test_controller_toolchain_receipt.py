#!/usr/bin/env python3
import hashlib
import importlib.util
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ansible/bin/controller-toolchain-receipt"


def load():
    loader = SourceFileLoader("controller_toolchain_receipt", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ControllerToolchainReceiptTest(unittest.TestCase):
    def setUp(self):
        self.mod = load()

    def test_component_hash_requires_exact_regular_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            installed = root / "installed"
            source.write_bytes(b"same")
            installed.write_bytes(b"same")
            self.assertEqual(self.mod.sha256(source), hashlib.sha256(b"same").hexdigest())
            link = root / "link"
            link.symlink_to(source)
            with self.assertRaises(self.mod.ToolchainError):
                self.mod.sha256(link)

    def test_installed_component_hash_uses_closed_privileged_reader(self):
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary) / "installed"
            installed.write_bytes(b"root-only")
            digest = hashlib.sha256(b"root-only").hexdigest()
            output = Mock(stdout=f"{digest}  {installed}\n")
            with patch.object(self.mod, "INSTALLED_COMPONENT_PATHS", frozenset({installed})), patch.object(
                self.mod, "run", return_value=output
            ) as privileged_run:
                self.assertEqual(self.mod.installed_sha256(installed), digest)
            privileged_run.assert_called_once_with(
                ["doas", self.mod.SHA256SUM, installed], capture=True
            )

    def test_installed_component_hash_refuses_path_escape_and_bad_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = root / "installed"
            unknown = root / "unknown"
            installed.write_bytes(b"root-only")
            unknown.write_bytes(b"not-allowed")
            with patch.object(self.mod, "INSTALLED_COMPONENT_PATHS", frozenset({installed})):
                with self.assertRaisesRegex(self.mod.ToolchainError, "closed component set"):
                    self.mod.installed_sha256(unknown)
                with patch.object(self.mod, "run", return_value=Mock(stdout="not-a-hash\n")):
                    with self.assertRaisesRegex(self.mod.ToolchainError, "hash is invalid"):
                        self.mod.installed_sha256(installed)

    def test_checkout_must_equal_selected_clean_engine(self):
        commit = "a" * 40
        results = [
            Mock(stdout=commit + "\n"),
            Mock(stdout="main\n"),
            Mock(stdout=""),
        ]
        with patch.object(self.mod.getpass, "getuser", return_value="smith"), patch.object(
            self.mod, "run", side_effect=results
        ):
            self.mod.verify_checkout(commit)
        results = [Mock(stdout="b" * 40), Mock(stdout="main"), Mock(stdout="")]
        with patch.object(self.mod.getpass, "getuser", return_value="smith"), patch.object(
            self.mod, "run", side_effect=results
        ):
            with self.assertRaisesRegex(self.mod.ToolchainError, "selected engine"):
                self.mod.verify_checkout(commit)

    def test_closed_component_names_match_go_validator(self):
        self.assertEqual(
            [name for name, _source, _installed in self.mod.COMPONENTS] + ["sealed_engine"],
            [
                "controller_guard", "ksa_apply", "platform_resources",
                "policy_mutation_helper", "policy_renderer", "policy_template",
                "sealed_engine",
            ],
        )

    def test_receipt_refuses_installed_source_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            installed = root / "installed"
            binary = root / "klokast"
            source.write_bytes(b"checked")
            installed.write_bytes(b"changed")
            binary.write_bytes(b"sealed")

            class Plan:
                class PlanError(Exception):
                    pass

                @staticmethod
                def require_active_controller():
                    return None

                @staticmethod
                def resolve_build_directory(_value):
                    return root, "a" * 40

                @staticmethod
                def verify_build_directory(_directory, _commit):
                    return {}, binary

                @staticmethod
                def verify_binary_version(_binary, _receipt):
                    return None

            with patch.object(self.mod, "load_plan_wrapper", return_value=Plan), patch.object(
                self.mod, "verify_checkout"
            ), patch.object(self.mod, "COMPONENTS", [("changed", source, installed)]):
                with patch.object(self.mod, "installed_sha256", return_value=hashlib.sha256(b"changed").hexdigest()):
                    with self.assertRaisesRegex(self.mod.ToolchainError, "differs"):
                        self.mod.build_receipt(root)


if __name__ == "__main__":
    unittest.main()
