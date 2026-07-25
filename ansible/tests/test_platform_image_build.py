#!/usr/bin/env python3
import importlib.util
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ansible" / "bin" / "platform-image-build"
PRINT_CTL = REPO_ROOT / "apps" / "print-server" / "bin" / "print-serverctl"
MUSIC_CTL = REPO_ROOT / "apps" / "music" / "bin" / "musicctl"


def load_module(path, name):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PlatformImageBuildTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module(SCRIPT, "platform_image_build")

    def app_specs(self, app):
        app_dir = REPO_ROOT / "apps" / app
        _path, manifest, upstream, _built = self.mod.load_manifest(app_dir)
        return self.mod.selected_specs(app_dir, manifest, upstream, set())

    def test_music_and_print_manifests_resolve_pinned_base_images(self):
        for app in ("music", "print-server"):
            specs = self.app_specs(app)
            self.assertEqual(len(specs), 1)
            self.assertIn("ALPINE_IMAGE", specs[0]["build_args"])
            self.assertGreaterEqual(len(specs[0]["build_args"]["ALPINE_IMAGE"]), 2)
            self.assertRegex(
                specs[0]["build_args"]["ALPINE_IMAGE"][0],
                r"^docker\.io/library/alpine@sha256:[0-9a-f]{64}$",
            )
            self.assertRegex(
                specs[0]["build_args"]["ALPINE_IMAGE"][1],
                r"^docker\.1ms\.run/library/alpine@sha256:[0-9a-f]{64}$",
            )

    def test_rejects_unpinned_required_upstream_images(self):
        with self.assertRaises(SystemExit):
            self.mod.resolve_build_args(
                {"alpine": {"canonical": "docker.io/library/alpine", "digest": ""}},
                {"build_args": {"ALPINE_IMAGE": "alpine"}},
                "test-image",
            )

    def test_deploy_templates_do_not_build_on_target(self):
        templates = [
            REPO_ROOT
            / "apps"
            / "music"
            / "ansible"
            / "roles"
            / "music-backend"
            / "templates"
            / "music-backend-deploy.sh.j2",
            REPO_ROOT
            / "apps"
            / "print-server"
            / "ansible"
            / "roles"
            / "print-server"
            / "templates"
            / "print-server-deploy.sh.j2",
        ]
        for template in templates:
            text = template.read_text(encoding="utf-8")
            self.assertNotIn("podman_cmd build", text)
            self.assertIn("podman_cmd image exists", text)

    def test_builder_uses_chroot_isolation(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"--isolation=chroot"', text)

    def test_archive_upload_does_not_depend_on_remote_variables(self):
        text = SCRIPT.read_text(encoding="utf-8")
        upload_body = text.split("def upload_archive", 1)[1].split("def artifact_path", 1)[0]
        self.assertIn('"dd"', upload_body)
        self.assertIn("remote_script", upload_body)
        self.assertNotIn('"sh", "-c"', upload_body)


class AppControlImageBuildTest(unittest.TestCase):
    def test_print_server_install_uses_platform_image_build_commands(self):
        ctl = load_module(PRINT_CTL, "print_server_ctl")
        with patch.object(ctl, "run") as run:
            ctl.build_and_load_images("k002")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][-1], "build")
        self.assertEqual(commands[1][-1], "load")
        self.assertIn("platform-image-build", commands[0][0])
        self.assertIn("print-server", commands[0])

    def test_music_install_uses_platform_image_build_commands(self):
        ctl = load_module(MUSIC_CTL, "music_ctl")
        with patch.object(ctl, "run") as run:
            ctl.build_and_load_images("k001")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][-1], "build")
        self.assertEqual(commands[1][-1], "load")
        self.assertIn("platform-image-build", commands[0][0])
        self.assertIn("music", commands[0])


if __name__ == "__main__":
    unittest.main()
