#!/usr/bin/env python3
import base64
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "klokast-dev" / "bin" / "ingest-static-site-cloudflare-token"


class KlokastDevSecretAuthorityWrapperTest(unittest.TestCase):
    def write_executable(self, path, content):
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        path.chmod(0o755)

    def fake_cloudflare_token(self):
        return base64.urlsafe_b64encode(b'{"tunnel":"test"}').decode("ascii").rstrip("=")

    def make_env(self, root, *, intent_domain="www.klokast.ai", final_status_true=True):
        fake_bin = root / "bin"
        fake_bin.mkdir()
        fake_ssh_keygen_log = root / "ssh-keygen.log"
        fake_token_log = root / "token.log"
        fake_remote_root = root / "remote"
        fake_remote_root.mkdir()

        self.write_executable(
            fake_bin / "ssh-keygen",
            """\
            #!/bin/sh
            printf '%s\\n' "$*" >> "$FAKE_SSH_KEYGEN_LOG"
            last=''
            for arg in "$@"; do
              last="$arg"
            done
            case " $* " in
              *" -Y sign "*)
                printf 'fake signature\\n' > "${last}.sig"
                exit 0
                ;;
            esac
            exit 2
            """,
        )
        self.write_executable(
            fake_bin / "tailscale",
            """\
            #!/bin/sh
            set -eu
            [ "${1:-}" = ssh ] || exit 2
            shift
            target=$1
            shift

            remote_path() {
              printf '%s/%s\\n' "$FAKE_REMOTE_ROOT" "$(printf '%s' "$1" | sed 's#^/##')"
            }

            if [ "${1:-}" = sh ] && [ "${2:-}" = -s ] && [ "${3:-}" = -- ]; then
              cat >/dev/null
              box=$4
              domain=$5
              repo_owner=$6
              repo_name=$7
              branch=$8
              source_dir=$9
              intent_domain=${FAKE_INTENT_DOMAIN:-$domain}
              intent_action=${FAKE_INTENT_ACTION:-ingest-cloudflare-token}
              cat <<EOF
            {"action":"$intent_action","app":"static-site","authority":"klokast-secret-authority","box":"$box","branch":"$branch","domain":"$intent_domain","expires_at":"2099-01-01T00:00:00Z","nonce":"nonce_123456789","repo_head":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","repo_name":"$repo_name","repo_owner":"$repo_owner","schema_version":1,"source_dir":"$source_dir"}
            EOF
              exit 0
            fi

            cmd="$*"
            case "$cmd" in
              "mktemp -d /tmp/ksa-static-site.XXXXXX")
                mkdir -p "$FAKE_REMOTE_ROOT/tmp/ksa-static-site.fake"
                printf '/tmp/ksa-static-site.fake\\n'
                exit 0
                ;;
              *"cat > '/tmp/ksa-static-site.fake/intent.json'"*)
                mkdir -p "$FAKE_REMOTE_ROOT/tmp/ksa-static-site.fake"
                cat > "$FAKE_REMOTE_ROOT/tmp/ksa-static-site.fake/intent.json"
                exit 0
                ;;
              *"cat > '/tmp/ksa-static-site.fake/intent.json.sig'"*)
                mkdir -p "$FAKE_REMOTE_ROOT/tmp/ksa-static-site.fake"
                cat > "$FAKE_REMOTE_ROOT/tmp/ksa-static-site.fake/intent.json.sig"
                exit 0
                ;;
              *"ingest-cloudflare-token"*)
                cat > "$FAKE_TOKEN_LOG"
                printf 'ok\\n'
                exit 0
                ;;
              *"status --redacted"*)
                if [ "${FAKE_FINAL_STATUS_TRUE:-1}" = 1 ]; then
                  printf '{"allowed_signers_configured":true,"app":"static-site","audit_log":"/var/log/klokast/secret-authority.jsonl","authority":"klokast-secret-authority","cloudflare_token_configured":true,"github_app_configured":true,"schema_version":1,"state_root":"/var/lib/klokast/secret-authority"}\\n'
                else
                  printf '{"allowed_signers_configured":true,"app":"static-site","audit_log":"/var/log/klokast/secret-authority.jsonl","authority":"klokast-secret-authority","cloudflare_token_configured":false,"github_app_configured":true,"schema_version":1,"state_root":"/var/lib/klokast/secret-authority"}\\n'
                fi
                exit 0
                ;;
              "rm -rf '/tmp/ksa-static-site.fake'")
                rm -rf "$FAKE_REMOTE_ROOT/tmp/ksa-static-site.fake"
                exit 0
                ;;
            esac

            printf 'unexpected fake tailscale command for %s: %s\\n' "$target" "$cmd" >&2
            exit 2
            """,
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "FAKE_SSH_KEYGEN_LOG": str(fake_ssh_keygen_log),
                "FAKE_TOKEN_LOG": str(fake_token_log),
                "FAKE_REMOTE_ROOT": str(fake_remote_root),
                "FAKE_INTENT_DOMAIN": intent_domain,
                "FAKE_FINAL_STATUS_TRUE": "1" if final_status_true else "0",
            }
        )
        return env, fake_ssh_keygen_log, fake_token_log

    def run_wrapper(self, env, input_text, key_path):
        return subprocess.run(
            [
                str(WRAPPER),
                "--controller",
                "k002-ops",
                "--box",
                "k001",
                "--domain",
                "www.klokast.ai",
                "--signer-id",
                "xiaoju-og",
                "--key",
                str(key_path),
                "--ssh-keygen",
                "ssh-keygen",
                "--skip-yubikey-gate",
            ],
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

    def test_successful_ingest_outputs_redacted_json_only(self):
        token = self.fake_cloudflare_token()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env, ssh_keygen_log, token_log = self.make_env(root)
            key_path = root / "fake-approval-key"
            key_path.write_text("fake", encoding="utf-8")

            result = self.run_wrapper(env, f"approve static-site www.klokast.ai\n{token}\n", key_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["status"], "ok")
            self.assertTrue(output["cloudflare_token_configured"])
            self.assertEqual(output["action"], "ingest-cloudflare-token")
            self.assertIn("intent_sha256", output)
            self.assertNotIn(token, result.stdout)
            self.assertNotIn(token, result.stderr)
            self.assertIn("-Y sign", ssh_keygen_log.read_text(encoding="utf-8"))
            self.assertEqual(token_log.read_text(encoding="utf-8"), token)

    def test_wrong_approval_phrase_aborts_before_signing(self):
        token = self.fake_cloudflare_token()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env, ssh_keygen_log, token_log = self.make_env(root)
            key_path = root / "fake-approval-key"
            key_path.write_text("fake", encoding="utf-8")

            result = self.run_wrapper(env, f"wrong phrase\n{token}\n", key_path)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("approval phrase mismatch", result.stderr)
            self.assertFalse(ssh_keygen_log.exists())
            self.assertFalse(token_log.exists())

    def test_mismatched_intent_domain_aborts_before_signing(self):
        token = self.fake_cloudflare_token()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env, ssh_keygen_log, token_log = self.make_env(root, intent_domain="evil.example")
            key_path = root / "fake-approval-key"
            key_path.write_text("fake", encoding="utf-8")

            result = self.run_wrapper(env, f"approve static-site www.klokast.ai\n{token}\n", key_path)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("intent domain mismatch", result.stderr)
            self.assertFalse(ssh_keygen_log.exists())
            self.assertFalse(token_log.exists())

    def test_final_status_must_report_cloudflare_token_configured(self):
        token = self.fake_cloudflare_token()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env, _ssh_keygen_log, _token_log = self.make_env(root, final_status_true=False)
            key_path = root / "fake-approval-key"
            key_path.write_text("fake", encoding="utf-8")

            result = self.run_wrapper(env, f"approve static-site www.klokast.ai\n{token}\n", key_path)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cloudflare_token_configured=true", result.stderr)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
