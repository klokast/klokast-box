#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_INSTANCE = REPO_ROOT / "ansible" / "bin" / "platform-instance"
ENGINE_COMMIT = "a" * 40
DNS_NAME = "example-tailnet.ts.net"
MEMBER_LOGIN = "member@example.com"


def load_platform_instance():
    loader = SourceFileLoader("platform_instance_configure_test", str(PLATFORM_INSTANCE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PlatformInstanceConfigureValuesTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_platform_instance()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.private_root = self.home / "private" / "klokast"
        self.values_path = self.private_root / "init-values.json"
        self.private_patch = mock.patch.object(
            self.mod, "PRIVATE_ROOT", self.private_root
        )
        self.values_patch = mock.patch.object(
            self.mod, "VALUES_PATH", self.values_path
        )
        self.private_patch.start()
        self.values_patch.start()
        self.args = mock.Mock(
            engine_commit=ENGINE_COMMIT,
            build_dir="/var/lib/klokast/builds/klokast-cli/test",
        )

    def tearDown(self):
        self.values_patch.stop()
        self.private_patch.stop()
        self.temporary.cleanup()

    def write_values(self, value, mode=0o600):
        self.private_root.parent.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.private_root.parent, 0o700)
        self.private_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.private_root, 0o700)
        self.values_path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.chmod(self.values_path, mode)

    def run_configure(self, answers, dns_name=DNS_NAME):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            self.mod, "verify_configure_engine", return_value=Path("/sealed/klokast")
        ), mock.patch.object(
            self.mod, "detect_tailnet_dns_name", return_value=dns_name
        ), mock.patch.object(
            self.mod, "validate_candidate_with_initializer"
        ) as candidate_validator, mock.patch.object(
            self.mod, "validate_values_with_initializer"
        ) as existing_validator, mock.patch.object(
            self.mod.sys, "stdin", io.StringIO(answers)
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.mod.configure_values(self.args)
        return stdout.getvalue(), stderr.getvalue(), candidate_validator, existing_validator

    def test_first_configuration_builds_complete_owner_only_values(self):
        stdout, stderr, validator, _ = self.run_configure(f"{MEMBER_LOGIN}\ny\n")
        self.assertEqual(stdout, "created\n")
        self.assertIn(f"Tailnet DNS name: {DNS_NAME}", stderr)
        self.assertIn(f"Tailscale member login: {MEMBER_LOGIN}", stderr)
        self.assertIn("Member roles: operator, family", stderr)
        self.assertIn("Platform time: Etc/UTC", stderr)
        self.assertEqual(stat.S_IMODE(self.values_path.stat().st_mode), 0o600)
        value = json.loads(self.values_path.read_text(encoding="utf-8"))
        self.assertEqual(value["tailnet"]["tailnet-dns-name"], DNS_NAME)
        self.assertEqual(
            value["tailnet"]["members"][MEMBER_LOGIN]["roles"],
            ["operator", "family"],
        )
        self.assertNotIn("timezone", json.dumps(value).lower())
        self.assertNotIn("REPLACE_WITH", json.dumps(value))
        validator.assert_called_once()

    def test_known_placeholder_file_is_repaired(self):
        self.write_values(
            self.mod.build_instance_values(
                ENGINE_COMMIT, self.mod.PLACEHOLDER_DNS, self.mod.PLACEHOLDER_LOGIN
            )
        )
        stdout, stderr, validator, _ = self.run_configure(f"{MEMBER_LOGIN}\ny\n")
        self.assertEqual(stdout, "repaired\n")
        self.assertIn("recognized placeholder template", stderr)
        self.assertNotIn("REPLACE_WITH", self.values_path.read_text(encoding="utf-8"))
        validator.assert_called_once()

    def test_partial_placeholder_keeps_the_existing_login_as_the_default(self):
        self.write_values(
            self.mod.build_instance_values(
                ENGINE_COMMIT, self.mod.PLACEHOLDER_DNS, MEMBER_LOGIN
            )
        )
        stdout, stderr, _, _ = self.run_configure("\ny\n")
        self.assertEqual(stdout, "repaired\n")
        self.assertIn(f"Tailscale member login [{MEMBER_LOGIN}]", stderr)
        value = json.loads(self.values_path.read_text(encoding="utf-8"))
        self.assertIn(MEMBER_LOGIN, value["tailnet"]["members"])

    def test_valid_existing_file_is_reviewed_and_reused_without_rewrite(self):
        value = self.mod.build_instance_values(ENGINE_COMMIT, DNS_NAME, MEMBER_LOGIN)
        self.write_values(value)
        before = self.values_path.stat()
        content = self.values_path.read_bytes()
        stdout, stderr, candidate_validator, existing_validator = self.run_configure("y\n")
        after = self.values_path.stat()
        self.assertEqual(stdout, "reused\n")
        self.assertIn("valid and unchanged", stderr)
        self.assertEqual(content, self.values_path.read_bytes())
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        candidate_validator.assert_not_called()
        existing_validator.assert_called_once_with(
            Path("/sealed/klokast"), self.values_path
        )

    def test_invalid_member_logins_get_specific_corrections(self):
        answers = "not-an-email\nspace @example.com\n" + MEMBER_LOGIN + "\ny\n"
        stdout, stderr, _, _ = self.run_configure(answers)
        self.assertEqual(stdout, "created\n")
        self.assertIn("enter one email-style Tailscale login", stderr)
        self.assertIn("must not contain spaces", stderr)

    def test_cancellation_and_eof_do_not_create_values(self):
        stdout, _stderr, _, _ = self.run_configure(f"{MEMBER_LOGIN}\nn\n")
        self.assertEqual(stdout, "cancelled\n")
        self.assertFalse(self.values_path.exists())
        with self.assertRaisesRegex(self.mod.InstanceError, "input ended"):
            self.run_configure("")
        self.assertFalse(self.values_path.exists())

    def test_interruption_before_confirmation_does_not_create_values(self):
        class InterruptedInput:
            @staticmethod
            def readline():
                raise KeyboardInterrupt

        with mock.patch.object(
            self.mod, "verify_configure_engine", return_value=Path("/sealed/klokast")
        ), mock.patch.object(
            self.mod, "detect_tailnet_dns_name", return_value=DNS_NAME
        ), mock.patch.object(
            self.mod.sys, "stdin", InterruptedInput()
        ), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                self.mod.configure_values(self.args)
        self.assertFalse(self.values_path.exists())

    def test_tailscale_status_must_be_available_valid_and_exact(self):
        failed = mock.Mock(returncode=1, stdout="", stderr="private detail")
        with mock.patch.object(self.mod, "run", return_value=failed):
            with self.assertRaisesRegex(self.mod.InstanceError, "cannot read Tailscale status"):
                self.mod.detect_tailnet_dns_name()
        malformed = mock.Mock(returncode=0, stdout="not-json", stderr="")
        with mock.patch.object(self.mod, "run", return_value=malformed):
            with self.assertRaisesRegex(self.mod.InstanceError, "valid JSON"):
                self.mod.detect_tailnet_dns_name()
        invalid = mock.Mock(
            returncode=0,
            stdout=json.dumps({"CurrentTailnet": {"MagicDNSSuffix": "machine.local"}}),
            stderr="",
        )
        with mock.patch.object(self.mod, "run", return_value=invalid):
            with self.assertRaisesRegex(self.mod.InstanceError, "ending in .ts.net"):
                self.mod.detect_tailnet_dns_name()
        valid = mock.Mock(
            returncode=0,
            stdout=json.dumps({"MagicDNSSuffix": DNS_NAME + "."}),
            stderr="",
        )
        with mock.patch.object(self.mod, "run", return_value=valid):
            self.assertEqual(self.mod.detect_tailnet_dns_name(), DNS_NAME)
        conflicting = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "MagicDNSSuffix": DNS_NAME,
                "CurrentTailnet": {"MagicDNSSuffix": "other-tailnet.ts.net"},
            }),
            stderr="",
        )
        with mock.patch.object(self.mod, "run", return_value=conflicting):
            with self.assertRaisesRegex(self.mod.InstanceError, "conflicting"):
                self.mod.detect_tailnet_dns_name()

    def test_unsafe_or_unexpected_existing_files_are_not_overwritten(self):
        cases = ("symlink", "hardlink", "permissions", "unexpected")
        for case in cases:
            with self.subTest(case=case):
                if self.private_root.exists():
                    for child in self.private_root.iterdir():
                        child.unlink()
                else:
                    self.private_root.parent.mkdir(mode=0o700, exist_ok=True)
                    os.chmod(self.private_root.parent, 0o700)
                    self.private_root.mkdir(mode=0o700)
                target = self.private_root / "target.json"
                if case == "symlink":
                    target.write_text("{}\n", encoding="utf-8")
                    self.values_path.symlink_to(target)
                elif case == "hardlink":
                    target.write_text("{}\n", encoding="utf-8")
                    os.chmod(target, 0o600)
                    os.link(target, self.values_path)
                elif case == "permissions":
                    self.write_values(
                        self.mod.build_instance_values(
                            ENGINE_COMMIT, DNS_NAME, MEMBER_LOGIN
                        ),
                        mode=0o644,
                    )
                else:
                    self.write_values({"unexpected": True})
                original = self.values_path.read_bytes()
                with self.assertRaises(self.mod.InstanceError):
                    self.run_configure("y\n")
                self.assertEqual(self.values_path.read_bytes(), original)

    def test_values_path_cannot_move_outside_the_fixed_private_root(self):
        with mock.patch.object(self.mod, "VALUES_PATH", self.home / "other.json"):
            with self.assertRaisesRegex(self.mod.InstanceError, "fixed controller private path"):
                self.mod.prepare_values_directory()

    def test_initializer_failures_are_field_by_field_and_do_not_echo_stderr(self):
        completed = mock.Mock(
            returncode=2,
            stdout=json.dumps({
                "created": False,
                "diagnostics": [
                    {
                        "path": "klokast-instance.json$.tailnet.tailnet-dns-name",
                        "code": "schema.invalid",
                        "message": "document does not satisfy the schema",
                    },
                    {
                        "path": "klokast-instance.json$.tailnet.members",
                        "code": "member.operator",
                        "message": "an operator and family member is required",
                    },
                ],
            }),
            stderr="must-not-appear",
        )
        with self.assertRaises(self.mod.InstanceError) as raised:
            self.mod.parse_initializer_result(completed, self.private_root / "seed")
        message = str(raised.exception)
        self.assertIn("tailnet-dns-name [schema.invalid]", message)
        self.assertIn("tailnet.members [member.operator]", message)
        self.assertNotIn("must-not-appear", message)

    def test_private_values_are_not_passed_as_initializer_arguments(self):
        destination = self.private_root / "check" / "instance"
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({"created": True, "instance_path": str(destination)}),
            stderr="",
        )
        with mock.patch.object(self.mod, "run", return_value=completed) as runner:
            self.mod.run_initializer(
                Path("/sealed/klokast"), self.values_path, destination
            )
        arguments = [str(item) for item in runner.call_args.args[0]]
        self.assertNotIn(DNS_NAME, arguments)
        self.assertNotIn(MEMBER_LOGIN, arguments)
        self.assertIn(str(self.values_path), arguments)


if __name__ == "__main__":
    unittest.main()
