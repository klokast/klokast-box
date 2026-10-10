#!/usr/bin/env python3
import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
KK = REPO_ROOT / "klokast-dev" / "bin" / "kk"


class KlokastDoctorTest(unittest.TestCase):
    def test_unconfigured_app_commands_fail_without_external_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake_bin = Path(temporary)
            marker = fake_bin / 'remote-called'
            for name in ('tailscale', 'ssh', 'rsync', 'open', 'sleep'):
                tool = fake_bin / name
                tool.write_text('#!/bin/sh\nprintf called > "${KLOKAST_TEST_MARKER}"\nexit 99\n')
                tool.chmod(0o755)
            environment = os.environ.copy()
            environment['PATH'] = f"{fake_bin}:{environment['PATH']}"
            environment['KLOKAST_TEST_MARKER'] = str(marker)
            environment.pop('KLOKAST_INSTANCE', None)
            for args in (['music', 'upload', '--from', temporary, '--to', 'boxb'],
                         ['torrent', 'open', '--to', 'boxb'],
                         ['torrent', 'status', '--to', 'boxb']):
                with self.subTest(args=args):
                    result = subprocess.run([str(KK), *args], env=environment,
                                            capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('select a private Instance worktree', result.stderr)
                    self.assertFalse(marker.exists())

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
        self.assertNotIn("Direct MacBook software dependencies", result.stdout)

    def test_success_prints_direct_dependency_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake_bin = Path(temporary)
            for name in (
                "python3",
                "rsync",
                "ssh",
                "ssh-keygen",
                "tailscale",
            ):
                path = fake_bin / name
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                path.chmod(0o755)
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

        self.assertEqual(result.returncode, 0, result.stderr)
        expected_entries = (
            "- macOS base utilities | Apple-provided | Run file operations, archives, and proxy checks.",
            "- Bash | Apple-provided | Run MacBook Bash scripts.",
            "- Git | Apple Command Line Tools | Synchronize checkouts and publish private-instance repositories.",
            "- Apple OpenSSH and CryptoTokenKit | Apple-provided | Provide SSH transport and Touch ID approval signatures.",
            "- rsync | Apple-provided | Upload music libraries.",
            "- Tailscale | Standalone vendor .pkg | Provide Tailnet access and dispatch commands to the controller.",
            "- Homebrew | Official Homebrew installer | Install Python when needed during MacBook setup.",
            "- Python 3 | Homebrew package | Process JSON and configuration data in MacBook helpers.",
            "- PyYAML | PyPI binary wheel | Parse controller HA and private-instance YAML.",
        )
        positions = [result.stdout.index(entry) for entry in expected_entries]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Direct MacBook software dependencies", result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith("kk doctor ok"))

    def test_pyyaml_install_is_pinned(self):
        source = KK.read_text(encoding="utf-8")
        self.assertIn('PYYAML_VERSION="6.0.3"', source)
        self.assertIn('"PyYAML==$PYYAML_VERSION"', source)
        self.assertIn("--only-binary=:all:", source)


class MacBookBashTest(unittest.TestCase):
    def test_doctor_never_requests_or_installs_homebrew_bash(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary)
            marker = tools / 'brew-called'
            for name in ('python3', 'tailscale', 'ssh', 'rsync', 'ssh-keygen'):
                path = tools / name
                path.write_text('#!/bin/sh\nexit 0\n')
                path.chmod(0o755)
            for name, content in (
                ('uname', '#!/bin/sh\nprintf "Darwin\\n"\n'),
                ('brew', '#!/bin/sh\ntouch "$KLOKAST_TEST_BREW_MARKER"\nexit 99\n'),
            ):
                path = tools / name
                path.write_text(content)
                path.chmod(0o755)
            environment = dict(os.environ, PATH=f'{tools}:{os.defpath}',
                               KLOKAST_TEST_BREW_MARKER=str(marker))
            for args in ([], ['--install']):
                with self.subTest(args=args):
                    result = subprocess.run([shutil.which('bash'), str(KK), 'doctor', *args],
                                            env=environment, capture_output=True, text=True)
                    # Native Apple signer checks can fail on the Linux test host.
                    self.assertIn('ok: Python PyYAML module found', result.stdout)
                    self.assertNotIn('Homebrew Bash', result.stdout + result.stderr)
                    self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
