#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import re
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

    def test_oob_web_access_is_operator_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deployment = MODULE.load_deployment(self.deployment(root))
            rendered = MODULE.render(MODULE.DEFAULT_TEMPLATE, deployment)

        operator_oob_grant = re.compile(
            r'"src": \["group:operators"\],\s*'
            r'"dst": \["tag:oob"\],\s*'
            r'"ip": \[\s*'
            r'"tcp:22",\s*'
            r'"tcp:80",\s*'
            r'"tcp:443",\s*'
            r'"udp:60000-61000",\s*'
            r'\]',
        )
        ops_oob_grant = re.compile(
            r'"src": \["tag:ops"\],\s*'
            r'"dst": \["tag:oob"\],\s*'
            r'"ip":\s*\["tcp:22"\]',
        )
        airunner_oob_grant = re.compile(
            r'"src": \["tag:airunner"\][^}]*'
            r'"dst": \["tag:oob"\]',
        )
        wildcard_oob_grant = re.compile(
            r'"dst": \["tag:oob"\][^}]*'
            r'"ip":\s*\["\*"\]',
        )
        airunner_oob_denies = re.compile(
            r'"src":\s+"tag:airunner",\s*'
            r'"accept":\s*\["tag:ops:22"\],\s*'
            r'"deny":\s*\['
            r'(?=[^]]*"tag:oob:22")'
            r'(?=[^]]*"tag:oob:80")'
            r'(?=[^]]*"tag:oob:443")',
        )

        self.assertRegex(rendered, operator_oob_grant)
        self.assertRegex(rendered, ops_oob_grant)
        self.assertNotRegex(rendered, airunner_oob_grant)
        self.assertNotRegex(rendered, wildcard_oob_grant)
        self.assertRegex(rendered, airunner_oob_denies)

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
