#!/usr/bin/env python3
import subprocess
import os
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_CHECK = REPO_ROOT / "ansible" / "bin" / "platform-check"
PLATFORM_CHECK_REMOTE = REPO_ROOT / "ansible" / "bin" / "platform-check-remote"


class PlatformCheckWrapperTest(unittest.TestCase):
    def run_command(self, argv):
        return subprocess.run(
            argv,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_platform_check_help_lists_expanded_targets(self):
        result = self.run_command([str(PLATFORM_CHECK), "--help"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("all, dom0, router, podman", result.stdout)
        self.assertIn("--resources-registry auto|none|PATH", result.stdout)
        self.assertIn("--remote-scope SCOPE", result.stdout)

    def test_platform_check_rejects_unknown_target_before_preflight(self):
        result = self.run_command(
            [str(PLATFORM_CHECK), "--box", "k001", "--target", "not-a-target"]
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported --target not-a-target", result.stderr)

    def test_remote_dry_run_supports_all_target_without_tailscale(self):
        result = self.run_command(
            [
                str(PLATFORM_CHECK_REMOTE),
                "--dry-run-plan",
                "--box",
                "k001",
                "--target",
                "all",
                "--resources-registry",
                "none",
            ]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ansible/bin/platform-check", result.stdout)
        self.assertIn("--target all", result.stdout)
        self.assertIn("--resources-registry none", result.stdout)

    def test_remote_dry_run_auto_controller_does_not_require_tailscale(self):
        result = self.run_command(
            [
                str(PLATFORM_CHECK_REMOTE),
                "--dry-run-plan",
                "--box",
                "k001",
                "--target",
                "router",
            ]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("smith@<active-controller>", result.stdout)

    def test_platform_check_preserves_failed_ansible_return_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_bin = Path(tmpdir)
            for name, body in (
                ("ansible-inventory", "#!/bin/sh\nexit 0\n"),
                ("ansible-playbook", "#!/bin/sh\nexit 7\n"),
            ):
                path = fake_bin / name
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            result = subprocess.run(
                [
                    str(PLATFORM_CHECK),
                    "--box",
                    "k001",
                    "--target",
                    "dom0",
                    "--resources-registry",
                    "none",
                    "--magicdns-suffix",
                    "example.ts.net",
                ],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("dom0 (rc=7)", result.stderr)


if __name__ == "__main__":
    unittest.main()
