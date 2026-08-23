#!/usr/bin/env python3
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "klokast-dev" / "bin" / "install-controller-ha-config"
SOURCE = HELPER.read_text(encoding="utf-8")


class InstallControllerHAConfigTest(unittest.TestCase):
    def test_help_names_scope_and_required_inputs(self):
        result = subprocess.run(
            [str(HELPER), "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("migration-only", result.stdout)
        self.assertIn("--candidate PATH --controller HOST", result.stdout)

    def test_install_is_interactive_atomic_and_has_both_rollbacks(self):
        self.assertIn("[ -t 0 ] && [ -t 1 ]", SOURCE)
        self.assertIn("install transitional controller HA config", SOURCE)
        self.assertIn('install -m 0600 "$candidate" "$local_tmp"', SOURCE)
        self.assertIn('mv "$local_tmp" "$DESTINATION"', SOURCE)
        self.assertIn('install -m 0600 "$DESTINATION" "$DESTINATION.previous"', SOURCE)
        self.assertIn("rollback()", SOURCE)
        self.assertGreaterEqual(SOURCE.count("the MacBook file was restored"), 2)

    def test_controller_is_explicit_and_live_marker_is_required(self):
        self.assertNotIn("resolve-active", SOURCE)
        self.assertIn("--require-active --status --json", SOURCE)
        self.assertIn('value.get("hostname") != expected', SOURCE)


if __name__ == "__main__":
    unittest.main()
