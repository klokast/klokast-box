#!/usr/bin/env python3
import argparse
import datetime as dt
import importlib.util
import io
import json
import os
import pwd
import stat
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "klokast-ops" / "secret-authority" / "bin" / "ksa-instance"
OLD_COMMIT = "a" * 40
NEW_COMMIT = "b" * 40


def load_module():
    loader = SourceFileLoader("ksa_engine_promotion_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class BinaryInput:
    def __init__(self, content):
        self.buffer = io.BytesIO(content)


class EnginePromotionTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "instance"
        self.checkout.mkdir(mode=0o700)
        self.make_private_checkout()
        self.args = argparse.Namespace(
            checkout=str(self.checkout),
            infra_user=pwd.getpwuid(os.getuid()).pw_name,
            activation_root=str(self.root / "activations"),
        )
        self.checker = self.root / "klokast"
        self.checker.write_text(
            "#!/bin/sh\nprintf '{\"valid\":true}\\n'\n",
            encoding="utf-8",
        )
        self.checker.chmod(0o755)
        self.old_build = {"binary_path": self.checker}
        self.new_build = {"binary_path": self.checker}

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def git(root, *arguments):
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments], text=True
        ).strip()

    @staticmethod
    def schema(commit, name):
        return (
            "https://raw.githubusercontent.com/klokast/klokast-box/"
            f"{commit}/schemas/{name}"
        )

    def make_private_checkout(self):
        files = {
            ".gitignore": "*.private\n",
            "AGENTS.md": "# Private instructions\n",
            "README.md": "# Private instance\n",
            "klokast-instance.json": json.dumps({
                "$schema": self.schema(OLD_COMMIT, "klokast-instance-v1.schema.json"),
                "schema-version": 1,
                "airunners": ["boxa-ops-airunner"],
            }, indent=2, sort_keys=True) + "\n",
            "klokast.lock.json": json.dumps({
                "$schema": self.schema(OLD_COMMIT, "klokast-lock-v1.schema.json"),
                "schema-version": 1,
                "engine": {
                    "repository": "https://github.com/klokast/klokast-box",
                    "ref": "main",
                    "commit": OLD_COMMIT,
                },
            }, indent=2, sort_keys=True) + "\n",
        }
        for name, content in files.items():
            (self.checkout / name).write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", self.checkout, "init", "-q", "--initial-branch=main"], check=True)
        subprocess.run(["git", "-C", self.checkout, "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", self.checkout, "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", self.checkout, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.checkout, "commit", "-qm", "initial"], check=True)

    def candidate_envelope(self, change_private_value=False):
        instance = json.loads((self.checkout / "klokast-instance.json").read_text())
        lock = json.loads((self.checkout / "klokast.lock.json").read_text())
        instance["$schema"] = self.schema(NEW_COMMIT, "klokast-instance-v1.schema.json")
        lock["$schema"] = self.schema(NEW_COMMIT, "klokast-lock-v1.schema.json")
        lock["engine"]["commit"] = NEW_COMMIT
        if change_private_value:
            instance["airunners"] = ["boxb-ops-airunner"]
        instance_content = json.dumps(instance, indent=2, sort_keys=True) + "\n"
        lock_content = json.dumps(lock, indent=2, sort_keys=True) + "\n"
        candidate = self.root / ("changed" if change_private_value else "candidate")
        candidate.mkdir()
        for name in (".gitignore", "AGENTS.md", "README.md"):
            (candidate / name).write_bytes((self.checkout / name).read_bytes())
        (candidate / "klokast-instance.json").write_text(instance_content)
        (candidate / "klokast.lock.json").write_text(lock_content)
        subprocess.run(["git", "-C", candidate, "init", "-q", "--initial-branch=main"], check=True)
        subprocess.run(["git", "-C", candidate, "add", "-A"], check=True)
        return {
            "schema_version": 1,
            "action": "promote-engine",
            "engine_repository": "https://github.com/klokast/klokast-box",
            "engine_ref": "main",
            "old_engine_commit": OLD_COMMIT,
            "new_engine_commit": NEW_COMMIT,
            "private_base_commit": self.git(self.checkout, "rev-parse", "HEAD"),
            "private_base_tree": self.git(self.checkout, "rev-parse", "HEAD^{tree}"),
            "candidate_tree": self.git(candidate, "write-tree"),
            "rollback_tree": self.git(self.checkout, "rev-parse", "HEAD^{tree}"),
            "candidate_instance_json": instance_content,
            "candidate_lock_json": lock_content,
        }

    def test_candidate_allows_only_three_engine_metadata_values(self):
        envelope = self.candidate_envelope()
        commit, tree = self.mod.validate_candidate_tree(
            self.args, envelope, self.old_build, self.new_build
        )
        self.assertEqual(commit, envelope["private_base_commit"])
        self.assertEqual(tree, envelope["private_base_tree"])
        self.assertEqual(list(self.root.glob(".engine-promotion.*")), [])

    def test_candidate_rejects_a_private_intent_change(self):
        envelope = self.candidate_envelope(change_private_value=True)
        with self.assertRaisesRegex(
            self.mod.InstanceAuthorityError, "outside the three permitted"
        ):
            self.mod.validate_candidate_tree(
                self.args, envelope, self.old_build, self.new_build
            )

    def test_bounded_envelope_rejects_unknown_fields(self):
        envelope = self.candidate_envelope()
        envelope["unexpected"] = True
        content = json.dumps(envelope).encode()
        with mock.patch.object(sys, "stdin", BinaryInput(content)), self.assertRaisesRegex(
            self.mod.InstanceAuthorityError, "closed schema"
        ):
            self.mod.read_promotion_envelope()

    def test_intent_lifetime_is_limited_to_ten_minutes(self):
        issued = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        intent = {
            "schema_version": 1,
            "authority": "klokast-secret-authority",
            "app": "instance",
            "action": "promote-engine",
            "engine_repository": "https://github.com/klokast/klokast-box",
            "engine_ref": "main",
            "old_engine_commit": OLD_COMMIT,
            "new_engine_commit": NEW_COMMIT,
            "old_build_operation": "c" * 12,
            "new_build_operation": "d" * 12,
            "old_binary_sha256": "e" * 64,
            "new_binary_sha256": "f" * 64,
            "old_builder_receipt_sha256": "1" * 64,
            "new_builder_receipt_sha256": "2" * 64,
            "controller_public_commit": NEW_COMMIT,
            "private_repository_sha256": "3" * 64,
            "private_repository_id": 42,
            "private_base_commit": "4" * 40,
            "private_base_tree": "5" * 40,
            "candidate_tree": "6" * 40,
            "rollback_tree": "5" * 40,
            "source_receipt_sha256": "7" * 64,
            "signer_id": "human-private-instance",
            "nonce": "nonce_123456789",
            "issued_at": self.mod.format_utc(issued),
            "expires_at": self.mod.format_utc(issued + dt.timedelta(minutes=10)),
        }
        self.mod.validate_promotion_intent(intent)
        intent["expires_at"] = self.mod.format_utc(issued + dt.timedelta(minutes=10, seconds=1))
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, "exceeds 10 minutes"):
            self.mod.validate_promotion_intent(intent)

    def test_immutable_receipt_has_required_hash_and_modes(self):
        receipt = {"schema_version": 1, "kind": self.mod.PROMOTION_KIND, "value": "bound"}
        with mock.patch.object(self.mod.os, "chown"):
            path, complete = self.mod.write_group_receipt(
                self.args, self.root / "promotions", NEW_COMMIT, receipt
            )
        self.assertEqual(path.name, f"{complete['receipt_sha256']}.json")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o440)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o750)
        self.assertEqual(json.loads(path.read_text()), complete)


if __name__ == "__main__":
    unittest.main()
