#!/usr/bin/env python3
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "terraform.yml"


class TerraformCiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_covers_each_module_and_required_check(self):
        self.assertIn("klokast-ops/terraform/hetzner-ops", self.workflow)
        self.assertIn("klokast-ops/terraform/vultr-ops", self.workflow)
        self.assertIn("fmt -check -recursive", self.workflow)
        self.assertIn("init -backend=false -input=false -lockfile=readonly", self.workflow)
        self.assertIn('validate\n', self.workflow)
        self.assertIn('test\n', self.workflow)

    def test_workflow_is_credential_free_and_read_only(self):
        self.assertIn("permissions:\n  contents: read\n", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertNotIn("pull_request_target:", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertNotRegex(self.workflow, re.compile(r"terraform\s+(?:plan|apply|destroy)\b"))

    def test_external_actions_are_pinned_to_full_commits(self):
        actions = re.findall(r"^\s*uses:\s*([^\s#]+)", self.workflow, re.MULTILINE)
        self.assertEqual(len(actions), 2)
        for action in actions:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
