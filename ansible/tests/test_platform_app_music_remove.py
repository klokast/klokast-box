#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_APP = REPO_ROOT / "ansible" / "bin" / "platform-app"


class PlatformAppMusicRemoveTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.registry = Path(self.temporary.name) / "platform-resources.yml"
        self.registry.write_text(
            yaml.safe_dump({
                "schema_version": 1,
                "boxes": {},
                "apps": {
                    "music": {
                        "enabled": True,
                        "runtime_state": "running",
                        "placement": {"boxes": ["k002"]},
                        "resources": {},
                    }
                },
            }, sort_keys=False),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, *arguments):
        environment = os.environ.copy()
        environment["KLOKAST_CONTROLLER_GUARD"] = str(Path(self.temporary.name) / "absent-guard")
        environment["KLOKAST_APP_LIFECYCLE_AUDIT"] = str(Path(self.temporary.name) / "audit.jsonl")
        return subprocess.run(
            [str(PLATFORM_APP), "--registry", str(self.registry), *arguments],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_dry_run_prints_the_hashed_preservation_plan_without_mutation(self):
        before = self.registry.read_bytes()
        result = self.run_cli("remove", "music", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"data_action": "preserve"', result.stdout)
        self.assertIn('"plan_sha256":', result.stdout)
        self.assertIn("klokast-music-library", result.stdout)
        self.assertIn('"hostname": "k002-audio"', result.stdout)
        self.assertIn('"hostname": "k002-streamer"', result.stdout)
        self.assertIn("dry-run would disable apps.music", result.stdout)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_remove_requires_yes_before_external_work(self):
        result = self.run_cli("remove", "music")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --yes", result.stderr)

    def test_remove_rejects_an_empty_placement(self):
        value = yaml.safe_load(self.registry.read_text(encoding="utf-8"))
        value["apps"]["music"]["placement"]["boxes"] = []
        self.registry.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
        result = self.run_cli("remove", "music", "--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain at least one box", result.stderr)

    def test_destroy_keeps_the_existing_double_confirmation(self):
        missing_wipe = self.run_cli("destroy", "music", "--yes")
        self.assertNotEqual(missing_wipe.returncode, 0)
        self.assertIn("destroy requires --wipe-data", missing_wipe.stderr)
        missing_yes = self.run_cli("destroy", "music", "--wipe-data")
        self.assertNotEqual(missing_yes.returncode, 0)
        self.assertIn("destroy requires --yes", missing_yes.stderr)


if __name__ == "__main__":
    unittest.main()
