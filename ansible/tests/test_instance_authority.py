#!/usr/bin/env python3
import importlib.util
import json
import os
import pwd
import stat
import subprocess
import sys
import tempfile
import types
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
PROMOTION_HELPER = REPO_ROOT / "klokast-dev" / "bin" / "promote-private-instance-engine"


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
        return self.run_script_for_repo(REPO_ROOT, *arguments)

    def run_script_for_repo(self, repo_root, *arguments):
        return subprocess.run(
            [str(SCRIPT), "--repo-root", str(repo_root), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def approval_intent(self, action):
        return {
            "action": action,
            "repo_owner": "family",
            "repo_name": "klokast-instance",
            "repo_head": "a" * 40,
            "engine_commit": "b" * 40,
            "nonce": "nonce_123456789",
        }

    @staticmethod
    def make_reviewed_repo(root):
        subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "Klokast test"], check=True)
        subprocess.run(
            ["git", "-C", root, "config", "user.email", "test@klokast.invalid"],
            check=True,
        )
        tracked = Path(root) / "tracked"
        tracked.write_text("first\n", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", "tracked"], check=True)
        subprocess.run(["git", "-C", root, "commit", "-qm", "first"], check=True)
        approved = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "HEAD"], text=True
        ).strip()
        tracked.write_text("second\n", encoding="utf-8")
        subprocess.run(["git", "-C", root, "commit", "-qam", "second"], check=True)
        current = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "HEAD"], text=True
        ).strip()
        return approved, current

    def test_register_intent_is_canonical_and_bound_to_approved_engine(self):
        with tempfile.TemporaryDirectory() as temporary:
            approved_engine, current_commit = self.make_reviewed_repo(temporary)
            result = self.run_script_for_repo(
                temporary,
                "intent", "instance", "register-repository",
                "--repo-owner", "family", "--repo-name", "klokast-instance",
                "--engine-commit", approved_engine,
                "--expires-at", "2099-01-01T00:00:00Z",
                "--nonce", "nonce_123456789",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        intent = json.loads(result.stdout)
        self.assertEqual(result.stdout, self.mod.canonical_json(intent))
        self.assertEqual(intent["action"], "register-repository")
        self.assertEqual(intent["schema_version"], 2)
        self.assertEqual(intent["repo_head"], current_commit)
        self.assertEqual(intent["engine_commit"], approved_engine)
        self.assertNotEqual(approved_engine, current_commit)

    def test_register_intent_rejects_unavailable_engine(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.make_reviewed_repo(temporary)
            result = self.run_script_for_repo(
                temporary,
                "intent", "instance", "register-repository",
                "--repo-owner", "family", "--repo-name", "klokast-instance",
                "--engine-commit", "a" * 40,
                "--expires-at", "2099-01-01T00:00:00Z",
                "--nonce", "nonce_123456789",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("approved engine commit is not available", result.stderr)

    def test_register_intent_rejects_changed_controller_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            approved_engine, _current_commit = self.make_reviewed_repo(temporary)
            (Path(temporary) / "untracked").write_text("change\n", encoding="utf-8")
            result = self.run_script_for_repo(
                temporary,
                "intent", "instance", "register-repository",
                "--repo-owner", "family", "--repo-name", "klokast-instance",
                "--engine-commit", approved_engine,
                "--expires-at", "2099-01-01T00:00:00Z",
                "--nonce", "nonce_123456789",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("current controller checkout has changes", result.stderr)

    def test_github_app_refuses_contents_permission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "github-app.env").write_text(
                "GITHUB_APP_ID=1\nGITHUB_APP_INSTALLATION_ID=2\n", encoding="utf-8"
            )
            app = self.mod.GithubApp(root)
            with self.assertRaisesRegex(self.mod.InstanceAuthorityError, "Contents"):
                app.installation_token({"administration": "write", "contents": "read"})

    def test_github_app_proves_uninstalled_installation_with_exact_404(self):
        calls = {}

        class FakeGithubException(Exception):
            def __init__(self, status):
                self.status = status

        class FakeUnknownObjectException(FakeGithubException):
            pass

        class FakeAppAuth:
            def __init__(self, app_id, private_key):
                calls["app_id"] = app_id
                calls["private_key"] = private_key

        class FakeAuth:
            AppAuth = FakeAppAuth

        class FakeGithubIntegration:
            def __init__(self, auth):
                calls["auth"] = auth

            def get_app_installation(self, installation_id):
                calls["installation_id"] = installation_id
                raise FakeUnknownObjectException(404)

            def get_installations(self):
                calls["listed_installations"] = True
                return []

        fake_github = types.SimpleNamespace(
            Auth=FakeAuth,
            GithubException=FakeGithubException,
            GithubIntegration=FakeGithubIntegration,
            UnknownObjectException=FakeUnknownObjectException,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "github-app.env").write_text(
                "GITHUB_APP_ID=123456\nGITHUB_APP_INSTALLATION_ID=12345678\n",
                encoding="utf-8",
            )
            (root / "github-app.pem").write_text("private-key\n", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"github": fake_github}):
                present = self.mod.GithubApp(root).installation_present()

        self.assertFalse(present)
        self.assertEqual(calls["app_id"], 123456)
        self.assertEqual(calls["installation_id"], 12345678)
        self.assertEqual(calls["private_key"], "private-key\n")
        self.assertIsInstance(calls["auth"], FakeAppAuth)
        self.assertTrue(calls["listed_installations"])

    def test_github_app_refuses_reinstalled_app_with_a_new_installation(self):
        class FakeGithubException(Exception):
            def __init__(self, status):
                self.status = status

        class FakeUnknownObjectException(FakeGithubException):
            pass

        class FakeAuth:
            class AppAuth:
                def __init__(self, _app_id, _private_key):
                    pass

        class FakeGithubIntegration:
            def __init__(self, auth):
                pass

            def get_app_installation(self, _installation_id):
                raise FakeUnknownObjectException(404)

            def get_installations(self):
                return [mock.Mock(id=87654321)]

        fake_github = types.SimpleNamespace(
            Auth=FakeAuth,
            GithubException=FakeGithubException,
            GithubIntegration=FakeGithubIntegration,
            UnknownObjectException=FakeUnknownObjectException,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "github-app.env").write_text(
                "GITHUB_APP_ID=123456\nGITHUB_APP_INSTALLATION_ID=12345678\n",
                encoding="utf-8",
            )
            (root / "github-app.pem").write_text("private-key\n", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"github": fake_github}), self.assertRaisesRegex(
                self.mod.InstanceAuthorityError,
                "still has another installation",
            ):
                self.mod.GithubApp(root).installation_present()

    def test_github_app_refuses_non_404_installation_failure(self):
        class FakeGithubException(Exception):
            def __init__(self, status):
                self.status = status

        class FakeUnknownObjectException(FakeGithubException):
            pass

        class FakeAuth:
            class AppAuth:
                def __init__(self, _app_id, _private_key):
                    pass

        class FakeGithubIntegration:
            def __init__(self, auth):
                pass

            def get_app_installation(self, _installation_id):
                raise FakeGithubException(403)

        fake_github = types.SimpleNamespace(
            Auth=FakeAuth,
            GithubException=FakeGithubException,
            GithubIntegration=FakeGithubIntegration,
            UnknownObjectException=FakeUnknownObjectException,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "github-app.env").write_text(
                "GITHUB_APP_ID=123456\nGITHUB_APP_INSTALLATION_ID=12345678\n",
                encoding="utf-8",
            )
            (root / "github-app.pem").write_text("private-key\n", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"github": fake_github}), self.assertRaisesRegex(
                self.mod.InstanceAuthorityError,
                "installation verification failed",
            ):
                self.mod.GithubApp(root).installation_present()

    def test_retire_bootstrap_accepts_verified_uninstalled_installation(self):
        class App:
            def __init__(self, _root):
                pass

            def installation_present(self):
                return False

            def installation_token(self, _permissions):
                raise AssertionError("an uninstalled App must not mint an installation token")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("github-app.env", "github-app.pem"):
                (root / name).write_text("temporary credential\n", encoding="utf-8")
            public = root / "github-readonly.pub"
            public.write_text("ssh-ed25519 public\n", encoding="utf-8")
            fingerprint = "SHA256:abcdefghijklmnopqrstuvwxyz1234567890"
            args = mock.Mock(
                config_root=root,
                key_fingerprint=fingerprint,
                signer_id="human-private-instance",
                audit_log=root / "audit.jsonl",
            )
            intent = self.approval_intent("retire-bootstrap")
            source = {"repository_sha256": "a" * 64}
            state = {"repository_id": 123}
            with mock.patch.object(self.mod, "require_root"), mock.patch.object(
                self.mod, "require_active_controller"
            ), mock.patch.object(
                self.mod, "require_approval", return_value=intent
            ), mock.patch.object(
                self.mod, "load_source_config", return_value=({"public": public}, source)
            ), mock.patch.object(
                self.mod, "load_repository_state", return_value=state
            ), mock.patch.object(
                self.mod, "fingerprint", return_value=fingerprint
            ), mock.patch.object(
                self.mod, "GithubApp", App
            ), mock.patch.object(
                self.mod, "app_repository_ids"
            ) as repository_ids, mock.patch.object(
                self.mod, "remote_head", return_value="b" * 40
            ), mock.patch.object(
                self.mod, "require_private_remote"
            ), mock.patch("sys.stdout"):
                self.mod.cmd_retire_bootstrap(args)
            audit_records = [
                json.loads(line)
                for line in (root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        repository_ids.assert_not_called()
        self.assertFalse((root / "github-app.env").exists())
        self.assertFalse((root / "github-app.pem").exists())
        self.assertEqual(audit_records[-2]["event"], "instance.bootstrap.retired")
        self.assertEqual(
            audit_records[-2]["github_installation_result"],
            "installation-uninstalled",
        )
        self.assertEqual(
            audit_records[-1]["github_installation_result"],
            "installation-uninstalled",
        )

    def test_retire_bootstrap_refuses_present_repository_access(self):
        class App:
            def __init__(self, _root):
                pass

            def installation_present(self):
                return True

            def installation_token(self, _permissions):
                return "installation-token"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("github-app.env", "github-app.pem"):
                (root / name).write_text("temporary credential\n", encoding="utf-8")
            public = root / "github-readonly.pub"
            public.write_text("ssh-ed25519 public\n", encoding="utf-8")
            fingerprint = "SHA256:abcdefghijklmnopqrstuvwxyz1234567890"
            args = mock.Mock(
                config_root=root,
                key_fingerprint=fingerprint,
                signer_id="human-private-instance",
                audit_log=root / "audit.jsonl",
            )
            with mock.patch.object(self.mod, "require_root"), mock.patch.object(
                self.mod, "require_active_controller"
            ), mock.patch.object(
                self.mod,
                "require_approval",
                return_value=self.approval_intent("retire-bootstrap"),
            ), mock.patch.object(
                self.mod,
                "load_source_config",
                return_value=({"public": public}, {"repository_sha256": "a" * 64}),
            ), mock.patch.object(
                self.mod, "load_repository_state", return_value={"repository_id": 123}
            ), mock.patch.object(
                self.mod, "fingerprint", return_value=fingerprint
            ), mock.patch.object(
                self.mod, "GithubApp", App
            ), mock.patch.object(
                self.mod, "app_repository_ids", return_value={123}
            ), self.assertRaisesRegex(
                self.mod.InstanceAuthorityError,
                "remove the private repository",
            ):
                self.mod.cmd_retire_bootstrap(args)

            self.assertTrue((root / "github-app.env").exists())
            self.assertTrue((root / "github-app.pem").exists())

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

    def test_approved_action_failure_audit_is_correlated_and_redacted(self):
        with tempfile.TemporaryDirectory() as temporary:
            audit_log = Path(temporary) / "audit.jsonl"
            args = mock.Mock(signer_id="human-private-instance", audit_log=audit_log)
            intent = self.approval_intent("register-read-key")
            self.mod.begin_approved_action(args, intent)
            self.mod.set_approved_action_phase(
                args, "create-deploy-key", github_status=422,
            )
            error = self.mod.InstanceAuthorityError("raw response must-not-appear")
            self.mod.audit_approved_action_failure(args, error)
            records = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([item["event"] for item in records], [
            "instance.action.started", "instance.action.finished",
        ])
        self.assertEqual(records[-1]["outcome"], "failure")
        self.assertEqual(records[-1]["phase"], "create-deploy-key")
        self.assertEqual(records[-1]["github_status"], 422)
        self.assertEqual(records[0]["intent_sha256"], records[-1]["intent_sha256"])
        self.assertNotIn("must-not-appear", json.dumps(records))

    def test_private_instance_repository_name_is_exact(self):
        approved_engine = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
        result = self.run_script(
            "intent", "instance", "register-repository",
            "--repo-owner", "family", "--repo-name", "klokast",
            "--engine-commit", approved_engine,
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
                self.mod, "require_approval", return_value=self.approval_intent("register-repository")
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
            return {
                "id": 456,
                "title": "active controller private instance read-only",
                "key": "ssh-ed25519 public",
                "read_only": True,
            }

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
            (source_root / "github-readonly.pub").write_text(
                "ssh-ed25519 public klokast private instance read-only\n",
                encoding="utf-8",
            )
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
                self.mod, "require_approval", return_value=self.approval_intent("register-read-key")
            ), mock.patch.object(
                self.mod, "fingerprint", return_value=args.key_fingerprint
            ), mock.patch.object(self.mod, "GithubApp", App), mock.patch.object(
                self.mod, "github_request", side_effect=request
            ), mock.patch.object(
                self.mod, "require_empty_authenticated_remote"
            ), mock.patch("sys.stdout"):
                self.mod.cmd_register_read_key(args)
            audit_records = [
                json.loads(line)
                for line in (root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(calls[-1][0], "POST")
        self.assertEqual(calls[-1][2]["read_only"], True)
        self.assertEqual(calls[-1][2]["key"], "ssh-ed25519 public")
        self.assertEqual([item["event"] for item in audit_records], [
            "instance.action.started",
            "instance.deploy-key.registered",
            "instance.action.finished",
        ])
        self.assertEqual(audit_records[1]["result"], "created")
        self.assertEqual(audit_records[-1]["outcome"], "success")
        self.assertNotIn("ssh-ed25519 public", json.dumps(audit_records))

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
                self.mod, "require_approval", return_value=self.approval_intent("register-read-key")
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
                return {
                    "id": 456,
                    "title": "active controller private instance read-only",
                    "key": "ssh-ed25519 public",
                    "read_only": True,
                }
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
                self.mod, "require_approval", return_value=self.approval_intent("register-read-key")
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
            audit_records = [
                json.loads(line)
                for line in (root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(calls[-1][0:2], (
            "DELETE", "/repos/family/klokast-instance/keys/456"
        ))
        self.assertEqual(audit_records[-1]["event"], "instance.deploy-key.cleanup.finished")
        self.assertEqual(audit_records[-1]["outcome"], "success")
        self.assertEqual(audit_records[-1]["reason"], "repository-validation-failed")

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
        self.assertIn("- path: /var/lib/klokast/engine-promotions", tasks)
        self.assertIn("- path: /var/lib/klokast/engine-activations", tasks)
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

    def test_engine_promotion_helper_preserves_private_git_authority(self):
        completed = subprocess.run(
            [str(PROMOTION_HELPER), "--help"], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("trusted\nMacBook", completed.stdout)
        self.assertIn("edit, stage, commit, or push", completed.stdout)
        source = PROMOTION_HELPER.read_text(encoding="utf-8")
        self.assertIn("human-private-instance", source)
        self.assertIn("promotion-preflight", source)
        self.assertIn("promotion-approve", source)
        self.assertIn("promotion-activate", source)
        self.assertIn("--rollback does not accept an arbitrary engine commit", source)
        self.assertIn("git -C \"$PRIVATE_WORKTREE\" push origin main", source)
        self.assertNotIn("prepare-private-instance-bootstrap", source)
        self.assertNotIn("09afa3e1e677da25ffdbdb1f22378e9f97a71e71", source)

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
        self.assertIn('"schema_version": 2', source)
        self.assertIn('"engine_commit": engine_commit', source)
        self.assertIn('"repo_head": controller_commit', source)
        self.assertIn("intent is not canonical JSON", source)
        self.assertIn("private-instance.wrapper.finished", source)
        self.assertIn("intent_sha256", source)
        self.assertIn("Private-instance action failed.", source)
        self.assertIn("Failed phase:", source)
        self.assertIn("secret-authority.jsonl", source)
        self.assertIn("ansible/bin/platform-instance verify-engine", source)
        self.assertIn('--engine-commit "$expected"', source)
        self.assertNotIn("test \"$(git rev-parse HEAD)\" = \"$expected\"", source)
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

    def test_verify_engine_separates_current_checkout_from_sealed_engine(self):
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

        args = mock.Mock(engine_commit="a" * 40, build_dir="/verified/build")
        with mock.patch.object(self.mod, "require_controller_user"), mock.patch.object(
            self.mod, "require_reviewed_engine", return_value="b" * 40
        ), mock.patch.object(
            self.mod, "load_plan_wrapper", return_value=Plan
        ), mock.patch("builtins.print") as printer:
            self.mod.verify_engine(args)
        result = json.loads(printer.call_args.args[0])
        self.assertEqual(result["engine_commit"], "a" * 40)
        self.assertEqual(result["controller_commit"], "b" * 40)
        self.assertTrue(result["verified"])

    def test_reviewed_engine_requires_a_clean_ancestor(self):
        current = mock.Mock(returncode=0, stdout="b" * 40 + "\n", stderr="")
        clean = mock.Mock(returncode=0, stdout="", stderr="")
        success = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            self.mod, "run", side_effect=[current, clean, success, success]
        ) as runner:
            result = self.mod.require_reviewed_engine("a" * 40)
        self.assertEqual(result, "b" * 40)
        self.assertEqual(
            runner.call_args_list[-1].args[0][-2:],
            ["a" * 40, "b" * 40],
        )

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
            os.chmod(values, 0o600)
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
                self.mod, "VALUES_PATH", values
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
