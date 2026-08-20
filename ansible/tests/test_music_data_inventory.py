#!/usr/bin/env python3
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = (
    REPO_ROOT
    / "apps"
    / "music"
    / "ansible"
    / "roles"
    / "music-backend-removal"
    / "files"
    / "music-data-inventory.py"
)


def load_inventory_module():
    spec = importlib.util.spec_from_file_location("music_data_inventory", INVENTORY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MusicDataInventoryTest(unittest.TestCase):
    def setUp(self):
        self.module = load_inventory_module()

    @mock.patch("subprocess.run")
    def test_exact_absence_result_is_accepted(self, run):
        run.return_value = subprocess.CompletedProcess([], 1)
        self.assertIsNone(self.module.volume_mountpoint("neo", "klokast-music-library"))
        self.assertEqual(run.call_count, 1)

    @mock.patch("subprocess.run")
    def test_podman_error_is_not_treated_as_absence(self, run):
        run.return_value = subprocess.CompletedProcess([], 125)
        with self.assertRaisesRegex(SystemExit, "could not determine"):
            self.module.volume_mountpoint("neo", "klokast-music-library")

    @mock.patch("subprocess.run")
    def test_inspection_error_is_not_treated_as_absence(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 125, stdout=""),
        ]
        with self.assertRaisesRegex(SystemExit, "could not inspect"):
            self.module.volume_mountpoint("neo", "klokast-music-library")

    def test_invalid_identity_is_rejected_before_podman(self):
        with self.assertRaisesRegex(SystemExit, "invalid Podman identity"):
            self.module.volume_mountpoint("../root", "klokast-music-library")


if __name__ == "__main__":
    unittest.main()
