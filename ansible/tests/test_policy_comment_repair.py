import argparse
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "maintenance/repair-policy-comments-20260911.py"
spec = importlib.util.spec_from_file_location("comment_repair", SCRIPT)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class FakeInstalledApply:
    KIND_PLAN_V8 = "klokast.plan.v8"
    RENDERER = "renderer"
    MUTATION_HELPER = "broker"
    POLICY_TEMPLATE = "installed-template"
    INSTANCE = Path("instance")

    def __init__(self, root):
        self.root = root
        self.work = root / "runtime"
        self.live = b'{\n"groups": {\n' + b''.join(repair.OLD) + b'"rules": []\n}}\n'
        self.candidate = b'{\n"groups": {\n' + b''.join(repair.NEW) + b'"rules": []\n}}\n'
        self.calls = []
        self.checks = 0
        self.change_at = None
        self.fail_operation = None
        self.conflict = False
        self.bad_after = False

    def validate_inputs_v3(self, args):
        self.checks += 1
        return {"plan": {"kind": self.KIND_PLAN_V8,
                         "engine": {"commit": repair.ENGINE}, "plan_sha256": "test"},
                "state": {"authority_state_sha256": repair.STATE},
                "changed": self.change_at is not None and self.checks >= self.change_at}

    def new_work(self, nonce):
        self.work.mkdir()
        return self.work

    def run(self, command, capture):
        operation = command[1]
        self.calls.append(operation)
        if operation == self.fail_operation:
            raise RuntimeError("injected helper failure")
        if command[0] == self.RENDERER:
            (self.work / "candidate.body").write_bytes(self.candidate)
        elif operation == "get-before":
            (self.work / "live.body").write_bytes(self.live)
            (self.work / "live.etag").write_bytes(
                b'changed' if self.conflict and self.calls.count(operation) > 1 else b'original')
        elif operation == "post-candidate":
            self.live = self.candidate
        elif operation == "get-after":
            (self.work / "after.body").write_bytes(b'wrong' if self.bad_after else self.live)
            (self.work / "after.etag").write_bytes(b'new')

    @staticmethod
    def read_regular(path, limit):
        return path.read_bytes()

    @staticmethod
    def sha256_bytes(value):
        return hashlib.sha256(value).hexdigest()


class CommentRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.m = FakeInstalledApply(self.root)
        self.args = argparse.Namespace(plan="protected-plan")
        self.archive = self.root / "archive"
        for key, value in {"ARCHIVE": self.archive,
                           "BEFORE": self.m.sha256_bytes(self.m.live),
                           "AFTER": self.m.sha256_bytes(self.m.candidate)}.items():
            p = patch.object(repair, key, value)
            p.start()
            self.addCleanup(p.stop)

    def test_prepare_does_not_post(self):
        self.assertEqual(repair.repair(self.m, self.args, False)["result"], "prepared")
        self.assertNotIn("post-candidate", self.m.calls)
        self.assertFalse(self.archive.exists())
        self.assertFalse(self.m.work.exists())

    def test_success_and_replay_refusal(self):
        old = self.m.live
        self.assertEqual(repair.repair(self.m, self.args, True)["result"], "verified")
        self.assertEqual((self.archive / "preimage.body").read_bytes(), old)
        self.assertEqual((self.archive / "result.json").stat().st_mode & 0o777, 0o400)
        self.m.live = old  # Even if live bytes return, the attempt cannot repeat.
        with self.assertRaises(FileExistsError):
            repair.repair(self.m, self.args, True)
        self.assertEqual(self.m.calls.count("post-candidate"), 1)
        self.assertFalse(self.m.work.exists())

    def test_rule_change_refused_even_with_expected_hash(self):
        self.m.candidate += b'// extra\n'
        with patch.object(repair, "AFTER", self.m.sha256_bytes(self.m.candidate)):
            with self.assertRaisesRegex(ValueError, "two reviewed"):
                repair.repair(self.m, self.args, True)
        self.assertNotIn("post-candidate", self.m.calls)

    def test_changed_hash_refused(self):
        self.m.live += b'\n'
        with self.assertRaisesRegex(ValueError, "hashes differ"):
            repair.repair(self.m, self.args, True)
        self.assertNotIn("post-candidate", self.m.calls)

    def test_etag_change_refused(self):
        self.m.conflict = True
        with self.assertRaisesRegex(ValueError, "ETag"):
            repair.repair(self.m, self.args, True)
        self.assertNotIn("post-candidate", self.m.calls)

    def test_changed_source_refused(self):
        for check in (2, 3):
            with self.subTest(check=check):
                self.m.checks = 0
                self.m.change_at = check
                with self.assertRaisesRegex(ValueError, "inputs changed|inputs changed before"):
                    repair.repair(self.m, self.args, True)
                self.assertNotIn("post-candidate", self.m.calls)

    def test_validation_failure_does_not_post(self):
        self.m.fail_operation = "validate-candidate"
        with self.assertRaises(RuntimeError):
            repair.repair(self.m, self.args, True)
        self.assertNotIn("post-candidate", self.m.calls)

    def test_preimage_storage_failure_does_not_post(self):
        with patch.object(repair, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                repair.repair(self.m, self.args, True)
        self.assertNotIn("post-candidate", self.m.calls)
        self.assertTrue(self.archive.exists())
        self.assertFalse(self.m.work.exists())

    def test_uncertain_post_retains_evidence_without_retry(self):
        self.m.fail_operation = "post-candidate"
        with self.assertRaises(RuntimeError):
            repair.repair(self.m, self.args, True)
        self.assertIn('"result": "inspection_required"', (self.archive / "result.json").read_text())
        self.assertTrue((self.archive / "preimage.body").exists())
        self.assertEqual(self.m.calls.count("post-candidate"), 1)
        self.assertFalse(self.m.work.exists())

    def test_after_mismatch_requires_inspection(self):
        self.m.bad_after = True
        with self.assertRaisesRegex(ValueError, "post-write"):
            repair.repair(self.m, self.args, True)
        self.assertIn('"result": "inspection_required"', (self.archive / "result.json").read_text())

    def test_post_write_source_change_requires_inspection(self):
        self.m.change_at = 4
        with self.assertRaisesRegex(ValueError, "post-write"):
            repair.repair(self.m, self.args, True)
        self.assertNotIn("post-preimage", self.m.calls)


if __name__ == "__main__":
    unittest.main()
