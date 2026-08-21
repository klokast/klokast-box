#!/usr/bin/env python3
import errno
import json
import os
import pty
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "klokast-dev" / "bin" / "prepare-private-instance-worktree"
COMMON = REPO_ROOT / "klokast-dev" / "lib" / "approval-common.sh"
ENGINE_COMMIT = "a" * 40
BUILD_OPERATION = "b" * 12


class PrivateInstanceWorktreeHelperTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.repo = self.root / "repo"
        self.remote = self.root / "remote"
        self.fake_bin = self.root / "bin"
        for directory in (self.home, self.remote, self.fake_bin):
            directory.mkdir(parents=True)

        helper_dir = self.repo / "klokast-dev" / "bin"
        library_dir = self.repo / "klokast-dev" / "lib"
        helper_dir.mkdir(parents=True)
        library_dir.mkdir(parents=True)
        shutil.copy2(HELPER, helper_dir / HELPER.name)
        shutil.copy2(COMMON, library_dir / COMMON.name)
        (helper_dir / "prepare-private-instance-bootstrap").write_text(
            f'ENGINE_COMMIT="{ENGINE_COMMIT}"\nBUILD_OPERATION="{BUILD_OPERATION}"\n',
            encoding="utf-8",
        )
        os.chmod(helper_dir / HELPER.name, 0o755)

        self.run_git(self.repo, "init", "-q", "--initial-branch=main")
        self.run_git(self.repo, "config", "user.name", "Klokast test")
        self.run_git(self.repo, "config", "user.email", "test@klokast.invalid")
        self.run_git(self.repo, "add", "-A")
        self.run_git(self.repo, "commit", "-qm", "test fixture")

        work = self.home / ".local" / "share" / "klokast" / "private-instance-bootstrap"
        work.mkdir(parents=True, mode=0o700)
        session = work / "session.sh"
        session.write_text(
            "\n".join([
                "CONTROLLER=k002-ops",
                "SSH_TARGET=smith@k002-ops",
                "INSTANCE_OWNER=family",
                "INSTANCE_REPO=klokast-instance",
                "SIGNER_ID=human-private-instance",
                "APPROVAL_PURPOSE=private-instance",
                f"ENGINE_COMMIT={ENGINE_COMMIT}",
                f"BUILD_OPERATION={BUILD_OPERATION}",
                f"BOOTSTRAP_WORK={work}",
                "",
            ]),
            encoding="utf-8",
        )
        os.chmod(session, 0o600)
        self.audit_log = work / "action-audit.jsonl"

        self.make_generated_seed()
        self.write_fake_commands()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def run_git(root, *arguments, check=True):
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def make_generated_seed(self):
        seed = self.remote / "instance-seed"
        seed.mkdir(parents=True)
        files = {
            ".gitignore": "*.private\n",
            "AGENTS.md": "# Private instructions\n",
            "README.md": "# Private instance\n",
            "klokast.lock.json": json.dumps({
                "$schema": (
                    "https://raw.githubusercontent.com/klokast/klokast-box/"
                    f"{ENGINE_COMMIT}/schemas/klokast-lock-v1.schema.json"
                ),
                "schema-version": 1,
                "engine": {
                    "repository": "https://github.com/klokast/klokast-box",
                    "ref": "main",
                    "commit": ENGINE_COMMIT,
                },
            }, indent=2) + "\n",
            "klokast-instance.json": json.dumps({
                "$schema": (
                    "https://raw.githubusercontent.com/klokast/klokast-box/"
                    f"{ENGINE_COMMIT}/schemas/klokast-instance-v1.schema.json"
                ),
                "schema-version": 1,
            }, indent=2) + "\n",
        }
        for relative, content in files.items():
            (seed / relative).write_text(content, encoding="utf-8")
        self.run_git(seed, "init", "-q", "--initial-branch=main")
        self.run_git(seed, "add", "-A")

    def write_fake_commands(self):
        uname = self.fake_bin / "uname"
        uname.write_text("#!/bin/sh\nprintf 'Darwin\\n'\n", encoding="utf-8")
        os.chmod(uname, 0o755)

        tailscale = self.fake_bin / "tailscale"
        tailscale.write_text(
            r'''#!/usr/bin/env bash
set -euo pipefail
[ "${1:-}" = ssh ]
shift
target=${1:-}
shift
[ "$target" = smith@k002-ops ]
if [ "${1:-}" = -t ]; then
  [ "$#" -eq 2 ]
  case "${2:-}" in
    *"exec vi '/home/smith/private/klokast/init-values.json'"*) exit 0 ;;
    *) exit 2 ;;
  esac
fi
if [ "${1:-}" != sh ]; then
  exit 0
fi
payload=$(mktemp)
trap 'rm -f "$payload"' EXIT
cat >"$payload"
if grep -q WORKTREE_VALUES_PREFLIGHT "$payload"; then
  if [ "${FAKE_PREFLIGHT_FAIL:-0}" = 1 ]; then
    printf 'the staged destination already exists; do not overwrite it\n' >&2
    exit 1
  fi
  operation=${8:-}
  case "$operation" in
    prepare) printf 'created\n' ;;
    check) printf 'checked\n' ;;
    *) exit 2 ;;
  esac
elif grep -q WORKTREE_SEED_OPERATION "$payload"; then
  [ "${FAKE_SEED_FAIL:-0}" != 1 ] || exit 1
elif grep -q WORKTREE_SEED_STATE "$payload"; then
  printf 'present\n'
elif grep -q WORKTREE_VERIFY_EXISTING_SEED "$payload"; then
  printf 'verified\n'
elif grep -q WORKTREE_STREAM_OPERATION "$payload"; then
  tar -C "$FAKE_REMOTE_ROOT" -cf - instance-seed
elif grep -q 'invalid controller archive operation' "$payload"; then
  operation=${8:-}
  case "$operation" in
    check) printf 'present\n' ;;
    move) printf '/home/smith/private/klokast/archive\n' ;;
    *) exit 2 ;;
  esac
else
  exit 2
fi
''',
            encoding="utf-8",
        )
        os.chmod(tailscale, 0o755)

    def run_helper(self, answers=b"", extra_environment=None, arguments=None):
        environment = os.environ.copy()
        environment.update({
            "HOME": str(self.home),
            "PATH": f"{self.fake_bin}:{environment['PATH']}",
            "FAKE_REMOTE_ROOT": str(self.remote),
        })
        if extra_environment:
            environment.update(extra_environment)
        master, slave = pty.openpty()
        process = subprocess.Popen(
            [str(self.repo / "klokast-dev" / "bin" / HELPER.name), *(arguments or [])],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=environment,
            close_fds=True,
        )
        os.close(slave)
        if answers:
            os.write(master, answers)
        output = bytearray()
        while True:
            try:
                chunk = os.read(master, 8192)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            output.extend(chunk)
        os.close(master)
        returncode = process.wait(timeout=10)
        return subprocess.CompletedProcess(
            process.args, returncode, bytes(output).decode("utf-8", errors="replace"), ""
        )

    def read_audit(self):
        return [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
        ]

    def test_help_describes_the_human_boundary(self):
        result = subprocess.run(
            [str(HELPER), "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("staged repository with the exact sealed build", result.stdout)
        self.assertIn("does not display the\nprivate values", result.stdout)
        self.assertIn("create a commit, add a remote, or", result.stdout)
        self.assertIn("--resume-transfer", result.stdout)
        self.assertIn("--archive-and-restart", result.stdout)

    def test_success_transfers_and_verifies_the_unborn_repository(self):
        result = self.run_helper(b"y\ny\n")
        self.assertEqual(result.returncode, 0, result.stdout)
        worktree = self.home / "src" / "private-klokast" / "klokast-instance"
        self.assertTrue((worktree / ".git").is_dir())
        self.assertNotEqual(
            self.run_git(worktree, "rev-parse", "--verify", "HEAD", check=False).returncode,
            0,
        )
        self.assertEqual(self.run_git(worktree, "remote").stdout.strip(), "")
        record = self.read_audit()[-1]
        self.assertEqual(record["event"], "private-instance.worktree-preparation.finished")
        self.assertEqual(record["outcome"], "success")
        self.assertTrue(record["seed_created"])
        self.assertTrue(record["worktree_created"])
        self.assertEqual(record["values_file"], "created")
        self.assertEqual(record["engine_commit"], ENGINE_COMMIT)
        self.assertNotIn("REPLACE_WITH", json.dumps(record))

    def test_declining_seed_is_a_redacted_cancellation(self):
        result = self.run_helper(b"n\n")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.home / "src" / "private-klokast" / "klokast-instance").exists())
        record = self.read_audit()[-1]
        self.assertEqual(record["outcome"], "cancelled")
        self.assertFalse(record["seed_created"])
        self.assertFalse(record["worktree_created"])

    def test_controller_preflight_failure_reports_safe_recovery(self):
        result = self.run_helper(extra_environment={"FAKE_PREFLIGHT_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("do not overwrite it", result.stdout)
        self.assertIn("No seed or worktree was created", result.stdout)
        record = self.read_audit()[-1]
        self.assertEqual(record["outcome"], "failure")
        self.assertEqual(record["phase"], "controller-preflight")

    def test_failed_seed_reports_a_completed_controller_destination(self):
        result = self.run_helper(b"y\n", {"FAKE_SEED_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Controller seed created: true", result.stdout)
        self.assertIn("Do not delete or overwrite it", result.stdout)
        record = self.read_audit()[-1]
        self.assertEqual(record["outcome"], "failure")
        self.assertTrue(record["seed_created"])
        self.assertFalse(record["worktree_created"])

    def test_resume_transfer_requires_audit_and_reuses_verified_seed(self):
        repository_hash = __import__("hashlib").sha256(
            b"family/klokast-instance"
        ).hexdigest()
        self.audit_log.write_text(json.dumps({
            "event": "private-instance.worktree-preparation.finished",
            "controller": "k002-ops",
            "repository_sha256": repository_hash,
            "repo_head": ENGINE_COMMIT,
            "build_operation": BUILD_OPERATION,
            "outcome": "failure",
            "phase": "validate-transferred-seed",
            "values_file": "reused",
            "seed_created": True,
            "worktree_created": False,
        }) + "\n", encoding="utf-8")
        os.chmod(self.audit_log, 0o600)
        result = self.run_helper(b"y\n", arguments=["--resume-transfer"])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("existing controller seed passed sealed verification", result.stdout)
        worktree = self.home / "src" / "private-klokast" / "klokast-instance"
        self.assertTrue((worktree / ".git").is_dir())
        self.assertEqual(self.read_audit()[-1]["outcome"], "success")

    def test_resume_transfer_refuses_missing_audit_evidence(self):
        result = self.run_helper(arguments=["--resume-transfer"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no matching incomplete seed-transfer audit record", result.stdout)
        self.assertFalse(
            (self.home / "src" / "private-klokast" / "klokast-instance").exists()
        )

    def test_archive_and_restart_preserves_the_earlier_worktree(self):
        parent = self.home / "src" / "private-klokast"
        parent.mkdir(parents=True, mode=0o700)
        worktree = parent / "klokast-instance"
        shutil.copytree(self.remote / "instance-seed", worktree)
        os.chmod(worktree, 0o700)
        result = self.run_helper(b"y\ny\ny\n", arguments=["--archive-and-restart"])
        self.assertEqual(result.returncode, 0, result.stdout)
        archives = list((parent / "archive").glob("klokast-instance.*"))
        self.assertEqual(len(archives), 1)
        self.assertTrue((archives[0] / ".git").is_dir())
        self.assertTrue((worktree / ".git").is_dir())
        record = self.read_audit()[-1]
        self.assertEqual(record["mode"], "archive-and-restart")
        self.assertTrue(record["archive_performed"])

    def test_source_keeps_fixed_paths_and_redacted_transfer(self):
        source = HELPER.read_text(encoding="utf-8")
        self.assertIn('CONTROLLER_VALUES="/home/smith/private/klokast/init-values.json"', source)
        self.assertIn('CONTROLLER_SEED="/home/smith/private/klokast/instance-seed"', source)
        self.assertIn('stream_controller_seed | tar -xf - -C "$transfer_work"', source)
        self.assertIn('tailscale ssh "$SSH_TARGET" -t', source)
        self.assertIn("private-instance.worktree-preparation.finished", source)
        self.assertIn("archive_controller_inputs", source)
        self.assertNotIn("git commit", source)
        self.assertNotIn("git remote add", source)
        self.assertNotIn("git push", source)

    def test_runbook_uses_helper_and_keeps_instance_review_manual(self):
        runbook = (
            REPO_ROOT / "klokast-dev" / "runbooks" /
            "40-private-instance-bootstrap.md"
        ).read_text(encoding="utf-8")
        self.assertIn("klokast-dev/bin/prepare-private-instance-worktree", runbook)
        self.assertIn("## 10. Review the private repository", runbook)
        self.assertIn("git commit -m \"Initialize private Klokast instance\"", runbook)
        self.assertIn("--archive-and-restart", runbook)


if __name__ == "__main__":
    unittest.main()
