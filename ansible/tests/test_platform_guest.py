#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
import os
from registry_fixture import legacy_client


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ansible" / "bin" / "platform-guest"


def load_module():
    loader = SourceFileLoader("platform_guest", str(SCRIPT))
    spec = importlib.util.spec_from_loader("platform_guest", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PlatformGuestTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = Path(self.tmp.name) / "platform-resources.yml"
        self.registry.write_text("schema_version: 1\napps: {}\n", encoding="utf-8")
        self.registry.chmod(0o600)
        environment = patch.dict(os.environ, {"PATH": legacy_client(self.tmp.name)})
        environment.start()
        self.addCleanup(environment.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *, role="iot", dry_run=False):
        return SimpleNamespace(
            registry=self.registry,
            box="boxa",
            role=role,
            dry_run=dry_run,
        )



    def test_apply_uses_narrow_compiled_shared_guest_command(self):
        calls = []

        def record(argv, **kwargs):
            calls.append([str(item) for item in argv])

        with patch.object(self.mod, "require_active_controller"), patch.object(
            self.mod, "approved_commit", return_value="abc123"
        ), patch.object(
            self.mod, "magicdns_suffix", return_value="example.ts.net"
        ), patch.object(self.mod, "run", side_effect=record):
            self.mod.converge(self.args(role="iot"), "apply")

        command = calls[0]
        self.assertIn("--box", command)
        self.assertIn("boxa", command)
        self.assertIn("--shared-guest-role", command)
        self.assertIn("iot", command)
        self.assertIn("--approved-commit", command)
        self.assertEqual(command[-1], "apply-shared-guests")


if __name__ == "__main__":
    unittest.main()
