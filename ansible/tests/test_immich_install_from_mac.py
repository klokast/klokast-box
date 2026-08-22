#!/usr/bin/env python3
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "apps" / "immich" / "bin" / "immich-install-from-mac"


class ImmichInstallFromMacTest(unittest.TestCase):
    def test_dry_run_creates_0600_secret_file_without_printing_secrets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            home.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(home)
            secret_path = home / ".klokast" / "secrets" / "immich-boxa-boxb.env"
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--controller",
                    "boxb-ops",
                    "--active-master",
                    "boxa",
                    "--passive-backup",
                    "boxb",
                    "--dry-run",
                ],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            mode = stat.S_IMODE(secret_path.stat().st_mode)
            self.assertEqual(mode, 0o600)
            content = secret_path.read_text(encoding="utf-8")
            self.assertIn("IMMICH_POSTGRES_PASSWORD=", content)
            self.assertIn("IMMICH_RESTIC_PASSWORD=", content)
            self.assertIn("IMMICH_RESTIC_REPOSITORY=", content)
            output = result.stdout + result.stderr
            self.assertIn("<redacted>", output)
            self.assertIn("platform-resources --registry", output)
            self.assertIn("immichctl private-ingress-identity", output)
            self.assertIn("immichctl resource-grant-check", output)
            script = SCRIPT.read_text(encoding="utf-8")
            self.assertIn("/home/minion/.ansible/cp", script)
            self.assertIn("/home/minion/.ansible/facts", script)
            self.assertIn("ANSIBLE_LOCAL_TEMP=/home/minion/.ansible/tmp", script)
            self.assertIn("ANSIBLE_SSH_CONTROL_PATH_DIR=/home/minion/.ansible/cp", script)
            self.assertIn("ANSIBLE_CACHE_PLUGIN_CONNECTION=/home/minion/.ansible/facts", script)
            self.assertIn('doas install -o minion -g minion -m 0644 "\\$work_tmp/.klokast-approved-commit"', script)
            self.assertIn('if doas test -d "\\$app_workspace"', script)
            for value in re.findall(r"=(\S+)", content):
                self.assertNotIn(value.strip("'"), output)


if __name__ == "__main__":
    unittest.main()
