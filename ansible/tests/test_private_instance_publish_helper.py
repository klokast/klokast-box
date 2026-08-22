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
HELPER = REPO_ROOT / "klokast-dev" / "bin" / "publish-private-instance"
COMMON = REPO_ROOT / "klokast-dev" / "lib" / "approval-common.sh"
ENGINE_COMMIT = "a" * 40
BUILD_OPERATION = "b" * 12


class PrivateInstancePublishHelperTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.public = self.root / "public"
        self.public_remote = self.root / "public.git"
        self.private_remote = self.root / "private.git"
        self.fake_bin = self.root / "bin"
        self.git_config = self.root / "gitconfig"
        for directory in (self.home, self.fake_bin):
            directory.mkdir(parents=True)

        self.git("init", "-q", "--bare", "--initial-branch=main", self.public_remote)
        self.git("init", "-q", "--bare", "--initial-branch=main", self.private_remote)
        self.git("init", "-q", "--initial-branch=main", self.public)
        self.git("-C", self.public, "config", "user.name", "Klokast test")
        self.git("-C", self.public, "config", "user.email", "test@klokast.invalid")
        helper_dir = self.public / "klokast-dev" / "bin"
        library_dir = self.public / "klokast-dev" / "lib"
        helper_dir.mkdir(parents=True)
        library_dir.mkdir(parents=True)
        shutil.copy2(HELPER, helper_dir / HELPER.name)
        shutil.copy2(COMMON, library_dir / COMMON.name)
        bootstrap = helper_dir / "prepare-private-instance-bootstrap"
        bootstrap.write_text(
            f'ENGINE_COMMIT="{ENGINE_COMMIT}"\nBUILD_OPERATION="{BUILD_OPERATION}"\n',
            encoding="utf-8",
        )
        self.git("-C", self.public, "add", "-A")
        self.git("-C", self.public, "commit", "-qm", "public fixture")
        self.git("-C", self.public, "remote", "add", "origin", self.public_remote)
        self.git("-C", self.public, "push", "-qu", "origin", "main")

        self.worktree = self.home / "src" / "private-klokast" / "klokast-instance"
        self.worktree.mkdir(parents=True, mode=0o700)
        os.chmod(self.worktree.parent, 0o700)
        self.write_private_files()
        self.git("-C", self.worktree, "init", "-q", "--initial-branch=main")
        self.git("-C", self.worktree, "config", "user.name", "Trusted Human")
        self.git("-C", self.worktree, "config", "user.email", "human@example.com")
        self.git("-C", self.worktree, "add", "-A")
        self.tree = self.git("-C", self.worktree, "write-tree").stdout.strip()

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

        self.git_config.write_text(
            "\n".join([
                "[user]",
                "\tname = Trusted Human",
                "\temail = human@example.com",
                f'[url "{self.private_remote}"]',
                "\tinsteadOf = git@github.com:family/klokast-instance.git",
                "",
            ]),
            encoding="utf-8",
        )
        self.write_fake_commands()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def git(*arguments, check=True):
        return subprocess.run(
            ["git", *(str(item) for item in arguments)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def write_private_files(self):
        files = {
            ".gitignore": "*.private\n",
            "AGENTS.md": "# Private instructions\n",
            "README.md": "# Private instance\n",
            "klokast-instance.json": json.dumps({
                "$schema": (
                    "https://raw.githubusercontent.com/klokast/klokast-box/"
                    f"{ENGINE_COMMIT}/schemas/klokast-instance-v1.schema.json"
                ),
                "schema-version": 1,
                "airunners": ["k002-ops-airunner"],
            }, indent=2) + "\n",
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
        }
        for name, content in files.items():
            path = self.worktree / name
            path.write_text(content, encoding="utf-8")
            os.chmod(path, 0o600)

    def write_fake_commands(self):
        uname = self.fake_bin / "uname"
        uname.write_text("#!/bin/sh\nprintf 'Darwin\\n'\n", encoding="utf-8")
        os.chmod(uname, 0o755)
        tailscale = self.fake_bin / "tailscale"
        tailscale.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "[ \"${1:-}\" = ssh ]\n"
            "cat >/dev/null\n"
            "printf '%s\\n' \"$FAKE_CONTROLLER_TREE\"\n",
            encoding="utf-8",
        )
        os.chmod(tailscale, 0o755)

    def environment(self, controller_tree=None):
        value = os.environ.copy()
        value.update({
            "HOME": str(self.home),
            "PATH": f"{self.fake_bin}:{value['PATH']}",
            "GIT_CONFIG_GLOBAL": str(self.git_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "FAKE_CONTROLLER_TREE": controller_tree or self.tree,
        })
        return value

    def run_helper(self, answers=b"", controller_tree=None):
        master, slave = pty.openpty()
        process = subprocess.Popen(
            [str(self.public / "klokast-dev" / "bin" / HELPER.name)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=self.environment(controller_tree),
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
        returncode = process.wait(timeout=15)
        return subprocess.CompletedProcess(
            process.args, returncode, bytes(output).decode("utf-8", errors="replace"), ""
        )

    def test_help_describes_human_publication_boundary(self):
        result = subprocess.run(
            [str(HELPER), "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("trusted MacBook", result.stdout)
        self.assertIn("human Git identity", result.stdout)
        self.assertIn("does not give the controller or an airunner write access", result.stdout)

    def test_success_commits_pushes_and_verifies_main(self):
        result = self.run_helper(b"y\n")
        self.assertEqual(result.returncode, 0, result.stdout)
        local_commit = self.git("-C", self.worktree, "rev-parse", "HEAD").stdout.strip()
        remote_commit = self.git(
            "--git-dir", self.private_remote, "rev-parse", "refs/heads/main"
        ).stdout.strip()
        self.assertEqual(local_commit, remote_commit)
        self.assertEqual(
            self.git("-C", self.worktree, "config", "--get", "remote.origin.url").stdout.strip(),
            "git@github.com:family/klokast-instance.git",
        )
        self.assertIn("Private instance main is published and verified", result.stdout)

    def test_tree_mismatch_stops_before_commit_or_remote(self):
        result = self.run_helper(controller_tree="f" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the sealed controller seed", result.stdout)
        self.assertNotEqual(
            self.git("-C", self.worktree, "rev-parse", "--verify", "HEAD", check=False).returncode,
            0,
        )
        self.assertEqual(self.git("-C", self.worktree, "remote").stdout.strip(), "")

    def test_declined_review_does_not_create_commit_or_remote(self):
        result = self.run_helper(b"n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("publication was not approved", result.stdout)
        self.assertNotEqual(
            self.git("-C", self.worktree, "rev-parse", "--verify", "HEAD", check=False).returncode,
            0,
        )
        self.assertEqual(self.git("-C", self.worktree, "remote").stdout.strip(), "")

    def test_unexpected_remote_ref_stops_initial_publication(self):
        unrelated = self.root / "unrelated"
        self.git("init", "-q", "--initial-branch=main", unrelated)
        self.git("-C", unrelated, "config", "user.name", "Unrelated test")
        self.git("-C", unrelated, "config", "user.email", "test@klokast.invalid")
        (unrelated / "file").write_text("unrelated\n", encoding="utf-8")
        self.git("-C", unrelated, "add", "file")
        self.git("-C", unrelated, "commit", "-qm", "unrelated")
        self.git("-C", unrelated, "tag", "unexpected")
        self.git(
            "-C", unrelated, "push", "-q", self.private_remote,
            "refs/tags/unexpected:refs/tags/unexpected",
        )

        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repository with unexpected refs", result.stdout)
        self.assertNotEqual(
            self.git("-C", self.worktree, "rev-parse", "--verify", "HEAD", check=False).returncode,
            0,
        )

    def test_exact_local_commit_can_resume_push(self):
        self.git("-C", self.worktree, "commit", "-qm", "Initialize private Klokast instance")
        self.git(
            "-C", self.worktree, "remote", "add", "origin",
            "git@github.com:family/klokast-instance.git",
        )
        result = self.run_helper(b"y\n")
        self.assertEqual(result.returncode, 0, result.stdout)
        local_commit = self.git("-C", self.worktree, "rev-parse", "HEAD").stdout.strip()
        remote_commit = self.git(
            "--git-dir", self.private_remote, "rev-parse", "refs/heads/main"
        ).stdout.strip()
        self.assertEqual(local_commit, remote_commit)
        self.assertIn("after an earlier incomplete push", result.stdout)


if __name__ == "__main__":
    unittest.main()
