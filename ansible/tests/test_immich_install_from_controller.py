#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "apps" / "immich" / "bin" / "immich-install-from-controller"


class ImmichInstallFromControllerTest(unittest.TestCase):
    def test_dry_run_is_controller_local_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            home.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(home)
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--active-master",
                    "k001",
                    "--passive-backup",
                    "k002",
                    "--dry-run",
                ],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Repository:", output)
        self.assertIn("Registry:", output)
        self.assertIn("Secrets:", output)
        self.assertIn("Restic repository:", output)
        self.assertIn("doas -u minion", script)
        self.assertIn("/home/minion/.ansible/cp", script)
        self.assertIn("/home/minion/.ansible/facts", script)
        self.assertIn("ANSIBLE_LOCAL_TEMP=/home/minion/.ansible/tmp", script)
        self.assertIn("ANSIBLE_SSH_CONTROL_PATH_DIR=/home/minion/.ansible/cp", script)
        self.assertIn("ANSIBLE_CACHE_PLUGIN_CONNECTION=/home/minion/.ansible/facts", script)
        self.assertIn('doas install -o minion -g minion -m 0644 "$work_tmp/.klokast-approved-commit"', script)
        self.assertIn('if doas test -d "$app_workspace"', script)
        self.assertNotIn("tailscale ssh", script)
        self.assertFalse((home / "private").exists())


if __name__ == "__main__":
    unittest.main()
