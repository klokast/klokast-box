#!/usr/bin/env python3
"""Regression checks for adopted registry consumers with no legacy file."""

import importlib.util
import json
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL = Path("/home/smith/private/klokast/platform-resources.yml")


def load(name, relative):
    loader = SourceFileLoader(name, str(REPO_ROOT / relative))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RegistryConsumerClosureTest(unittest.TestCase):
    def test_core_consumers_default_to_the_canonical_source_selector(self):
        resources = load("closure_resources", "ansible/bin/platform-resources")
        with patch.object(resources.sys, "argv", ["platform-resources", "show"]):
            self.assertEqual(Path(resources.parse_args().registry), CANONICAL)

        mapper = load("closure_mapper", "ansible/bin/platform-map")
        args = mapper.build_parser().parse_args(["refresh"])
        self.assertEqual(args.resources_registry, CANONICAL)

    def test_python_app_controllers_do_not_probe_the_legacy_file(self):
        fixtures = {
            "household-vpn": {
                "apps": {"household-vpn": {"enabled": True, "boxes": ["boxa"], "resources": ["vpn-egress"]}},
                "app_vm_specs": [{"app": "household-vpn", "node": "boxa", "node_domain_role": "dmz", "guest_spec": {"guest_os": "alpine"}, "vm_ipv4_address": "192.168.200.40", "advertised_tags": ["tag:household-vpn"]}],
            },
            "local-ingress": {
                "apps": {"local-ingress": {"enabled": True, "boxes": ["boxa"], "resources": ["household-https", "music-upstream"]}},
            },
            "music": {
                "apps": {"music": {"enabled": True, "boxes": ["boxa"], "resources": ["snapcast-stream", "audio-endpoint-updates"]}},
                "managed_iot_devices": [{"app": "music", "resource": "local-audio-endpoint", "node": "boxa"}],
                "tailnet_resources": [{"app": "music", "id": "upload-ingress", "node": "boxa", "tag": "tag:music-upload"}],
            },
            "print-server": {
                "apps": {"print-server": {"enabled": True, "boxes": ["boxa"], "resources": ["printer-ipp"]}},
                "managed_iot_devices": [{"app": "print-server", "resource": "printer", "node": "boxa"}],
                "tailnet_resources": [{"app": "print-server", "id": "print-ingress", "node": "boxa", "tag": "tag:print"}],
            },
            "torrent": {
                "apps": {"torrent": {"enabled": True, "boxes": ["boxa"]}},
                "app_vm_specs": [{"app": "torrent", "node": "boxa", "node_domain_role": "dmz", "guest_spec": {"guest_os": "alpine"}, "advertised_tags": ["tag:torrent"]}],
            },
        }
        for app, fixture in fixtures.items():
            with self.subTest(app=app):
                module = load("closure_" + app.replace("-", "_"), f"apps/{app}/bin/{app}ctl")
                with patch.object(module, "output", return_value=json.dumps(fixture)):
                    module.load_resources(CANONICAL, "boxa")

    def test_shell_app_readers_have_no_legacy_registry_existence_gate(self):
        for relative in (
            "apps/nextcloud/bin/nextcloudctl",
            "apps/nextcloud-v2/bin/nextcloud-v2ctl",
            "apps/static-site/bin/static-sitectl",
            "apps/immich/bin/immichctl",
        ):
            with self.subTest(relative=relative):
                text = (REPO_ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn('[[ -f "$resources_registry" ]]', text)
                self.assertIn(str(CANONICAL), text)

    def test_platform_check_does_not_require_the_selector_to_exist(self):
        text = (REPO_ROOT / "ansible/bin/platform-check").read_text(encoding="utf-8")
        self.assertNotIn('if [ ! -f "$registry" ]', text)
        self.assertIn(str(CANONICAL), text)


if __name__ == "__main__":
    unittest.main()
