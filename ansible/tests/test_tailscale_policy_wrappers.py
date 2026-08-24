#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PULL = (
    REPO_ROOT / "klokast-ops" / "tailscale" / "bin" / "ts-policy-pull"
).read_text()
POLICY_MUTATION = (
    REPO_ROOT / "klokast-ops" / "tailscale" / "bin" / "ts-policy-mutate-internal"
).read_text()


class TailscalePolicyWrappersTest(unittest.TestCase):
    def test_policy_mutation_does_not_print_private_policy_response(self):
        self.assertIn('-o "$work_dir/post-response.body"', POLICY_MUTATION)
        self.assertNotIn("cat \"$work_dir/post-response.body\"", POLICY_MUTATION)

    def test_policy_pull_preserves_child_options_with_busybox_su(self):
        self.assertIn(
            "su \"$WRITE_USER\" -s /bin/sh -c 'exec \"$@\"' sh -- \"$@\"",
            POLICY_PULL,
        )


if __name__ == "__main__":
    unittest.main()
