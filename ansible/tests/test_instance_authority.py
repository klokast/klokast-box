#!/usr/bin/env python3
import importlib.util
import json
import os
import pwd
import stat
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "klokast-ops" / "secret-authority" / "bin" / "ksa-instance"
PLATFORM_INSTANCE = REPO_ROOT / "ansible" / "bin" / "platform-instance"
INSTALLER = REPO_ROOT / "klokast-dev" / "bin" / "install-instance-github-app"
PREPARE_HELPER = REPO_ROOT / "klokast-dev" / "bin" / "prepare-private-instance-bootstrap"
SIGN_HELPER = REPO_ROOT / "klokast-dev" / "bin" / "sign-secret-authority-intent"
ACTION_HELPER = REPO_ROOT / "klokast-dev" / "bin" / "run-private-instance-action"


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

    def test_register_intent_is_canonical_and_bound_to_public_commit(self):
        result = self.run_script(
            "intent", "instance", "register-repository",
            "--repo-owner", "family", "--repo-name", "klokast-instance",
            "--expires-at", "2099-01-01T00:00:00Z",
            "--nonce", "nonce_123456789",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        intent = json.loads(result.stdout)
        self.assertEqual(result.stdout, self.mod.canonical_json(intent))
        self.assertEqual(intent["action"], "register-repository")
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

    def test_github_request_reports_sanitized_validation_detail(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 422
        response.read.return_value = json.dumps({
            "message": "Validation Failed\u001b[31m",
            "errors": [{
                "resource": "PublicKey", "field": "key", "code": "custom",
                "message": "key is already in use",
            }],
            "token": "must-not-appear",
        }).encode("utf-8")
        with mock.patch.object(self.mod.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(self.mod.GithubRequestError) as raised:
                self.mod.github_request("secret-token", "POST", "/repos/family/repo/keys")
        self.assertEqual(raised.exception.status, 422)
        self.assertEqual(
            raised.exception.detail,
            "Validation Failed; key: key is already in use",
        )
        self.assertNotIn("must-not-appear", str(raised.exception))
        self.assertNotIn("secret-token", str(raised.exception))

    def test_private_instance_repository_name_is_exact(self):
        result = self.run_script(
            "intent", "instance", "register-repository",
            "--repo-owner", "family", "--repo-name", "klokast",
            "--expires-at", "2099-01-01T00:00:00Z",
            "--nonce", "nonce_123456789",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be exactly klokast-instance", result.stderr)

    def test_register_repository_verifies_exact_private_empty_repository(self):
        calls = []

        class App:
            def __init__(self, _root):
                pass

            def installation_token(self, permissions):
                calls.append(("permissions", permissions))
                return "token"

        def request(_token, method, path, body=None, expected=(200,)):
            calls.append((method, path, body, expected))
            if path.endswith("/keys?per_page=100"):
                return []
            return {
                "full_name": "family/klokast-instance",
                "private": True,
                "visibility": "private",
                "owner": {"login": "family", "type": "Organization"},
                "id": 123,
                "size": 0,
                "fork": False,
                "archived": False,
                "disabled": False,
                "is_template": False,
                "default_branch": "main",
            }

        with tempfile.TemporaryDirectory() as temporary:
            args = mock.Mock(
                repo_owner="family", repo_name="klokast-instance", state_root=temporary,
                config_root=temporary, signer_id="human", audit_log=Path(temporary) / "audit.jsonl",
            )
            with mock.patch.object(self.mod, "require_root"), mock.patch.object(
                self.mod, "require_active_controller"
            ), mock.patch.object(
                self.mod, "require_approval", return_value={"nonce": "nonce_123456789"}
            ), mock.patch.object(self.mod, "GithubApp", App), mock.patch.object(
                self.mod, "github_request", side_effect=request
            ), mock.patch.object(
                self.mod, "require_private_remote"
            ), mock.patch("sys.stdout"):
                self.mod.cmd_register_repository(args)
            state = json.loads((Path(temporary) / "repository.json").read_text(encoding="utf-8"))

        self.assertTrue(state["private"])
        self.assertIn("registered_at", state)
        self.assertEqual(calls[0], ("permissions", {"administration": "read", "metadata": "read"}))
        self.assertEqual(
            [(call[0], call[1]) for call in calls[1:]],
            [
                ("GET", "/repos/family/klokast-instance"),
                ("GET", "/repos/family/klokast-instance/keys?per_page=100"),
            ],
        )

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
            return {"id": 456, "key": "ssh-ed25519 public", "read_only": True}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            repository = "family/klokast-instance"
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
                "registered_at": "2026-01-01T00:00:00Z",
            }
            (state_root / "repository.json").write_text(self.mod.canonical_json(state), encoding="utf-8")
            args = mock.Mock(
                repo_owner="family", repo_name="klokast-instance", source_root=source_root,
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
            ), mock.patch.object(
                self.mod, "require_empty_authenticated_remote"
            ), mock.patch("sys.stdout"):
                self.mod.cmd_register_read_key(args)

        self.assertEqual(calls[-1][0], "POST")
        self.assertEqual(calls[-1][2]["read_only"], True)

    def test_register_deploy_key_explains_key_already_in_use(self):
        class App:
            def __init__(self, _root):
                pass

            def installation_token(self, _permissions):
                return "token"

        def request(_token, method, path, body=None, expected=(200,)):
            if method == "GET":
                return []
            raise self.mod.GithubRequestError(
                method,
                path,
                422,
                "Validation Failed; key: key is already in use",
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            repository = "family/klokast-instance"
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
                "schema_version": 1,
                "repository": repository,
                "repository_sha256": self.mod.repository_hash(repository),
                "repository_id": 123,
                "private": True,
                "registered_at": "2026-01-01T00:00:00Z",
            }
            (state_root / "repository.json").write_text(self.mod.canonical_json(state), encoding="utf-8")
            args = mock.Mock(
                repo_owner="family",
                repo_name="klokast-instance",
                source_root=source_root,
                state_root=state_root,
                config_root=root,
                key_fingerprint="SHA256:abcdefghijklmnopqrstuvwxyz1234567890",
                signer_id="human",
                audit_log=root / "audit.jsonl",
            )
            with mock.patch.object(self.mod, "require_root"), mock.patch.object(
                self.mod, "require_active_controller"
            ), mock.patch.object(
                self.mod, "require_approval", return_value={"nonce": "nonce_123456789"}
            ), mock.patch.object(
                self.mod, "fingerprint", return_value=args.key_fingerprint
            ), mock.patch.object(self.mod, "GithubApp", App), mock.patch.object(
                self.mod, "github_request", side_effect=request
            ):
                with self.assertRaisesRegex(
                    self.mod.InstanceAuthorityError,
                    "already attached to a GitHub account or repository",
                ) as raised:
                    self.mod.cmd_register_read_key(args)

        message = str(raised.exception)
        self.assertIn("family/klokast-instance (HTTP 422)", message)
        self.assertIn(args.key_fingerprint, message)
        self.assertIn("GitHub did not create the key", message)
        self.assertIn("only one repository", message)

    def test_register_deploy_key_removes_new_key_when_repository_has_refs(self):
        calls = []

        class App:
            def __init__(self, _root):
                pass

            def installation_token(self, _permissions):
                return "token"

        def request(_token, method, path, body=None, expected=(200,)):
            calls.append((method, path, body, expected))
            if method == "GET":
                return []
            if method == "POST":
                return {"id": 456, "key": "ssh-ed25519 public", "read_only": True}
            return None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            repository = "family/klokast-instance"
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
                "schema_version": 1,
                "repository": repository,
                "repository_sha256": self.mod.repository_hash(repository),
                "repository_id": 123,
                "private": True,
                "registered_at": "2026-01-01T00:00:00Z",
            }
            (state_root / "repository.json").write_text(self.mod.canonical_json(state), encoding="utf-8")
            args = mock.Mock(
                repo_owner="family",
                repo_name="klokast-instance",
                source_root=source_root,
                state_root=state_root,
                config_root=root,
                key_fingerprint="SHA256:abcdefghijklmnopqrstuvwxyz1234567890",
                signer_id="human",
                audit_log=root / "audit.jsonl",
            )
            with mock.patch.object(self.mod, "require_root"), mock.patch.object(
                self.mod, "require_active_controller"
            ), mock.patch.object(
                self.mod, "require_approval", return_value={"nonce": "nonce_123456789"}
            ), mock.patch.object(
                self.mod, "fingerprint", return_value=args.key_fingerprint
            ), mock.patch.object(self.mod, "GithubApp", App), mock.patch.object(
                self.mod, "github_request", side_effect=request
            ), mock.patch.object(
                self.mod,
                "require_empty_authenticated_remote",
                side_effect=self.mod.InstanceAuthorityError("private repository has refs"),
            ):
                with self.assertRaisesRegex(self.mod.InstanceAuthorityError, "has refs"):
                    self.mod.cmd_register_read_key(args)

        self.assertEqual(calls[-1][0:2], (
            "DELETE", "/repos/family/klokast-instance/keys/456"
        ))

    def test_source_receipt_is_canonical_root_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = mock.Mock(receipt_root=root, infra_user=pwd.getpwuid(os.getuid()).pw_name)
            repository = "family/klokast-instance"
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
        args = mock.Mock(repo_owner="family", repo_name="klokast-instance")
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
        self.assertNotIn("  - ksa-cloudflare", variables)
        self.assertIn("Remove retired Cloudflare guardian controller wrapper", tasks)
        self.assertIn("- path: /etc/klokast/private-instance", tasks)
        self.assertIn("- path: /var/lib/klokast/instance-sources", tasks)
        self.assertIn("Cmnd_Alias KLOKAST_INFRA_SECRET_AUTHORITY_WRAPPERS", tasks)
        self.assertIn("Cmnd_Alias KLOKAST_INFRA_TAILSCALE_WRAPPERS", tasks)

    def test_retired_cloudflare_guardian_is_not_exposed(self):
        self.assertFalse((REPO_ROOT / "ansible" / "bin" / "converge-cloudflare-guardian").exists())
        self.assertFalse((REPO_ROOT / "klokast-dev" / "bin" / "install-cloudflare-authority").exists())
        self.assertFalse(
            (REPO_ROOT / "klokast-ops" / "secret-authority" / "bin" / "ksa-cloudflare").exists()
        )
        completed = subprocess.run(
            [str(REPO_ROOT / "ansible" / "bin" / "secret-authority"), "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("cloudflare ACTION", completed.stdout)

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
        self.assertIn("Apple CryptoTokenKit keeps the key non-exportable", completed.stdout)
        self.assertIn("Do not create a passkey for this step", completed.stdout)
        self.assertIn("does not ask for a GitHub App PEM", completed.stdout)

    def test_private_instance_prepare_helper_preserves_authority_boundaries(self):
        source = PREPARE_HELPER.read_text(encoding="utf-8")
        self.assertIn('chmod 0600 "$session_tmp"', source)
        self.assertIn('--controller "$controller"', source)
        self.assertIn("--purpose private-instance", source)
        self.assertIn('"approval_signer":"human-private-instance"', source)
        self.assertIn('"allowed_signers_configured":true', source)
        self.assertIn('printf \'APPROVAL_PURPOSE=%q\\n\'', source)
        self.assertIn('printf \'APPROVAL_PUBLIC_KEY=%q\\n\'', source)
        self.assertIn('printf \'APPROVAL_CTK_HASH=%q\\n\'', source)
        self.assertIn('DEFAULT_REPO_NAME="klokast-instance"', source)
        self.assertIn('repo="$DEFAULT_REPO_NAME"', source)
        self.assertNotIn('prompt_default "Private repository name"', source)
        self.assertIn('confirm_yes "Use these settings?"', source)
        self.assertNotIn('Continue with Step 1?', source)
        self.assertNotIn("GITHUB_APP_PRIVATE_KEY", source)
        self.assertNotIn("init-values.json", source)
        completed = subprocess.run(
            ["bash", "-n", str(PREPARE_HELPER)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_approval_installer_uses_native_scoped_touch_id_signing(self):
        installer = (
            REPO_ROOT / "klokast-dev" / "bin" /
            "install-secret-authority-approval-signer"
        )
        common = (
            REPO_ROOT / "klokast-dev" / "lib" / "approval-common.sh"
        ).read_text(encoding="utf-8")
        source = installer.read_text(encoding="utf-8")
        self.assertIn("--purpose private-instance|static-site", source)
        self.assertIn("--finalize-migration", source)
        self.assertIn("allowed-signers-private-instance", source)
        self.assertIn("allowed-signers-static-site", source)
        self.assertIn("Touch ID approved. Sending the public signer check", source)
        self.assertIn("Touch ID approved. Verifying the installed", source)
        self.assertIn("KSA_CTK_SC_AUTH=/usr/sbin/sc_auth", common)
        self.assertIn("KSA_CTK_SSH_KEYGEN=/usr/bin/ssh-keygen", common)
        self.assertIn("KSA_CTK_SSH_AGENT=/usr/bin/ssh-agent", common)
        self.assertIn("KSA_CTK_SSH_ADD=/usr/bin/ssh-add", common)
        self.assertIn("KSA_CTK_PROVIDER=/usr/lib/ssh-keychain.dylib", common)
        self.assertIn("-l \"$ctk_label\" -k p-256-ne -t bio", common)
        self.assertIn("ephemeral-apple-agent", common)
        self.assertIn("Apple OpenSSH can take a short time", common)
        self.assertIn('candidate_fingerprint="$(', common)
        self.assertIn('KEYCHAIN_CERTIFICATES="$ctk_hash"', common)
        self.assertIn('"$KSA_CTK_SSH_KEYGEN" -q -w "$KSA_CTK_PROVIDER" -Y sign', common)
        self.assertLess(
            source.index('doas ssh-keygen -Y verify'),
            source.index('doas install -m 0600 -o root -g root "$candidate" "$staged"'),
        )
        self.assertLess(
            source.index('verify_installed_purpose private-instance'),
            source.index('doas rm -f /etc/klokast/secret-authority/allowed-signers'),
        )
        completed = subprocess.run(
            ["bash", "-n", str(installer)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        sign_source = SIGN_HELPER.read_text(encoding="utf-8")
        self.assertIn("--purpose private-instance|static-site", sign_source)
        self.assertIn('ksa_load_touchid_profile "$purpose"', sign_source)
        self.assertIn('ksa_sign_touchid_message "$intent"', sign_source)
        completed = subprocess.run(
            ["bash", "-n", str(SIGN_HELPER)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_private_instance_rejects_the_static_site_signer(self):
        args = mock.Mock(
            approval_intent="intent.json",
            approval_signature="intent.json.sig",
            signer_id="human-static-site",
        )
        with self.assertRaisesRegex(
            self.mod.InstanceAuthorityError,
            "private-instance approvals require signer ID human-private-instance",
        ):
            self.mod.require_approval(args)

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
        self.assertIn("The repository name is `klokast-instance`", runbook)
        self.assertIn("Create the exact empty private repository", runbook)
        self.assertIn("Select only `klokast-instance`", runbook)
        self.assertIn(
            "klokast-dev/bin/run-private-instance-action register-repository",
            runbook,
        )
        self.assertIn(
            "klokast-dev/bin/run-private-instance-action register-read-key",
            runbook,
        )
        self.assertIn(
            "klokast-dev/bin/run-private-instance-action retire-bootstrap",
            runbook,
        )
        self.assertNotIn("create-repository", runbook)
        self.assertNotIn("Leave the repository selection empty", runbook)

    def test_private_instance_action_helper_keeps_human_approval_boundary(self):
        source = ACTION_HELPER.read_text(encoding="utf-8")
        self.assertIn(
            "register-repository|register-read-key|retire-bootstrap",
            source,
        )
        self.assertIn("Sign and run this exact action? [y/N]", source)
        self.assertIn('"$SCRIPT_DIR/sign-secret-authority-intent"', source)
        self.assertIn("intent fields do not match the closed schema", source)
        self.assertIn("intent is not canonical JSON", source)
        self.assertIn("test \"$(git rev-parse HEAD)\" = \"$expected\"", source)
        self.assertIn("klokast-controller-guard --status --json --require-active", source)
        self.assertIn('EXPECTED_REPO_NAME="klokast-instance"', source)
        self.assertNotIn("github-app.pem", source)

        completed = subprocess.run(
            ["bash", "-n", str(ACTION_HELPER)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        completed = subprocess.run(
            [str(ACTION_HELPER), "--help"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Runs one human-approved", completed.stdout)


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
        args = mock.Mock(action="sync", repo_owner="family", repo_name="klokast-instance")
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
