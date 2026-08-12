#!/usr/bin/env python3
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
COMMON = REPO_ROOT / "klokast-dev" / "lib" / "approval-common.sh"


class TouchIDApprovalTest(unittest.TestCase):
    def write_executable(self, path, content):
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        path.chmod(0o755)

    def native_fakes(self, root):
        fake_bin = root / "bin"
        fake_bin.mkdir(exist_ok=True)
        state = root / "identity-created"
        calls = root / "calls"
        provider = root / "ssh-keychain.dylib"
        provider.write_text("provider", encoding="utf-8")

        self.write_executable(fake_bin / "uname", "#!/bin/sh\nprintf 'Darwin\\n'\n")
        self.write_executable(
            fake_bin / "sc_auth",
            """\
            #!/bin/sh
            set -eu
            printf 'sc_auth %s\\n' "$*" >> "$FAKE_CALLS"
            case "${1:-}" in
              '') printf 'create-ctk-identity\\nlist-ctk-identities\\n' ;;
              identities)
                if [ -f "$FAKE_STATE" ]; then
                  printf 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\\tKlokast private-instance approval\\n'
                fi
                ;;
              create-ctk-identity)
                : > "$FAKE_STATE"
                ;;
              *) exit 2 ;;
            esac
            """,
        )
        self.write_executable(
            fake_bin / "ssh-keygen",
            """\
            #!/bin/sh
            set -eu
            printf 'ssh-keygen cert=%s %s\\n' "${KEYCHAIN_CERTIFICATES:-}" "$*" >> "$FAKE_CALLS"
            case " $* " in
              *" -? "*) printf 'usage: ssh-keygen -K -Y -w provider\\n' >&2; exit 1 ;;
              *" -K "*)
                printf 'handle\\n' > id_ecdsa_sk_rk
                printf 'sk-ecdsa-sha2-nistp256@openssh.com AAAAfake private-instance\\n' > id_ecdsa_sk_rk.pub
                exit 0
                ;;
              *" -E sha256 -lf "*)
                printf '256 SHA256:testfingerprint test (ECDSA-SK)\\n'
                exit 0
                ;;
              *" -Y sign "*)
                last=''
                for arg in "$@"; do last="$arg"; done
                printf 'signature\\n' > "${last}.sig"
                exit 0
                ;;
            esac
            exit 2
            """,
        )
        return fake_bin, state, calls, provider

    def run_common(self, root, script, input_text=""):
        fake_bin, state, calls, provider = self.native_fakes(root)
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "HOME": str(root / "home"),
                "FAKE_STATE": str(state),
                "FAKE_CALLS": str(calls),
            }
        )
        harness = f"""
set -eu
. {COMMON}
KSA_TOOL_NAME=test-touchid
KSA_CTK_SC_AUTH={fake_bin / 'sc_auth'}
KSA_CTK_SSH_KEYGEN={fake_bin / 'ssh-keygen'}
KSA_CTK_PROVIDER={provider}
KSA_APPROVAL_PROFILE_ROOT={root / 'profiles'}
SIGNATURE_NAMESPACE=klokast-secret-authority
tmpdir={root / 'work'}
mkdir -p "$tmpdir"
{script}
"""
        return subprocess.run(
            ["bash", "-c", harness],
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        ), calls

    def test_native_profile_is_created_reused_and_signed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            completed, calls = self.run_common(
                root,
                """
ksa_ensure_touchid_profile private-instance "$tmpdir"
first_fingerprint=$key_fingerprint
ksa_ensure_touchid_profile private-instance "$tmpdir"
test "$key_fingerprint" = "$first_fingerprint"
printf 'intent\\n' > "$tmpdir/intent"
ksa_sign_touchid_message "$tmpdir/intent"
""",
                "y\n",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            profile = root / "profiles" / "private-instance"
            manifest = (profile / "profile").read_text(encoding="utf-8")
            self.assertIn("signer_id=human-private-instance", manifest)
            self.assertIn("key_type=p-256-ne", manifest)
            self.assertIn("protection=bio", manifest)
            self.assertEqual(stat.S_IMODE((profile / "profile").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((profile / "id_ecdsa_sk_rk").stat().st_mode), 0o600)
            call_text = calls.read_text(encoding="utf-8")
            self.assertEqual(call_text.count("create-ctk-identity"), 1)
            self.assertEqual(call_text.count(" -K "), 1)
            self.assertIn("cert=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", call_text)
            self.assertIn(" -Y sign ", call_text)

    def test_duplicate_exact_labels_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin, _state, _calls, _provider = self.native_fakes(root)
            self.write_executable(
                fake_bin / "sc_auth",
                """\
                #!/bin/sh
                case "${1:-}" in
                  identities)
                    printf 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\\tKlokast private-instance approval\\n'
                    printf 'BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\\tKlokast private-instance approval\\n'
                    ;;
                  *) printf 'create-ctk-identity\\nlist-ctk-identities\\n' ;;
                esac
                """,
            )
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            harness = f"""
. {COMMON}
KSA_TOOL_NAME=test-touchid
KSA_CTK_SC_AUTH={fake_bin / 'sc_auth'}
ksa_select_touchid_profile private-instance
ksa_exact_identity_hash
"""
            completed = subprocess.run(
                ["bash", "-c", harness], text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("more than one CryptoTokenKit identity", completed.stderr)

    def test_untracked_existing_identity_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "identity-created").touch()
            completed, _calls = self.run_common(
                root,
                "ksa_ensure_touchid_profile private-instance \"$tmpdir\"",
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("identity exists without Klokast profile metadata", completed.stderr)

    def test_profile_fingerprint_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            completed, _calls = self.run_common(
                root,
                "ksa_ensure_touchid_profile private-instance \"$tmpdir\"",
                "y\n",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = root / "profiles" / "private-instance" / "profile"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "key_fingerprint=SHA256:testfingerprint",
                    "key_fingerprint=SHA256:different",
                ),
                encoding="utf-8",
            )

            completed, _calls = self.run_common(
                root,
                "ksa_load_touchid_profile private-instance",
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("public key does not match its profile", completed.stderr)


if __name__ == "__main__":
    unittest.main()
