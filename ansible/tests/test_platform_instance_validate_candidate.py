#!/usr/bin/env python3
import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ansible" / "bin" / "platform-instance"
ENGINE_COMMIT = "a" * 40


class BinaryInput:
    def __init__(self, content):
        self.buffer = io.BytesIO(content)


class PlatformInstanceValidateCandidateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        loader = SourceFileLoader("platform_instance_validate_candidate_test", str(SCRIPT))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.mod = importlib.util.module_from_spec(spec)
        loader.exec_module(self.mod)

        self.private_root = self.root / "private"
        self.private_root.mkdir(mode=0o700)
        self.seed = self.private_root / "instance-seed"
        self.seed.mkdir(mode=0o700)
        self.mod.PRIVATE_ROOT = self.private_root
        self.mod.SEED_PATH = self.seed
        self.make_seed()
        self.checker = self.make_checker()
        self.args = argparse.Namespace(
            engine_commit=ENGINE_COMMIT,
            build_dir=str(self.root / "build"),
            require_compatible=False,
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def git(root, *arguments, check=True):
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def make_seed(self):
        files = {
            ".gitignore": "*.private\n",
            "AGENTS.md": "# Private instructions\n",
            "README.md": "# Private instance\n",
            "klokast.lock.json": json.dumps({
                "schema-version": 1,
                "engine": {"commit": ENGINE_COMMIT},
            }) + "\n",
            "klokast-instance.json": json.dumps({
                "schema-version": 1,
                "boxes": {
                    "boxa": {"site": "site-b", "country": "XB", "description": ""},
                    "boxb": {"site": "site-a", "country": "XA", "description": ""},
                },
            }) + "\n",
        }
        for name, content in files.items():
            (self.seed / name).write_text(content, encoding="utf-8")
        self.git(self.seed, "init", "-q", "--initial-branch=main")
        self.git(self.seed, "add", "-A")
        for current, directories, names in os.walk(self.seed):
            os.chmod(current, 0o700)
            for name in names:
                os.chmod(Path(current) / name, 0o600)
        # The private seed root is the access boundary. Git can keep its normal
        # nested directory mode below that owner-only root.
        os.chmod(self.seed / ".git", 0o755)

    def make_checker(self):
        checker = self.root / "klokast"
        checker.write_text(
            """#!/usr/bin/env python3
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[sys.argv.index("--instance") + 1])
value = json.loads((root / "klokast-instance.json").read_text())
valid = value.get("boxes", {}).get("boxa", {}).get("site") == "site-a"
result = {"valid": valid, "diagnostics": []}
if not valid:
    result["diagnostics"].append({
        "path": "klokast-instance.json#/boxes/boxa/site",
        "code": "test.site",
        "message": "candidate site was rejected",
    })
print(json.dumps(result))
raise SystemExit(0 if valid else 1)
""",
            encoding="utf-8",
        )
        os.chmod(checker, 0o700)
        return checker

    @staticmethod
    def candidate(boxa="site-a", boxb="site-b"):
        return (json.dumps({
            "schema-version": 1,
            "boxes": {
                "boxa": {"site": boxa, "country": "XA", "description": ""},
                "boxb": {"site": boxb, "country": "XB", "description": ""},
            },
        }) + "\n").encode()

    def run_candidate(self, content):
        output = io.StringIO()
        with mock.patch.object(
            self.mod, "verify_configure_engine", return_value=self.checker
        ), mock.patch.object(sys, "stdin", BinaryInput(content)), contextlib.redirect_stdout(output):
            self.mod.check_candidate(self.args)
        return json.loads(output.getvalue())

    def test_valid_candidate_returns_bound_tree_and_cleans_temporary_copy(self):
        result = self.run_candidate(self.candidate())
        self.assertTrue(result["valid"])
        self.assertEqual(result["engine_commit"], ENGINE_COMMIT)
        self.assertRegex(result["tree"], r"^[0-9a-f]{40,64}$")
        self.assertEqual(
            list(self.private_root.glob(".instance-candidate-check.*")), []
        )
        original = json.loads((self.seed / "klokast-instance.json").read_text())
        self.assertEqual(original["boxes"]["boxa"]["site"], "site-b")

    def test_checker_rejection_is_expressive_and_cleans_temporary_copy(self):
        with self.assertRaisesRegex(
            self.mod.InstanceError, "candidate site was rejected"
        ):
            self.run_candidate(self.candidate(boxa="site-b", boxb="site-a"))
        self.assertEqual(
            list(self.private_root.glob(".instance-candidate-check.*")), []
        )

    def test_oversized_candidate_stops_before_temporary_copy(self):
        with self.assertRaisesRegex(self.mod.InstanceError, "larger than 64 KiB"):
            self.run_candidate(b"{" + b" " * self.mod.MAXIMUM_VALUES_SIZE + b"}")
        self.assertEqual(
            list(self.private_root.glob(".instance-candidate-check.*")), []
        )

    def test_seed_symlink_is_rejected(self):
        target = self.seed / "README.md"
        target.unlink()
        target.symlink_to("AGENTS.md")
        with self.assertRaisesRegex(self.mod.InstanceError, "symbolic link"):
            self.run_candidate(self.candidate())

    def test_required_compatibility_uses_all_private_inputs(self):
        self.args.require_compatible = True
        planned = {
            "valid": True,
            "compatible": True,
            "compatibility": {"summary": {"conflict": 0, "unsupported": 0}},
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(planned), "")
        commands = []

        original_run = self.mod.run
        def dispatch(argv, **kwargs):
            if len(argv) > 1 and argv[1] == "plan":
                commands.append(argv)
                return completed
            return original_run(argv, **kwargs)

        with mock.patch.object(self.mod, "run", side_effect=dispatch):
            result = self.run_candidate(self.candidate())
        self.assertTrue(result["compatible"])
        command = [str(item) for item in commands[0]]
        self.assertIn(str(self.mod.COMPATIBILITY_DEPLOYMENT), command)
        self.assertIn(str(self.mod.COMPATIBILITY_REGISTRY), command)
        self.assertIn(str(self.mod.COMPATIBILITY_CONTROLLER_HA), command)
        self.assertNotIn("--observation", command)

    def test_required_compatibility_refuses_conflict(self):
        self.args.require_compatible = True
        planned = {
            "valid": True,
            "compatible": False,
            "compatibility": {"summary": {"conflict": 2, "unsupported": 0}},
        }
        original_run = self.mod.run
        def dispatch(argv, **kwargs):
            if len(argv) > 1 and argv[1] == "plan":
                return subprocess.CompletedProcess(argv, 2, json.dumps(planned), "")
            return original_run(argv, **kwargs)
        with mock.patch.object(self.mod, "run", side_effect=dispatch), self.assertRaisesRegex(
            self.mod.InstanceError, "2 conflict"
        ):
            self.run_candidate(self.candidate())


if __name__ == "__main__":
    unittest.main()
