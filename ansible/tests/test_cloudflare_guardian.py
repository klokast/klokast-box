#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDIAN_SCRIPT = REPO_ROOT / "klokast-ops" / "cloudflare-guardian" / "bin" / "cloudflare-guardian"
DISPATCHER_SCRIPT = REPO_ROOT / "klokast-ops" / "secret-authority" / "bin" / "ksa-cloudflare"


def load_module(name, path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CloudflareGuardianTest(unittest.TestCase):
    def setUp(self):
        self.guardian = load_module("cloudflare_guardian", GUARDIAN_SCRIPT)
        self.dispatcher = load_module("ksa_cloudflare", DISPATCHER_SCRIPT)

    def intent(self):
        return {
            "schema_version": 1,
            "authority": "klokast-secret-authority",
            "provider": "cloudflare",
            "action": "static-site-tunnel",
            "guardian": "k001-dom0",
            "box": "k001",
            "domain": "www.klokast.ai",
            "cloudflare_account_id": "account123",
            "cloudflare_zone_id": "zone123",
            "tunnel_name": "klokast-static-k001",
            "service_url": "http://127.0.0.1:18081",
            "repo_head": "a" * 40,
            "expires_at": "2099-01-01T00:00:00Z",
            "nonce": "nonce_123456789",
        }

    def test_dispatcher_intent_is_canonical(self):
        class Args:
            guardian = "k001-dom0"
            box = "k001"
            domain = "www.klokast.ai"
            cloudflare_account_id = "account123"
            cloudflare_zone_id = "zone123"
            tunnel_name = "klokast-static-k001"
            service_url = "http://127.0.0.1:18081"
            ttl_seconds = 600
            expires_at = "2099-01-01T00:00:00Z"
            nonce = "nonce_123456789"

        intent = self.dispatcher.build_intent(Args(), REPO_ROOT)

        self.assertEqual(intent["provider"], "cloudflare")
        self.assertEqual(intent["action"], "static-site-tunnel")
        self.assertEqual(self.dispatcher.canonical_json(intent), json.dumps(intent, sort_keys=True, separators=(",", ":")) + "\n")

    def test_dispatcher_status_does_not_require_box(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            argv = [
                "--config-root",
                tmpdir,
                "--state-root",
                tmpdir,
                "--audit-log",
                str(Path(tmpdir) / "audit.jsonl"),
                "cloudflare",
                "status",
                "--redacted",
            ]
            with mock.patch.object(self.dispatcher, "require_root"):
                with mock.patch("sys.stdout") as stdout:
                    self.dispatcher.main(argv)

        output = "".join(call.args[0] for call in stdout.write.call_args_list if call.args)
        status = json.loads(output)
        self.assertEqual(status["provider"], "cloudflare")
        self.assertFalse(status["static_site_cloudflare_token_configured"])

    def test_guardian_policy_rejects_wrong_domain(self):
        intent = self.intent()
        policy = {
            "static_site": {
                "guardian": "k001-dom0",
                "box": "k001",
                "domain": "other.klokast.ai",
                "cloudflare_account_id": "account123",
                "cloudflare_zone_id": "zone123",
                "tunnel_name": "klokast-static-k001",
                "service_url": "http://127.0.0.1:18081",
            }
        }

        with self.assertRaises(self.guardian.GuardianError):
            self.guardian.verify_policy(policy, intent)

    def test_guardian_nonce_replay_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = mock.Mock()
            args.state_root = tmpdir
            args.signer_id = "human"
            intent = self.intent()

            self.guardian.consume_nonce(args, intent)

            with self.assertRaises(self.guardian.GuardianError):
                self.guardian.consume_nonce(args, intent)

    def test_guardian_unseal_token_never_appears_in_dry_run_response(self):
        intent = self.intent()
        request = {"intent": intent, "approval_signature": "signature"}
        policy = {
            "static_site": {
                "guardian": "k001-dom0",
                "box": "k001",
                "domain": "www.klokast.ai",
                "cloudflare_account_id": "account123",
                "cloudflare_zone_id": "zone123",
                "tunnel_name": "klokast-static-k001",
                "service_url": "http://127.0.0.1:18081",
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            args = mock.Mock()
            args.dry_run = True
            args.signer_id = "human"
            args.state_root = tmpdir
            args.audit_log = str(Path(tmpdir) / "audit.jsonl")
            args.guardian = "k001-dom0"
            args.api_base = "https://api.cloudflare.invalid/client/v4"
            args.allowed_signers = "/does/not/matter"
            args.ciphertext_file = ""
            args.unseal_command = ""
            policy_path = Path(tmpdir) / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            args.policy = str(policy_path)
            with mock.patch("sys.stdin", mock.Mock(read=lambda: json.dumps(request))):
                with mock.patch.object(self.guardian, "require_root"):
                    with mock.patch.object(self.guardian, "verify_signature"):
                        with mock.patch("sys.stdout") as stdout:
                            self.guardian.static_site_tunnel(args)

        output = "".join(call.args[0] for call in stdout.write.call_args_list if call.args)
        self.assertIn("cloudflared_token", output)
        self.assertNotIn("dry-run-token", output)
        self.assertNotIn("Bearer", output)

    def test_cloudflare_client_uses_bearer_without_printing_token(self):
        client = self.guardian.CloudflareClient("root-secret", api_base="https://api.example")
        captured = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"success":true,"result":{"ok":true}}'

        def fake_urlopen(request, timeout):
            captured["authorization"] = request.headers.get("Authorization")
            captured["url"] = request.full_url
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            result = client.result("GET", "/test")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["authorization"], "Bearer root-secret")
        self.assertEqual(captured["url"], "https://api.example/test")


if __name__ == "__main__":
    unittest.main()
