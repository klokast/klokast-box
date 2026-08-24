#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
KK = REPO_ROOT / "klokast-dev" / "bin" / "kk"


class KlokastDoctorTest(unittest.TestCase):
    def test_missing_pyyaml_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake_bin = Path(temporary)
            for name in ("tailscale", "ssh", "rsync", "ssh-keygen"):
                path = fake_bin / name
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                path.chmod(0o755)
            python = fake_bin / "python3"
            python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            python.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

            result = subprocess.run(
                [str(KK), "doctor"],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("missing: Python PyYAML module", result.stderr)
        self.assertIn("kk doctor --install", result.stderr)

    def test_pyyaml_install_is_pinned(self):
        source = KK.read_text(encoding="utf-8")
        self.assertIn('PYYAML_VERSION="6.0.3"', source)
        self.assertIn('"PyYAML==$PYYAML_VERSION"', source)
        self.assertIn("--only-binary=:all:", source)


if __name__ == "__main__":
    unittest.main()
