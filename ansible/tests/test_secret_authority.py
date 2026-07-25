#!/usr/bin/env python3
import base64
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "klokast-ops" / "secret-authority" / "bin" / "ksa-static-site"


def load_module():
    loader = SourceFileLoader("ksa_static_site", str(SCRIPT))
    spec = importlib.util.spec_from_loader("ksa_static_site", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class SecretAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def write_registry(self, root):
        registry = root / "platform-resources.yml"
        registry.write_text(
            "schema_version: 1\napps:\n  static-site:\n    enabled: true\n",
            encoding="utf-8",
        )
        return registry

    def run_script(self, *args, input_text=None):
        import subprocess

        return subprocess.run(
            [str(SCRIPT), "--repo-root", str(REPO_ROOT), *args],
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_install_intent_is_canonical_and_binds_registry_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self.write_registry(Path(tmpdir))
            result = self.run_script(
                "intent",
                "static-site",
                "install",
                "--box",
                "k001",
                "--domain",
                "www.klokast.ai",
                "--resources-registry",
                str(registry),
                "--expires-at",
                "2099-01-01T00:00:00Z",
                "--nonce",
                "nonce_123456789",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith("\n"))
        intent = json.loads(result.stdout)
        self.assertEqual(result.stdout, self.mod.canonical_json(intent))
        self.assertEqual(intent["action"], "install")
        self.assertEqual(intent["app"], "static-site")
        self.assertEqual(intent["box"], "k001")
        self.assertRegex(intent["resources_registry_sha256"], r"^[0-9a-f]{64}$")

    def test_intent_rejects_vm_hostname_as_box(self):
        result = self.run_script(
            "intent",
            "static-site",
            "bootstrap-repo",
            "--box",
            "k001-dmz",
            "--domain",
            "www.klokast.ai",
            "--expires-at",
            "2099-01-01T00:00:00Z",
            "--nonce",
            "nonce_123456789",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("box name", result.stderr)

    def test_cloudflare_token_validation_accepts_base64_json_only(self):
        token = base64.urlsafe_b64encode(b'{"tunnel":"test"}').decode("ascii").rstrip("=")

        self.assertEqual(self.mod.validate_cloudflare_tunnel_token(token), token)

        with self.assertRaises(self.mod.SecretAuthorityError):
            self.mod.validate_cloudflare_tunnel_token("not json")

    def test_dry_run_hides_secret_values_from_output(self):
        class Args:
            dry_run = True
            infra_user = "smith"
            repo_root = str(REPO_ROOT)

        command = ["apps/static-site/bin/static-sitectl", "install"]
        with mock.patch.object(self.mod, "demote_to_user") as demote:
            user = mock.Mock()
            user.pw_dir = "/home/smith"
            user.pw_name = "smith"
            user.pw_shell = "/bin/ash"
            demote.return_value = (user, lambda: None)
            with mock.patch("sys.stdout") as stdout:
                rc = self.mod.run_as_infra(
                    Args(),
                    command,
                    {
                        "GITHUB_TOKEN": "github-secret-value",
                        "STATIC_SITE_CLOUDFLARED_TOKEN": "cloudflare-secret-value",
                    },
                )

        self.assertEqual(rc, 0)
        output = "".join(call.args[0] for call in stdout.write.call_args_list if call.args)
        self.assertIn("GITHUB_TOKEN", output)
        self.assertIn("STATIC_SITE_CLOUDFLARED_TOKEN", output)
        self.assertNotIn("github-secret-value", output)
        self.assertNotIn("cloudflare-secret-value", output)

    def test_nonce_replay_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = mock.Mock()
            args.state_root = tmpdir
            args.signer_id = "human"
            intent = {
                "nonce": "nonce_123456789",
                "action": "install",
                "app": "static-site",
                "repo_head": "a" * 40,
            }

            self.mod.consume_nonce(args, intent)

            with self.assertRaises(self.mod.SecretAuthorityError):
                self.mod.consume_nonce(args, intent)

    def test_github_app_mints_installation_token_with_integration(self):
        calls = {}

        class FakeAppAuth:
            def __init__(self, app_id, private_key):
                calls["app_id"] = app_id
                calls["private_key"] = private_key

        class FakeAuth:
            AppAuth = FakeAppAuth

        class FakeGithubIntegration:
            def __init__(self, auth):
                calls["auth"] = auth

            def get_access_token(self, installation_id, permissions):
                calls["installation_id"] = installation_id
                calls["permissions"] = permissions
                return mock.Mock(token="installation-token")

        fake_github = types.SimpleNamespace(Auth=FakeAuth, GithubIntegration=FakeGithubIntegration)
        with tempfile.TemporaryDirectory() as tmpdir:
            config_root = Path(tmpdir)
            (config_root / "github-app.env").write_text(
                "GITHUB_APP_ID=123456\n"
                "GITHUB_APP_INSTALLATION_ID=12345678\n",
                encoding="utf-8",
            )
            (config_root / "github-app.pem").write_text(
                "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----\n",
                encoding="utf-8",
            )

            with mock.patch.dict(sys.modules, {"github": fake_github}):
                token = self.mod.GithubApp(config_root).installation_token({"metadata": "read"})

        self.assertEqual(token, "installation-token")
        self.assertEqual(calls["app_id"], 123456)
        self.assertEqual(calls["installation_id"], 12345678)
        self.assertEqual(
            calls["private_key"],
            "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----\n",
        )
        self.assertIsInstance(calls["auth"], FakeAppAuth)
        self.assertEqual(calls["permissions"], {"metadata": "read"})


if __name__ == "__main__":
    unittest.main()
