#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ansible" / "bin" / "render-tailscale-policy"
LOADER = importlib.machinery.SourceFileLoader("render_tailscale_policy", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
MODULE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(MODULE)


class RenderTailscalePolicyTest(unittest.TestCase):
    def deployment(self, root):
        path = root / "deployment.yml"
        path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "tailnet": {
                        "magicdns_suffix": "example.ts.net",
                        "groups": {
                            "operators": ["admin@example.com"],
                            "family": ["family@example.com"],
                        },
                    },
                    "boxes": {"k001": {"site": "site-a"}},
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_renders_private_group_membership(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deployment = MODULE.load_deployment(self.deployment(root))
            rendered = MODULE.render(MODULE.DEFAULT_TEMPLATE, deployment)

        self.assertIn('"group:operators": ["admin@example.com"]', rendered)
        self.assertIn('"group:family": ["family@example.com"]', rendered)
        self.assertNotIn("{{", rendered)

    def test_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.deployment(root)
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data["credential"] = "forbidden"
            path.write_text(yaml.safe_dump(data), encoding="utf-8")

            with self.assertRaises(SystemExit):
                MODULE.load_deployment(path)


if __name__ == "__main__":
    unittest.main()
