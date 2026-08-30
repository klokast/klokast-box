#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
BROKER = REPO_ROOT / "klokast-ops/secret-authority/bin/freebox-ipv6-broker"
INSTALLER = REPO_ROOT / "klokast-ops/secret-authority/bin/ksa-freebox-credential"


def load(path, name):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FreeboxCredentialInstallerTest(unittest.TestCase):
    def setUp(self):
        self.broker = load(BROKER, "freebox_broker_installer_test")
        self.installer = load(INSTALLER, "freebox_credential_installer_test")
        self.discovery = {
            "uid": "gateway_uid_123456",
            "device_name": "Freebox Server",
            "api_version": "15.0",
            "api_base_url": "/api/",
            "device_type": "FreeboxServer9",
            "api_domain": "example.fbxos.fr",
            "https_available": True,
            "https_port": 443,
        }

    def credential(self):
        return {
            "schema_version": 1,
            "kind": "klokast.freebox-credential.v1",
            "app_id": self.installer.APP_ID,
            "app_token": "TOP-SECRET-APP-TOKEN",
            "gateway_uid": self.discovery["uid"],
            "api_version": "15.0",
            "api_major": 15,
            "local_endpoint": f"http://{self.broker.GATEWAY_HOST}",
        }

    def common_patches(self, root):
        credential = root / "freebox-ipv6.json"
        pending = root / "freebox-ipv6.pending.json"
        return credential, pending, (
            patch.object(self.installer, "load_broker", return_value=self.broker),
            patch.object(self.broker, "CREDENTIAL", credential),
            patch.object(self.broker, "PENDING_CREDENTIAL", pending),
            patch.object(self.broker, "require_root_active"),
            patch.object(self.broker, "Transport", return_value=object()),
            patch.object(self.broker, "discover", return_value=self.discovery),
            patch.object(self.broker, "ensure_root_dir"),
        )

    def test_granted_token_is_recoverable_when_session_check_fails(self):
        class Transport:
            def request(_self, method, path, body=None, headers=None):
                if path.endswith("/login/authorize/"):
                    return {"success": True, "result": {
                        "app_token": "TOP-SECRET-APP-TOKEN", "track_id": 7,
                    }}
                return {"success": True, "result": {
                    "status": "granted", "challenge": "challenge",
                }}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            credential, pending, patches = self.common_patches(root)
            with patches[0], patches[1], patches[2], patches[3], patch.object(
                self.broker, "Transport", return_value=Transport()
            ), patches[5], patches[6] as ensure_root_dir, patch.object(
                self.broker, "Session", side_effect=self.broker.BrokerError("session refused")
            ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.installer.main(["install"]), 1)
            ensure_root_dir.assert_called_once_with(root, mode=0o755)
            self.assertFalse(credential.exists())
            self.assertEqual(pending.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(pending.read_text(encoding="utf-8")), self.credential())

    def test_pending_token_resumes_without_new_authorization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            credential, pending, patches = self.common_patches(root)
            pending.write_text(self.broker.canonical(self.credential()) + "\n", encoding="utf-8")
            pending.chmod(0o600)

            class Session:
                def __init__(_self, _transport, supplied):
                    self.assertEqual(supplied, self.credential())

                def close(_self):
                    return None

            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patch.object(
                self.broker, "Session", Session
            ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.installer.main(["install"]), 0)
            self.assertTrue(credential.exists())
            self.assertFalse(pending.exists())

    def test_pending_token_rejects_application_identity_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            credential, pending, patches = self.common_patches(root)
            value = self.credential()
            value["app_id"] = "org.example.wrong"
            pending.write_text(self.broker.canonical(value) + "\n", encoding="utf-8")
            pending.chmod(0o600)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.installer.main(["install"]), 1)
            self.assertFalse(credential.exists())
            self.assertTrue(pending.exists())


if __name__ == "__main__":
    unittest.main()
