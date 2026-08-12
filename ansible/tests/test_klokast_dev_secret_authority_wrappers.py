#!/usr/bin/env python3
import base64
import json
import os
import shutil
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
        fake_provider = root / "ssh-keychain.dylib"
        fake_provider.write_text("provider", encoding="utf-8")
        fake_ssh_keygen_log = root / "ssh-keygen.log"
        fake_token_log = root / "token.log"
        fake_remote_root = root / "remote"
        fake_remote_root.mkdir()

        self.write_executable(
            fake_bin / "ssh-keygen",
            """\
            #!/bin/sh
            printf '%s\\n' "$*" >> "$FAKE_SSH_KEYGEN_LOG"
            case " $* " in
              *" -? "*) printf 'usage: ssh-keygen -K -Y -w provider\\n' >&2; exit 1 ;;
              *" -E sha256 -lf "*) printf '256 SHA256:fakefingerprint test (ECDSA-SK)\\n'; exit 0 ;;
            esac
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
            fake_bin / "sc_auth",
            """\
            #!/bin/sh
            case "${1:-}" in
              '') printf 'create-ctk-identity\\nlist-ctk-identities\\n';;
              identities)
                printf 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\\tKlokast static-site approval\\n'
                ;;
              *) exit 2;;
            esac
            """,
        )
        self.write_executable(fake_bin / "uname", "#!/bin/sh\nprintf 'Darwin\\n'\n")
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
                  printf '{"allowed_signers_configured":true,"approval_signer":"human-static-site","app":"static-site","audit_log":"/var/log/klokast/secret-authority.jsonl","authority":"klokast-secret-authority","cloudflare_token_configured":true,"github_app_configured":true,"schema_version":1,"state_root":"/var/lib/klokast/secret-authority"}\\n'
                else
                  printf '{"allowed_signers_configured":true,"approval_signer":"human-static-site","app":"static-site","audit_log":"/var/log/klokast/secret-authority.jsonl","authority":"klokast-secret-authority","cloudflare_token_configured":false,"github_app_configured":true,"schema_version":1,"state_root":"/var/lib/klokast/secret-authority"}\\n'
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

        home = root / "home"
        profile = home / ".local" / "share" / "klokast" / "approval-signers" / "static-site"
        profile.mkdir(parents=True)
        key_path = profile / "id_ecdsa_sk_rk"
        key_path.write_text("fake key handle\n", encoding="utf-8")
        (profile / "id_ecdsa_sk_rk.pub").write_text(
            "sk-ecdsa-sha2-nistp256@openssh.com AAAAfake static-site\n",
            encoding="utf-8",
        )

        tool_root = root / "tool"
        tool_bin = tool_root / "bin"
        tool_lib = tool_root / "lib"
        tool_bin.mkdir(parents=True)
        tool_lib.mkdir()
        wrapper = tool_bin / WRAPPER.name
        shutil.copy2(WRAPPER, wrapper)
        common_source = (REPO_ROOT / "klokast-dev" / "lib" / "approval-common.sh").read_text(
            encoding="utf-8"
        )
        common_source = common_source.replace(
            "KSA_CTK_SC_AUTH=/usr/sbin/sc_auth", f"KSA_CTK_SC_AUTH={fake_bin / 'sc_auth'}"
        ).replace(
            "KSA_CTK_SSH_KEYGEN=/usr/bin/ssh-keygen",
            f"KSA_CTK_SSH_KEYGEN={fake_bin / 'ssh-keygen'}",
        ).replace(
            "KSA_CTK_PROVIDER=/usr/lib/ssh-keychain.dylib",
            f"KSA_CTK_PROVIDER={fake_provider}",
        )
        (tool_lib / "approval-common.sh").write_text(common_source, encoding="utf-8")

        (profile / "profile").write_text(
            "schema_version=1\n"
            "purpose=static-site\n"
            "signer_id=human-static-site\n"
            "ctk_label=Klokast static-site approval\n"
            "ctk_hash=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
            "key_fingerprint=SHA256:fakefingerprint\n"
            "key_type=p-256-ne\n"
            "protection=bio\n"
            f"key_path={key_path}\n"
            f"ssh_keygen={fake_bin / 'ssh-keygen'}\n"
            f"sk_provider={fake_provider}\n",
            encoding="utf-8",
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "HOME": str(home),
                "FAKE_SSH_KEYGEN_LOG": str(fake_ssh_keygen_log),
                "FAKE_TOKEN_LOG": str(fake_token_log),
                "FAKE_REMOTE_ROOT": str(fake_remote_root),
                "FAKE_INTENT_DOMAIN": intent_domain,
                "FAKE_FINAL_STATUS_TRUE": "1" if final_status_true else "0",
            }
        )
        return env, fake_ssh_keygen_log, fake_token_log, wrapper

    def run_wrapper(self, wrapper, env, input_text):
        return subprocess.run(
            [
                str(wrapper),
                "--controller",
                "k002-ops",
                "--box",
                "k001",
                "--domain",
                "www.klokast.ai",
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
            env, ssh_keygen_log, token_log, wrapper = self.make_env(root)

            result = self.run_wrapper(wrapper, env, f"approve static-site www.klokast.ai\n{token}\n")

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
            env, ssh_keygen_log, token_log, wrapper = self.make_env(root)

            result = self.run_wrapper(wrapper, env, f"wrong phrase\n{token}\n")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("approval phrase mismatch", result.stderr)
            self.assertNotIn("-Y sign", ssh_keygen_log.read_text(encoding="utf-8"))
            self.assertFalse(token_log.exists())

    def test_mismatched_intent_domain_aborts_before_signing(self):
        token = self.fake_cloudflare_token()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env, ssh_keygen_log, token_log, wrapper = self.make_env(
                root, intent_domain="evil.example"
            )

            result = self.run_wrapper(wrapper, env, f"approve static-site www.klokast.ai\n{token}\n")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("intent domain mismatch", result.stderr)
            self.assertNotIn("-Y sign", ssh_keygen_log.read_text(encoding="utf-8"))
            self.assertFalse(token_log.exists())

    def test_final_status_must_report_cloudflare_token_configured(self):
        token = self.fake_cloudflare_token()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env, _ssh_keygen_log, _token_log, wrapper = self.make_env(
                root, final_status_true=False
            )

            result = self.run_wrapper(wrapper, env, f"approve static-site www.klokast.ai\n{token}\n")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cloudflare_token_configured=true", result.stderr)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
