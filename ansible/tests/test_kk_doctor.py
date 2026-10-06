#!/usr/bin/env python3
import os
import subprocess
import shutil
import json
import sys
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
            "- Bash | Homebrew package | Run MacBook Bash scripts with version 5.2 or newer.",
            "- Git | Apple Command Line Tools | Synchronize checkouts and publish private-instance repositories.",
            "- Apple OpenSSH and CryptoTokenKit | Apple-provided | Provide SSH transport and Touch ID approval signatures.",
            "- rsync | Apple-provided | Upload music libraries.",
            "- Tailscale | Standalone vendor .pkg | Provide Tailnet access and dispatch commands to the controller.",
            "- Homebrew | Official Homebrew installer | Install Bash and Python during MacBook setup.",
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
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='macbook-bash-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        self.brew_bin = self.root / 'homebrew/bin'
        self.brew_bin.mkdir(parents=True)
        self.prefix = self.root / 'homebrew/opt/bash'
        self.bash = self.prefix / 'bin/bash'
        self.log = self.root / 'brew.jsonl'
        self.real_bash = shutil.which('bash')
        self.environment = os.environ.copy()
        self.environment.update(PATH=f'{self.tools}:{self.brew_bin}:{os.defpath}',
                                KLOKAST_TEST_BASH_PREFIX=str(self.prefix),
                                KLOKAST_TEST_BREW_BIN=str(self.brew_bin),
                                KLOKAST_TEST_BREW_LOG=str(self.log))
        for name in ('python3', 'tailscale', 'ssh', 'rsync', 'ssh-keygen'):
            self.write_tool(self.tools / name, '#!/bin/sh\nexit 0\n')
        self.write_tool(self.tools / 'uname', '#!/bin/sh\nprintf "Darwin\\n"\n')
        # Simulate Homebrew only. Installation writes inside this test directory.
        self.write_tool(self.brew_bin / 'brew', f'#!{sys.executable}\n' + '''import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['KLOKAST_TEST_BREW_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args == ['--prefix', 'bash']:
    print(os.environ['KLOKAST_TEST_BASH_PREFIX'])
elif args in (['install', 'bash'], ['upgrade', 'bash']):
    if os.environ.get('KLOKAST_TEST_BREW_FAIL'):
        sys.exit(1)
    binary = pathlib.Path(os.environ['KLOKAST_TEST_BASH_PREFIX']) / 'bin/bash'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text('#!/bin/sh\\nprintf "5.3\\\\n"\\n')
    binary.chmod(0o755)
    link = pathlib.Path(os.environ['KLOKAST_TEST_BREW_BIN']) / 'bash'
    if not link.exists():
        link.symlink_to(binary)
else:
    sys.exit('unexpected Homebrew call')
''')

    def write_tool(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755)

    def install_bash(self, version):
        self.write_tool(self.bash, '#!/bin/sh\nprintf "%s\\n" ' + version + '\n')
        (self.brew_bin / 'bash').symlink_to(self.bash)

    def run_doctor(self, *args):
        # Other native macOS checks remain active; this test asserts the Bash
        # result separately because the test host may not have Apple's signer.
        return subprocess.run([self.real_bash, str(KK), 'doctor', *args],
                              env=self.environment, capture_output=True,
                              text=True, check=False)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_accepts_selected_homebrew_bash_and_reports_path(self):
        self.install_bash('5.3')
        result = self.run_doctor()
        self.assertIn(f'ok: Homebrew Bash 5.3 selected at {self.brew_bin}/bash', result.stdout)
        self.assertEqual(self.calls(), [['--prefix', 'bash']])

    def test_supported_minimum_and_future_major(self):
        self.install_bash('5.2')
        for version in ('5.2', '6.0'):
            self.write_tool(self.bash, f'#!/bin/sh\nprintf "{version}\\n"\n')
            result = self.run_doctor()
            self.assertIn(f'ok: Homebrew Bash {version} selected', result.stdout)

    def test_missing_homebrew_has_setup_instruction(self):
        (self.brew_bin / 'brew').unlink()
        result = self.run_doctor()
        self.assertEqual(result.returncode, 2)
        self.assertIn('missing: Homebrew. Install it from https://brew.sh/', result.stderr)

    def test_missing_bash_does_not_install_without_flag(self):
        result = self.run_doctor()
        self.assertEqual(result.returncode, 2)
        self.assertIn('missing: Homebrew Bash. Run kk doctor --install', result.stderr)
        self.assertEqual(self.calls(), [['--prefix', 'bash']])

    def test_install_flag_installs_and_rechecks_selection(self):
        result = self.run_doctor('--install')
        self.assertIn('ok: Homebrew Bash 5.3 selected', result.stdout)
        self.assertEqual(self.calls(), [['--prefix', 'bash'], ['install', 'bash']])

    def test_install_flag_leaves_supported_bash_alone(self):
        self.install_bash('5.3')
        result = self.run_doctor('--install')
        self.assertIn('ok: Homebrew Bash 5.3 selected', result.stdout)
        self.assertEqual(self.calls(), [['--prefix', 'bash']])

    def test_install_failure_is_visible(self):
        self.environment['KLOKAST_TEST_BREW_FAIL'] = '1'
        result = self.run_doctor('--install')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Homebrew Bash installation failed', result.stderr)
        self.assertNotIn('ok: Homebrew Bash', result.stdout)

    def test_path_shadowing_is_rejected_even_for_modern_other_bash(self):
        self.install_bash('5.3')
        self.write_tool(self.tools / 'bash', '#!/bin/sh\nprintf "5.3\\n"\n')
        result = self.run_doctor()
        self.assertEqual(result.returncode, 2)
        self.assertIn(f'PATH selects {self.tools}/bash instead of Homebrew Bash', result.stderr)
        self.assertNotIn('ok: Homebrew Bash', result.stdout)

    def test_install_does_not_hide_path_shadowing(self):
        self.write_tool(self.tools / 'bash', '#!/bin/sh\nprintf "5.3\\n"\n')
        result = self.run_doctor('--install')
        self.assertEqual(result.returncode, 2)
        self.assertIn(f'PATH selects {self.tools}/bash instead of Homebrew Bash', result.stderr)
        self.assertNotIn('ok: Homebrew Bash', result.stdout)

    def test_unsupported_version_has_upgrade_instruction(self):
        self.install_bash('5.1')
        result = self.run_doctor()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Homebrew Bash 5.2 or newer is required', result.stderr)
        self.assertIn('brew upgrade bash', result.stderr)

    def test_install_flag_upgrades_unsupported_version(self):
        self.install_bash('5.1')
        result = self.run_doctor('--install')
        self.assertIn('ok: Homebrew Bash 5.3 selected', result.stdout)
        self.assertEqual(self.calls(), [['--prefix', 'bash'], ['upgrade', 'bash']])

    def test_upgrade_failure_is_visible(self):
        self.install_bash('5.1')
        self.environment['KLOKAST_TEST_BREW_FAIL'] = '1'
        result = self.run_doctor('--install')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Homebrew Bash upgrade failed', result.stderr)
        self.assertNotIn('ok: Homebrew Bash', result.stdout)

    def test_invalid_version_output_is_rejected(self):
        self.install_bash('53')
        result = self.run_doctor()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Homebrew Bash 5.2 or newer is required', result.stderr)
        self.assertNotIn('ok: Homebrew Bash', result.stdout)


if __name__ == "__main__":
    unittest.main()
