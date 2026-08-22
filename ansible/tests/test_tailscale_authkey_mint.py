#!/usr/bin/env python3
import json
import tempfile
import subprocess
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "klokast-ops" / "tailscale" / "bin" / "ts-authkey-mint"


class TailscaleAuthkeyMintTest(unittest.TestCase):
    def run_mint(self, *args, env=None):
        return subprocess.run(
            [str(SCRIPT), *args],
            check=False,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_vm_app_enrollment_allows_vm_and_app_specific_tags(self):
        result = self.run_mint(
            "--purpose",
            "vm",
            "--hostname",
            "boxa-usr-alice",
            "--tags",
            "tag:vm,tag:user-shell-alice",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        create = body["capabilities"]["devices"]["create"]
        self.assertEqual(create["tags"], ["tag:vm", "tag:user-shell-alice"])
        self.assertFalse(create["reusable"])
        self.assertFalse(create["ephemeral"])
        self.assertTrue(create["preauthorized"])

    def test_ops_enrollment_can_include_infra_tag(self):
        result = self.run_mint(
            "--purpose",
            "ops",
            "--hostname",
            "boxa-ops",
            "--tags",
            "tag:ops,tag:infra",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:ops", "tag:infra"],
        )

    def test_standalone_infra_enrollment_uses_only_infra_tag(self):
        result = self.run_mint(
            "--purpose",
            "infra",
            "--hostname",
            "codex",
            "--tags",
            "tag:infra",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:infra"],
        )

    def test_legacy_standalone_infra_agent_enrollment_uses_only_infra_agent_tag(self):
        result = self.run_mint(
            "--purpose",
            "infra-agent",
            "--hostname",
            "codex",
            "--tags",
            "tag:infra-agent",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:infra-agent"],
        )

    def test_airunner_enrollment_uses_only_airunner_tag(self):
        result = self.run_mint(
            "--purpose",
            "airunner",
            "--hostname",
            "boxb-ops-airunner",
            "--tags",
            "tag:airunner",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:airunner"],
        )

    def test_candidate_airunner_enrollment_uses_only_airunner_tag(self):
        result = self.run_mint(
            "--purpose",
            "airunner",
            "--hostname",
            "boxb-ops-airunner-candidate",
            "--tags",
            "tag:airunner",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:airunner"],
        )

    def test_airunner_rejects_wrong_hostname_suffix(self):
        result = self.run_mint(
            "--purpose",
            "airunner",
            "--hostname",
            "boxb-airunner",
            "--tags",
            "tag:airunner",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "airunner hostname must end in -ops-airunner or -ops-airunner-candidate",
            result.stderr,
        )

    def test_airunner_rejects_extra_tags(self):
        result = self.run_mint(
            "--purpose",
            "airunner",
            "--hostname",
            "boxb-ops-airunner",
            "--tags",
            "tag:airunner,tag:infra",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected tags", result.stderr)

    def test_immich_enrollment_uses_only_immich_tag(self):
        result = self.run_mint(
            "--purpose",
            "immich",
            "--hostname",
            "photos",
            "--tags",
            "tag:immich",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:immich"],
        )

    def test_music_enrollment_uses_only_music_tag(self):
        result = self.run_mint(
            "--purpose",
            "music",
            "--hostname",
            "boxb-music",
            "--tags",
            "tag:music",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:music"],
        )

    def test_music_upload_enrollment_uses_only_music_upload_tag(self):
        result = self.run_mint(
            "--purpose",
            "music-upload",
            "--hostname",
            "boxb-music-upload",
            "--tags",
            "tag:music-upload",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:music-upload"],
        )

    def test_music_upload_rejects_unscoped_hostname(self):
        result = self.run_mint(
            "--purpose",
            "music-upload",
            "--hostname",
            "music",
            "--tags",
            "tag:music-upload",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("music-upload hostname must end in -music-upload", result.stderr)

    def test_print_enrollment_uses_only_print_tag(self):
        result = self.run_mint(
            "--purpose",
            "print",
            "--hostname",
            "boxb-print",
            "--tags",
            "tag:print",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:print"],
        )

    def test_print_rejects_unscoped_hostname(self):
        result = self.run_mint(
            "--purpose",
            "print",
            "--hostname",
            "printer",
            "--tags",
            "tag:print",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("print hostname must end in -print", result.stderr)

    def test_streamer_enrollment_uses_only_streamer_tag(self):
        result = self.run_mint(
            "--purpose",
            "streamer",
            "--hostname",
            "boxb-streamer",
            "--tags",
            "tag:streamer",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:streamer"],
        )

    def test_streamer_rejects_unscoped_hostname(self):
        result = self.run_mint(
            "--purpose",
            "streamer",
            "--hostname",
            "boxb-audio",
            "--tags",
            "tag:streamer",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("streamer hostname must end in -streamer", result.stderr)

    def test_streamer_rejects_extra_tags(self):
        result = self.run_mint(
            "--purpose",
            "streamer",
            "--hostname",
            "boxb-streamer",
            "--tags",
            "tag:streamer,tag:iot",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected tags", result.stderr)

    def test_standard_vm_rejects_extra_app_tag(self):
        result = self.run_mint(
            "--purpose",
            "vm",
            "--hostname",
            "boxa-router",
            "--tags",
            "tag:vm,tag:user-shell-alice",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected tags", result.stderr)

    def test_legacy_agt_app_vm_hostname_is_rejected(self):
        result = self.run_mint(
            "--purpose",
            "vm",
            "--hostname",
            "boxa-agt-alice",
            "--tags",
            "tag:vm,tag:user-shell-alice",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("standard VM hostname must end", result.stderr)

    def test_usr_app_vm_rejects_reserved_usr_tag(self):
        result = self.run_mint(
            "--purpose",
            "vm",
            "--hostname",
            "boxa-usr-alice",
            "--tags",
            "tag:vm,tag:usr",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("app-specific tags", result.stderr)

    def test_household_vpn_enrollment_uses_vm_and_household_vpn_tags(self):
        result = self.run_mint(
            "--purpose",
            "vm",
            "--hostname",
            "boxb-household-vpn",
            "--tags",
            "tag:vm,tag:household-vpn",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(
            body["capabilities"]["devices"]["create"]["tags"],
            ["tag:vm", "tag:household-vpn"],
        )

    def test_check_config_requests_tag_scoped_oauth_token(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode()
                requests.append((self.path, urllib.parse.parse_qs(body)))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    b'{"access_token":"tskey-api-test","token_type":"Bearer"}'
                )

            def log_message(self, fmt, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                secret_file = Path(tmpdir) / "tailscale-policy.env"
                secret_file.write_text(
                    'TAILNET_ID="tailnet-test"\n'
                    'TS_OAUTH_CLIENT_ID="client-test"\n'
                    'TS_OAUTH_CLIENT_SECRET="secret-test"\n'
                )
                env = {
                    "PATH": "/usr/bin:/bin",
                    "TS_AUTHKEY_SECRET_FILE": str(secret_file),
                    "TS_AUTHKEY_API_BASE": f"http://127.0.0.1:{server.server_port}/api/v2",
                }
                result = self.run_mint(
                    "--purpose",
                    "ops",
                    "--hostname",
                    "boxa-ops",
                    "--tags",
                    "tag:ops,tag:infra",
                    "--check-config",
                    env=env,
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0], "/api/v2/oauth/token")
        self.assertEqual(requests[0][1]["grant_type"], ["client_credentials"])
        self.assertEqual(requests[0][1]["scope"], ["auth_keys"])
        self.assertEqual(requests[0][1]["tags"], ["tag:ops tag:infra"])


if __name__ == "__main__":
    unittest.main()
