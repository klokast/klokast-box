#!/usr/bin/env python3
"""Exercise the temporary controller signer with OpenSSH's real verifier."""

import importlib.util
import os
import pathlib
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from unittest.mock import Mock, patch


ROOT = pathlib.Path(__file__).resolve().parents[2]
HELPER = ROOT / "ansible/bin/development-sign-intent"


class DevelopmentApprovalTest(unittest.TestCase):
    @unittest.skipUnless(subprocess.run(["sh", "-c", "command -v ssh-keygen"], capture_output=True).returncode == 0,
                         "OpenSSH is required")
    def test_development_signature_and_signed_mode_refusal(self):
        loader = SourceFileLoader("development_sign_intent_test", str(HELPER))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            key_dir = root / "development-approvals"
            key_dir.mkdir(mode=0o700)
            key = key_dir / "platform-apply"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
            mode = root / "human-approval-mode"
            mode.write_text("development\n")
            mode.chmod(0o644)
            intent = root / "intent.json"
            intent.write_text('{"action":"test"}\n')
            intent.chmod(0o600)
            signature = root / "intent.json.sig"
            original_run = subprocess.run

            def run(command, **kwargs):
                if command[0] == "/usr/local/sbin/klokast-controller-guard":
                    return Mock(returncode=0, stdout='{"active":true}')
                return original_run(command, **kwargs)

            original_regular = module.require_regular

            def regular(path, owner, max_mode):
                return original_regular(path, os.getuid() if path == mode else owner, max_mode)

            with patch.object(module, "MODE_FILE", mode), patch.object(module, "KEY_DIR", key_dir), patch.object(
                module.getpass, "getuser", return_value="smith"
            ), patch.object(module, "require_regular", side_effect=regular), patch.object(
                module.subprocess, "run", side_effect=run
            ):
                module.main(["--purpose", "platform-apply", "--intent", str(intent), "--signature", str(signature)])
                self.assertEqual(signature.stat().st_mode & 0o777, 0o600)
                public = key.with_suffix(".pub").read_text().split()
                allowed = root / "allowed-signers"
                allowed.write_text('human-platform-apply namespaces="klokast-platform-apply" '
                                   + public[0] + " " + public[1] + "\n")
                checked = original_run(
                    ["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", "human-platform-apply",
                     "-n", "klokast-platform-apply", "-s", str(signature)],
                    input=intent.read_bytes(), capture_output=True,
                )
                self.assertEqual(checked.returncode, 0, checked.stderr)
                mode.write_text("signed\n")
                with self.assertRaisesRegex(ValueError, "disabled"):
                    module.main(["--purpose", "platform-apply", "--intent", str(intent),
                                 "--signature", str(root / "second.sig")])


if __name__ == "__main__":
    unittest.main()
