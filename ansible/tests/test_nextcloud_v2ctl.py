#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "apps" / "nextcloud-v2" / "bin" / "nextcloud-v2ctl"


class NextcloudV2CtlGrantTest(unittest.TestCase):
    def make_grant(self, tmpdir, **overrides):
        grant = {
            "schema_version": 1,
            "kind": "platform-resource-grant",
            "app": "nextcloud-v2",
            "enabled": True,
            "approved_commit": "commit-a",
            "compiler": "platform-resources",
            "compiler_version": 11,
            "registry_sha256": "0" * 64,
            "boxes": ["k001", "k002"],
            "placement": {"active_master": "k001", "passive_backup": "k002"},
            "resources": ["backend-http-upstream"],
            "tailnet_resources": [
                {
                    "app": "nextcloud-v2",
                    "id": "private-ingress",
                    "hostname": "next",
                    "tag": "tag:nextcloud",
                    "grants": [{"src": "group:family", "ports": [443]}],
                }
            ],
            "app_resource_effective_files": [],
        }
        grant.update(overrides)
        path = Path(tmpdir) / "grant.json"
        path.write_text(json.dumps(grant), encoding="utf-8")
        return path

    def run_check(self, grant_path, marker_path):
        env = os.environ.copy()
        env["KLOKAST_APPROVED_COMMIT_MARKER"] = str(marker_path)
        env.pop("KLOKAST_PLATFORM_RESOURCE_GRANT", None)
        return subprocess.run(
            [
                str(SCRIPT),
                "resource-grant-check",
                "--active-master",
                "k001",
                "--passive-backup",
                "k002",
                "--resource-grant",
                str(grant_path),
            ],
            check=False,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_accepts_matching_grant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "approved"
            marker.write_text("commit-a\n", encoding="utf-8")
            result = self.run_check(self.make_grant(tmpdir), marker)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("resource grant ok", result.stdout)

    def test_rejects_wrong_app(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "approved"
            marker.write_text("commit-a\n", encoding="utf-8")
            result = self.run_check(self.make_grant(tmpdir, app="nextcloud"), marker)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_private_registry_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "approved"
            marker.write_text("commit-a\n", encoding="utf-8")
            result = self.run_check(
                self.make_grant(tmpdir, registry_path="/private/platform-resources.yml"),
                marker,
            )
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_missing_resource(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "approved"
            marker.write_text("commit-a\n", encoding="utf-8")
            result = self.run_check(self.make_grant(tmpdir, resources=[]), marker)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
