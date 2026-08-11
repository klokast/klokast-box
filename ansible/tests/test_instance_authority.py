#!/usr/bin/env python3
import importlib.util
import json
import os
import pwd
import stat
import subprocess
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "klokast-ops" / "secret-authority" / "bin" / "ksa-instance"
PLATFORM_INSTANCE = REPO_ROOT / "ansible" / "bin" / "platform-instance"
INSTALLER = REPO_ROOT / "klokast-dev" / "bin" / "install-instance-github-app"
PREPARE_HELPER = REPO_ROOT / "klokast-dev" / "bin" / "prepare-private-instance-bootstrap"


def load(path, name):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class InstanceAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.mod = load(SCRIPT, "ksa_instance")

    def run_script(self, *arguments):
        return subprocess.run(
            [str(SCRIPT), "--repo-root", str(REPO_ROOT), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_create_intent_is_canonical_and_bound_to_public_commit(self):
        result = self.run_script(
            "intent", "instance", "create-repository",
            "--repo-owner", "family", "--repo-name", "klokast",
            "--expires-at", "2099-01-01T00:00:00Z",
            "--nonce", "nonce_123456789",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        intent = json.loads(result.stdout)
        self.assertEqual(result.stdout, self.mod.canonical_json(intent))
        self.assertEqual(intent["action"], "create-repository")
        self.assertEqual(intent["repo_head"], subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip())

    def test_github_app_refuses_contents_permission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "github-app.env").write_text(
                "GITHUB_APP_ID=1\nGITHUB_APP_INSTALLATION_ID=2\n", encoding="utf-8"
            )
            app = self.mod.GithubApp(root)
            with self.assertRaisesRegex(self.mod.InstanceAuthorityError, "Contents"):
                app.installation_token({"administration": "write", "contents": "read"})

    def test_create_repository_is_private_empty_and_uses_no_contents_permission(self):
        calls = []

        class App:
            def __init__(self, _root):
                pass

            def installation_token(self, permissions):
                calls.append(("permissions", permissions))
                return "token"

        def request(_token, method, path, body=None, expected=(200,)):
            calls.append((method, path, body, expected))
            return {"full_name": "family/klokast", "private": True, "id": 123}

        with tempfile.TemporaryDirectory() as temporary:
            args = mock.Mock(
                repo_owner="family", repo_name="klokast", state_root=temporary,
                config_root=temporary, signer_id="human", audit_log=Path(temporary) / "audit.jsonl",
            )
            with mock.patch.object(self.mod, "require_root"), mock.patch.object(
                self.mod, "require_active_controller"
            ), mock.patch.object(
                self.mod, "require_approval", return_value={"nonce": "nonce_123456789"}
            ), mock.patch.object(self.mod, "GithubApp", App), mock.patch.object(
                self.mod, "github_request", side_effect=request
            ), mock.patch("sys.stdout"):
                self.mod.cmd_create_repository(args)
            state = json.loads((Path(temporary) / "repository.json").read_text(encoding="utf-8"))

        self.assertTrue(state["private"])
        self.assertEqual(calls[0], ("permissions", {"administration": "write", "metadata": "read"}))
        create = calls[1]
        self.assertEqual((create[0], create[1]), ("POST", "/orgs/family/repos"))
        self.assertEqual(create[2]["private"], True)
        self.assertEqual(create[2]["auto_init"], False)

    def test_register_deploy_key_requests_read_only_key(self):
        calls = []

        class App:
            def __init__(self, _root):
                pass

            def installation_token(self, permissions):
                return "token"

        def request(_token, method, path, body=None, expected=(200,)):
            calls.append((method, path, body, expected))
            if method == "GET":
                return []
            return {"key": "ssh-ed25519 public", "read_only": True}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            repository = "family/klokast"
            source = {
                "schema_version": 1,
                "repository": repository,
                "repository_sha256": self.mod.repository_hash(repository),
            }
            (source_root / "source.json").write_text(self.mod.canonical_json(source), encoding="utf-8")
            (source_root / "github-readonly.pub").write_text("ssh-ed25519 public\n", encoding="utf-8")
            state_root = root / "state"
            state_root.mkdir()
            state = {
                "schema_version": 1, "repository": repository,
                "repository_sha256": self.mod.repository_hash(repository),
                "repository_id": 123, "private": True,
                "created_at": "2026-01-01T00:00:00Z",
            }
            (state_root / "repository.json").write_text(self.mod.canonical_json(state), encoding="utf-8")
            args = mock.Mock(
                repo_owner="family", repo_name="klokast", source_root=source_root,
                state_root=state_root, config_root=root, key_fingerprint="SHA256:abcdefghijklmnopqrstuvwxyz1234567890",
                signer_id="human", audit_log=root / "audit.jsonl",
            )
            with mock.patch.object(self.mod, "require_root"), mock.patch.object(
                self.mod, "require_active_controller"
            ), mock.patch.object(
                self.mod, "require_approval", return_value={"nonce": "nonce_123456789"}
            ), mock.patch.object(
                self.mod, "fingerprint", return_value=args.key_fingerprint
            ), mock.patch.object(self.mod, "GithubApp", App), mock.patch.object(
                self.mod, "github_request", side_effect=request
            ), mock.patch("sys.stdout"):
                self.mod.cmd_register_read_key(args)

        self.assertEqual(calls[-1][0], "POST")
        self.assertEqual(calls[-1][2]["read_only"], True)

    def test_source_receipt_is_canonical_root_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = mock.Mock(receipt_root=root, infra_user=pwd.getpwuid(os.getuid()).pw_name)
            repository = "family/klokast"
            state = {"repository": repository, "repository_id": 123}
            source = {"repository_sha256": self.mod.repository_hash(repository)}
            with mock.patch.object(self.mod.os, "chown"):
                path, receipt = self.mod.write_source_receipt(
                    args, state, source, "a" * 40,
                    "SHA256:abcdefghijklmnopqrstuvwxyz1234567890",
                )
            content = json.loads(path.read_text(encoding="utf-8"))
            unhashed = dict(content)
            supplied = unhashed.pop("receipt_sha256")
            actual = self.mod.hashlib.sha256(
                self.mod.canonical_json(unhashed).rstrip("\n").encode("utf-8")
            ).hexdigest()
            self.assertEqual(supplied, actual)
            self.assertFalse(receipt["anonymous_readable"])
            self.assertTrue(receipt["authenticated_readable"])
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o750)

    def test_private_probe_retries_and_refuses_any_anonymous_success(self):
        args = mock.Mock(repo_owner="family", repo_name="klokast")
        failed = mock.Mock(returncode=128)
        with mock.patch.object(self.mod, "run", return_value=failed) as runner:
            self.mod.require_private_remote(args)
        self.assertEqual(runner.call_count, 3)
        succeeded = mock.Mock(returncode=0)
        with mock.patch.object(self.mod, "run", return_value=succeeded):
            with self.assertRaisesRegex(self.mod.InstanceAuthorityError, "anonymous"):
                self.mod.require_private_remote(args)

    def test_checkout_ownership_does_not_follow_tracked_symlinks(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("os.lchown(Path(current) / name", source)

    def test_controller_role_installs_wrapper_and_private_boundaries(self):
        variables = (REPO_ROOT / "ansible" / "inventory" / "group_vars" / "ops.yml").read_text(
            encoding="utf-8"
        )
        tasks = (REPO_ROOT / "ansible" / "roles" / "ops-controller" / "tasks" / "main.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("  - ksa-instance", variables)
        self.assertIn("- path: /etc/klokast/private-instance", tasks)
        self.assertIn("- path: /var/lib/klokast/instance-sources", tasks)
        self.assertIn("Cmnd_Alias KLOKAST_INFRA_SECRET_AUTHORITY_WRAPPERS", tasks)
        self.assertIn("Cmnd_Alias KLOKAST_INFRA_TAILSCALE_WRAPPERS", tasks)

    def test_workstation_installer_sends_pem_only_through_standard_input(self):
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('base64 < "$pem" | tailscale ssh', source)
        self.assertNotIn('tailscale ssh "$ssh_target" "$pem"', source)
        completed = subprocess.run(
            ["bash", "-n", str(INSTALLER)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_private_instance_prepare_helper_starts_with_prerequisites(self):
        completed = subprocess.run(
            ["bash", str(PREPARE_HELPER), "--help"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.startswith(
            "Private instance bootstrap prerequisites\n"
        ))
        self.assertLess(
            completed.stdout.index("Prepare these items before you continue:"),
            completed.stdout.index("What this helper will do"),
        )
        self.assertIn("Touch ID configured on this MacBook", completed.stdout)
        self.assertIn("Public Key Path shown for that key in Secretive", completed.stdout)
        self.assertIn("Do not create a passkey for this step", completed.stdout)
        self.assertIn("does not ask for a GitHub App PEM", completed.stdout)
        self.assertNotIn("YubiKey", completed.stdout)

    def test_private_instance_prepare_helper_preserves_authority_boundaries(self):
        source = PREPARE_HELPER.read_text(encoding="utf-8")
        self.assertIn('chmod 0600 "$session_tmp"', source)
        self.assertIn('--controller "$controller"', source)
        self.assertIn('--signer-id "$signer_id"', source)
        self.assertIn('--agent-key "$approval_public_key"', source)
        self.assertIn('--agent-socket "$agent_socket"', source)
        self.assertIn('printf \'APPROVAL_AGENT_SOCKET=%q\\n\'', source)
        self.assertNotIn("GITHUB_APP_PRIVATE_KEY", source)
        self.assertNotIn("init-values.json", source)
        completed = subprocess.run(
            ["bash", "-n", str(PREPARE_HELPER)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_approval_installer_supports_touch_id_agent_signing(self):
        installer = (
            REPO_ROOT / "klokast-dev" / "bin" /
            "install-secret-authority-approval-signer"
        )
        source = installer.read_text(encoding="utf-8")
        self.assertIn("--agent-key PUBLIC_KEY", source)
        self.assertIn('SSH_AUTH_SOCK="$agent_socket" ssh-add -L', source)
        self.assertIn('"$ssh_keygen" -q -Y sign -U', source)
        self.assertIn("must be a P-256 Secure Enclave SSH public key", source)
        completed = subprocess.run(
            ["bash", "-n", str(installer)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_approval_installer_signs_through_selected_ssh_agent(self):
        installer = (
            REPO_ROOT / "klokast-dev" / "bin" /
            "install-secret-authority-approval-signer"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / "approval"
            generated = subprocess.run(
                ["ssh-keygen", "-q", "-t", "ecdsa", "-b", "256", "-N", "", "-f", str(key)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)

            socket = root / "agent.sock"
            agent = subprocess.Popen(
                ["ssh-agent", "-D", "-a", str(socket)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                for _ in range(100):
                    if socket.exists():
                        break
                    time.sleep(0.01)
                self.assertTrue(socket.exists(), "ssh-agent socket was not created")

                env = os.environ.copy()
                env["SSH_AUTH_SOCK"] = str(socket)
                added = subprocess.run(
                    ["ssh-add", str(key)], env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
                self.assertEqual(added.returncode, 0, added.stderr)

                fake_bin = root / "bin"
                fake_bin.mkdir()
                tailscale = fake_bin / "tailscale"
                tailscale.write_text(
                    """#!/bin/sh
set -eu
[ "${1:-}" = ssh ] || exit 2
shift
target=$1
shift
command="$*"
case "$command" in
  "mktemp /tmp/ksa-allowed-signer.XXXXXX")
    printf '/tmp/ksa-allowed-signer.fake\\n'
    ;;
  "mktemp -d /tmp/ksa-approval-signer.XXXXXX")
    printf '/tmp/ksa-approval-signer.fake\\n'
    ;;
  *"status --redacted"*)
    printf '{"allowed_signers_configured":true}\\n'
    ;;
  sh\ -s\ --*)
    cat >/dev/null
    ;;
  cat\ \>*)
    cat >/dev/null
    ;;
  *)
    printf 'unexpected tailscale command for %s: %s\\n' "$target" "$command" >&2
    exit 2
    ;;
esac
""",
                    encoding="utf-8",
                )
                tailscale.chmod(0o755)
                env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
                env["TMPDIR"] = str(root)

                completed = subprocess.run(
                    [
                        str(installer), "--controller", "k002-ops",
                        "--signer-id", "human", "--agent-key", f"{key}.pub",
                        "--agent-socket", str(socket), "--ssh-keygen", "ssh-keygen",
                    ],
                    env=env, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn('"allowed_signers_configured":true', completed.stdout)
                self.assertIn("Approve the test signature with Touch ID", completed.stderr)
                self.assertIn("Approval signer signature check ok", completed.stderr)
            finally:
                agent.terminate()
                agent.wait(timeout=5)
                if agent.stderr is not None:
                    agent.stderr.close()

    def test_private_instance_runbook_uses_prepare_helper_session(self):
        runbook = (
            REPO_ROOT / "klokast-dev" / "runbooks" /
            "40-private-instance-bootstrap.md"
        ).read_text(encoding="utf-8")
        self.assertIn("klokast-dev/bin/prepare-private-instance-bootstrap", runbook)
        self.assertIn(
            'source "$HOME/.local/share/klokast/private-instance-bootstrap/session.sh"',
            runbook,
        )


class PlatformInstanceTest(unittest.TestCase):
    def setUp(self):
        self.mod = load(PLATFORM_INSTANCE, "platform_instance")

    def test_seed_destination_must_be_private_new_and_not_deployment_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(self.mod, "PRIVATE_ROOT", root), mock.patch.object(
                self.mod, "DEPLOYMENT_CHECKOUT", root / "instance"
            ):
                self.assertEqual(self.mod.resolve_private_destination(str(root / "seed")), root / "seed")
                with self.assertRaisesRegex(self.mod.InstanceError, "deployment checkout"):
                    self.mod.resolve_private_destination(str(root / "instance"))

    def test_source_actions_use_only_installed_root_wrapper(self):
        args = mock.Mock(action="sync", repo_owner="family", repo_name="klokast")
        completed = mock.Mock(returncode=0)
        with mock.patch.object(self.mod, "require_controller_user"), mock.patch.object(
            self.mod.Path, "is_file", return_value=True
        ), mock.patch.object(self.mod.os, "access", return_value=True), mock.patch.object(
            self.mod, "run", return_value=completed
        ) as runner:
            self.mod.authority(args)
        command = runner.call_args.args[0]
        self.assertEqual(command[:3], ["sudo", "-n", self.mod.INSTALLED_AUTHORITY])
        self.assertNotIn("git", command)

    def test_seed_accepts_only_successful_init_at_the_requested_private_path(self):
        class Plan:
            class PlanError(Exception):
                pass

            @staticmethod
            def require_active_controller():
                return None

            @staticmethod
            def resolve_build_directory(_value):
                return Path("/verified/build"), "a" * 40

            @staticmethod
            def verify_build_directory(_directory, _commit):
                return {}, Path("/verified/klokast")

            @staticmethod
            def verify_binary_version(_binary, _receipt):
                return None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = root / "values.json"
            values.write_text("{}\n", encoding="utf-8")
            destination = root / "seed"
            args = mock.Mock(build_dir="/verified/build", values=str(values), destination=str(destination))
            completed = mock.Mock(
                returncode=0,
                stdout=json.dumps({"created": True, "instance_path": str(destination)}),
                stderr="",
            )
            with mock.patch.object(self.mod, "require_controller_user"), mock.patch.object(
                self.mod, "PRIVATE_ROOT", root
            ), mock.patch.object(
                self.mod, "DEPLOYMENT_CHECKOUT", root / "instance"
            ), mock.patch.object(
                self.mod, "load_plan_wrapper", return_value=Plan
            ), mock.patch.object(self.mod, "run", return_value=completed) as runner, mock.patch(
                "builtins.print"
            ):
                self.mod.seed(args)
        command = runner.call_args.args[0]
        self.assertEqual(command[1:3], ["init", "--instance"])
        self.assertIn(destination, command)


if __name__ == "__main__":
    unittest.main()
