#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "ansible" / "bin" / "platform-plan"


def load(path, name):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PlatformPlanTest(unittest.TestCase):
    def setUp(self):
        self.mod = load(WRAPPER, "platform_plan")

    def test_requires_smith_and_an_active_configured_controller(self):
        with patch.object(self.mod.getpass, "getuser", return_value="agent"):
            with self.assertRaisesRegex(self.mod.PlanError, "smith"):
                self.mod.require_active_controller()
        completed = Mock(returncode=0, stdout='{"configured":true,"active":false}')
        with patch.object(self.mod.getpass, "getuser", return_value="smith"), patch.object(
            self.mod.Path, "is_file", return_value=True
        ), patch.object(self.mod.os, "access", return_value=True), patch.object(
            self.mod, "run", return_value=completed
        ):
            with self.assertRaisesRegex(self.mod.PlanError, "explicitly configured"):
                self.mod.require_active_controller()

    def test_build_directory_is_exact_and_has_no_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "builds"
            commit = "a" * 40
            operation = "0123456789ab"
            directory = root / commit / operation
            directory.mkdir(parents=True)
            with patch.object(self.mod, "BUILD_ROOT", root):
                resolved, selected = self.mod.resolve_build_directory(str(directory))
                self.assertEqual((resolved, selected), (directory, commit))
                with self.assertRaises(self.mod.PlanError):
                    self.mod.resolve_build_directory(str(root / commit))
                link = root / commit / "abcdefabcdef"
                link.symlink_to(directory, target_is_directory=True)
                with self.assertRaisesRegex(self.mod.PlanError, "symbolic"):
                    self.mod.resolve_build_directory(str(link))

    def test_verifies_builder_receipt_and_binary_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            binary = directory / "klokast"
            binary.write_bytes(b"verified binary")
            binary.chmod(0o750)
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            receipt = {
                "schema_version": 1,
                "source_repository": self.mod.ENGINE_REPOSITORY,
                "source_ref": "main",
                "source_commit": "a" * 40,
                "test_result": "success",
                "build_result": "success",
                "binary_sha256": digest,
            }
            (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            verified, path = self.mod.verify_build_directory(directory, "a" * 40)
            self.assertEqual((verified, path), (receipt, binary))
            receipt["binary_sha256"] = "0" * 64
            (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(self.mod.PlanError, "binary hash"):
                self.mod.verify_build_directory(directory, "a" * 40)

    def test_instance_source_receipt_path_is_root_owned_and_content_addressed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commit = "a" * 40
            digest = "b" * 64
            directory = root / commit
            directory.mkdir(mode=0o750)
            receipt = directory / f"{digest}.json"
            receipt.write_text(json.dumps({
                "commit": commit,
                "receipt_sha256": digest,
            }), encoding="utf-8")
            receipt.chmod(0o440)
            real_stat = Path.stat

            def root_stat(path, *args, **kwargs):
                value = real_stat(path, *args, **kwargs)
                fields = list(value)
                if Path(path) == receipt:
                    fields[4] = 0
                    fields[5] = os.getgid()
                if Path(path) == directory:
                    fields[4] = 0
                    fields[5] = os.getgid()
                return os.stat_result(fields)

            account = Mock(pw_gid=os.getgid())
            with patch.object(self.mod, "INSTANCE_SOURCE_ROOT", root), patch.object(
                self.mod.Path, "stat", autospec=True, side_effect=root_stat
            ), patch.object(self.mod.pwd, "getpwnam", return_value=account):
                self.assertEqual(self.mod.resolve_instance_source_receipt(str(receipt)), receipt)
                alias = root / "alias.json"
                alias.symlink_to(receipt)
                with self.assertRaises(self.mod.PlanError):
                    self.mod.resolve_instance_source_receipt(str(alias))

    def test_plan_hash_is_canonical_and_tamper_evident(self):
        plan = {
            "schema_version": 2,
            "kind": "klokast.plan.v2",
            "valid": True,
            "deployable": False,
            "instance": {"commit": "b" * 40},
            "actions": [],
        }
        digest = hashlib.sha256(self.mod.canonical_plan(plan)).hexdigest()
        plan["plan_sha256"] = digest
        verified, content = self.mod.verify_plan(json.dumps(plan).encode("utf-8"))
        self.assertEqual(verified["plan_sha256"], digest)
        self.assertEqual(json.loads(content), verified)
        plan["valid"] = False
        with self.assertRaisesRegex(self.mod.PlanError, "valid plan"):
            self.mod.verify_plan(json.dumps(plan).encode("utf-8"))
        plan["valid"] = True
        plan["deployable"] = True
        with self.assertRaisesRegex(self.mod.PlanError, "hash"):
            self.mod.verify_plan(json.dumps(plan).encode("utf-8"))

    def test_plan_v1_is_historical_evidence_only(self):
        plan = {
            "schema_version": 1,
            "kind": "klokast.plan.v1",
            "valid": True,
            "deployable": True,
            "instance": {"commit": "b" * 40},
        }
        plan["plan_sha256"] = hashlib.sha256(self.mod.canonical_plan(plan)).hexdigest()
        with self.assertRaisesRegex(self.mod.PlanError, "unsupported plan"):
            self.mod.verify_plan(json.dumps(plan).encode("utf-8"))

    def test_non_deployable_plan_is_valid_audit_output(self):
        plan = {
            "schema_version": 2,
            "kind": "klokast.plan.v2",
            "valid": True,
            "deployable": False,
            "instance": {"commit": "b" * 40},
        }
        plan["plan_sha256"] = hashlib.sha256(self.mod.canonical_plan(plan)).hexdigest()
        completed = Mock(returncode=2, stdout=json.dumps(plan), stderr="")
        with patch.object(self.mod, "run", return_value=completed):
            verified, _ = self.mod.create_plan(Path("/verified/klokast"), ["--instance", "/private"])
        self.assertFalse(verified["deployable"])

    def test_plan_directories_are_traversable_by_smith(self):
        destination = self.mod.PLAN_ROOT / ("a" * 40)
        account = Mock(pw_gid=1234)
        with patch.object(self.mod.pwd, "getpwnam", return_value=account), patch.object(
            self.mod, "run"
        ) as run:
            smith_group = self.mod.prepare_plan_directory(destination)
        self.assertEqual(smith_group, "1234")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                [
                    "doas", "install", "-d", "-o", "root", "-g", "1234", "-m", "0750",
                    self.mod.PLAN_ROOT,
                ],
                [
                    "doas", "install", "-d", "-o", "root", "-g", "1234", "-m", "0750",
                    destination,
                ],
            ],
        )

    def test_immutable_install_uses_no_replace_link(self):
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertLess(
            source.index("smith_group = prepare_plan_directory(destination_directory)"),
            source.index("if destination.exists():"),
        )
        self.assertIn('run(["doas", "ln", target_temporary, destination], check=False)', source)
        self.assertIn('run(["doas", "rm", "-f", target_temporary], check=False)', source)
        self.assertNotIn("os.replace", source)


if __name__ == "__main__":
    unittest.main()
