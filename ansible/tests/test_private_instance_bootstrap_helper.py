#!/usr/bin/env python3
import errno
import os
import pty
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "klokast-dev" / "bin" / "prepare-private-instance-bootstrap"
COMMON = REPO_ROOT / "klokast-dev" / "lib" / "approval-common.sh"


class PrivateInstanceBootstrapHelperTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.publisher = self.root / "publisher"
        self.client = self.root / "client"
        self.home = self.root / "home"
        self.fake_bin = self.root / "bin"
        self.home.mkdir()
        self.fake_bin.mkdir()

        self.git("init", "-q", "--bare", "--initial-branch=main", self.remote)
        self.git("init", "-q", "--initial-branch=main", self.publisher)
        self.git("-C", self.publisher, "config", "user.name", "Klokast test")
        self.git(
            "-C", self.publisher, "config", "user.email", "test@klokast.invalid"
        )
        helper_dir = self.publisher / "klokast-dev" / "bin"
        library_dir = self.publisher / "klokast-dev" / "lib"
        helper_dir.mkdir(parents=True)
        library_dir.mkdir(parents=True)
        shutil.copy2(HELPER, helper_dir / HELPER.name)
        shutil.copy2(COMMON, library_dir / COMMON.name)
        self.git("-C", self.publisher, "add", "-A")
        self.git("-C", self.publisher, "commit", "-qm", "helper A")
        self.git("-C", self.publisher, "remote", "add", "origin", self.remote)
        self.git("-C", self.publisher, "push", "-qu", "origin", "main")
        self.git("clone", "-q", "--branch", "main", self.remote, self.client)
        self.commit_a = self.head(self.client)

        uname = self.fake_bin / "uname"
        uname.write_text("#!/bin/sh\nprintf 'Darwin\\n'\n", encoding="utf-8")
        os.chmod(uname, 0o755)

        updated = helper_dir / HELPER.name
        updated.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'updated helper executed\\n'\n"
            "printf 'restart count: %s\\n' "
            '"${KLOKAST_PRIVATE_BOOTSTRAP_REEXEC_COUNT:-missing}"\n',
            encoding="utf-8",
        )
        os.chmod(updated, 0o755)
        self.git("-C", self.publisher, "add", "-A")
        self.git("-C", self.publisher, "commit", "-qm", "helper B")
        self.git("-C", self.publisher, "push", "-q", "origin", "main")
        self.commit_b = self.head(self.publisher)

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

    def head(self, repository):
        return self.git("-C", repository, "rev-parse", "HEAD").stdout.strip()

    def run_helper(self, extra_environment=None):
        environment = os.environ.copy()
        environment.update({
            "HOME": str(self.home),
            "PATH": f"{self.fake_bin}:{environment['PATH']}",
        })
        if extra_environment:
            environment.update(extra_environment)
        master, slave = pty.openpty()
        process = subprocess.Popen(
            [str(self.client / "klokast-dev" / "bin" / HELPER.name)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=environment,
            close_fds=True,
        )
        os.close(slave)
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
            process.args,
            returncode,
            bytes(output).decode("utf-8", errors="replace"),
            "",
        )

    def test_update_restarts_into_the_new_helper_before_any_prompt(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.head(self.client), self.commit_b)
        self.assertIn("Updated the helper checkout.", result.stdout)
        self.assertIn("Restarting the updated helper", result.stdout)
        self.assertIn("updated helper executed", result.stdout)
        self.assertIn("restart count: 1", result.stdout)
        self.assertNotIn("Active controller [", result.stdout)
        self.assertNotIn("Private GitHub organization:", result.stdout)

    def test_dirty_checkout_stops_before_pull(self):
        (self.client / "untracked").write_text("change\n", encoding="utf-8")
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.head(self.client), self.commit_a)
        self.assertIn("checkout has changes", result.stdout)
        self.assertNotIn("updated helper executed", result.stdout)

    def test_second_update_stops_instead_of_running_mixed_code(self):
        result = self.run_helper({"KLOKAST_PRIVATE_BOOTSTRAP_REEXEC_COUNT": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.head(self.client), self.commit_b)
        self.assertIn("checkout changed again during the automatic restart", result.stdout)
        self.assertIn("no bootstrap action ran", result.stdout)
        self.assertNotIn("updated helper executed", result.stdout)

    def test_help_does_not_update_the_checkout(self):
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        result = subprocess.run(
            [str(self.client / "klokast-dev" / "bin" / HELPER.name), "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.head(self.client), self.commit_a)
        self.assertIn("Private instance bootstrap prerequisites", result.stdout)
        self.assertNotIn("updated helper executed", result.stdout)


if __name__ == "__main__":
    unittest.main()
