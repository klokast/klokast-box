#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "apps" / "immich" / "bin" / "immichctl"
REMOVE_TASKS = REPO_ROOT / "apps" / "immich" / "ansible" / "roles" / "immich-remove" / "tasks" / "main.yml"


class ImmichctlGrantTest(unittest.TestCase):
    def test_avoids_process_substitution_for_alpine_target_shells(self):
        content = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("< <(", content)
        self.assertNotIn("/dev/fd", content)

    def make_grant(self, tmpdir, **overrides):
        grant = {
            "schema_version": 1,
            "kind": "platform-resource-grant",
            "app": "immich",
            "enabled": True,
            "approved_commit": "commit-a",
            "compiler": "platform-resources",
            "compiler_version": 11,
            "registry_sha256": "0" * 64,
            "boxes": ["boxa", "boxb"],
            "placement": {"active_master": "boxa", "passive_backup": "boxb"},
            "resources": ["backend-http-upstream"],
            "tailnet_resources": [
                {
                    "app": "immich",
                    "id": "private-ingress",
                    "hostname": "photos",
                    "tag": "tag:immich",
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
        env.pop("KLOKAST_PLATFORM_RESOURCES_REGISTRY", None)
        env.pop("KLOKAST_APP_RESOURCES_REGISTRY", None)
        env.pop("KLOKAST_PLATFORM_RESOURCE_GRANT", None)
        return subprocess.run(
            [
                str(SCRIPT),
                "resource-grant-check",
                "--active-master",
                "boxa",
                "--passive-backup",
                "boxb",
                "--resource-grant",
                str(grant_path),
            ],
            check=False,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def write_registry(self, tmpdir):
        path = Path(tmpdir) / "platform-resources.yml"
        path.write_text(
            """---
schema_version: 1
apps:
  immich:
    enabled: true
    placement:
      active_master: boxa
      passive_backup: boxb
    ingress_mode: tailscale
    resources:
      backend-http-upstream: true
""",
            encoding="utf-8",
        )
        return path

    def run_destroy(self, registry_path, *extra_args):
        env = os.environ.copy()
        env["HOME"] = str(registry_path.parent)
        env.pop("KLOKAST_PLATFORM_RESOURCES_REGISTRY", None)
        env.pop("KLOKAST_APP_RESOURCES_REGISTRY", None)
        env.pop("KLOKAST_PLATFORM_RESOURCE_GRANT", None)
        return subprocess.run(
            [
                str(SCRIPT),
                "destroy",
                "--active-master",
                "boxa",
                "--passive-backup",
                "boxb",
                "--resources-registry",
                str(registry_path),
                *extra_args,
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

    def test_rejects_wrong_boxes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "approved"
            marker.write_text("commit-a\n", encoding="utf-8")
            grant = self.make_grant(tmpdir, boxes=["boxa", "k003"])
            result = self.run_check(grant, marker)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid Immich platform resource grant", result.stderr)

    def test_rejects_disabled_grant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "approved"
            marker.write_text("commit-a\n", encoding="utf-8")
            result = self.run_check(self.make_grant(tmpdir, enabled=False), marker)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_stale_commit_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "approved"
            marker.write_text("commit-b\n", encoding="utf-8")
            result = self.run_check(self.make_grant(tmpdir), marker)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_missing_backend_resource(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "approved"
            marker.write_text("commit-a\n", encoding="utf-8")
            result = self.run_check(self.make_grant(tmpdir, resources=[]), marker)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_missing_tailnet_resource(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "approved"
            marker.write_text("commit-a\n", encoding="utf-8")
            result = self.run_check(self.make_grant(tmpdir, tailnet_resources=[]), marker)
        self.assertNotEqual(result.returncode, 0)

    def test_destroy_requires_explicit_wipe_and_yes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self.write_registry(tmpdir)
            missing_wipe = self.run_destroy(registry, "--yes", "--dry-run-plan")
            missing_yes = self.run_destroy(registry, "--wipe-data", "--dry-run-plan")

        self.assertNotEqual(missing_wipe.returncode, 0)
        self.assertIn("destroy requires --wipe-data", missing_wipe.stderr)
        self.assertNotEqual(missing_yes.returncode, 0)
        self.assertIn("destroy requires --yes", missing_yes.stderr)

    def test_destroy_dry_run_plans_without_mutating_registry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self.write_registry(tmpdir)
            before = registry.read_text(encoding="utf-8")
            result = self.run_destroy(registry, "--wipe-data", "--yes", "--dry-run-plan")
            after = registry.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(after, before)
        self.assertIn("dry-run destroy plan", result.stdout)
        self.assertIn("90-remove.yml", result.stdout)
        self.assertIn("apps.immich.enabled=false", result.stdout)
        self.assertIn("platform-resources --registry", result.stdout)
        self.assertIn("cleanup approved-state=", result.stdout)
        self.assertIn("cleanup controller-secret=", result.stdout)

    def test_destroy_rejects_registry_placement_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self.write_registry(tmpdir)
            content = registry.read_text(encoding="utf-8").replace("passive_backup: boxb", "passive_backup: k003")
            registry.write_text(content, encoding="utf-8")
            result = self.run_destroy(registry, "--wipe-data", "--yes", "--dry-run-plan")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid Immich platform resources registry state", result.stderr)

    def test_remove_role_wipes_restic_backup_state_when_requested(self):
        content = REMOVE_TASKS.read_text(encoding="utf-8")
        self.assertIn("immich_restic_sftp_repository_path", content)
        self.assertIn("immich_restic_sshd_config_path", content)
        self.assertIn("ansible.posix.authorized_key", content)
        self.assertIn("state: absent", content)


if __name__ == "__main__":
    unittest.main()
