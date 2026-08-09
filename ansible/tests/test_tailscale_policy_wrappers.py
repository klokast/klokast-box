#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PULL = (
    REPO_ROOT / "klokast-ops" / "tailscale" / "bin" / "ts-policy-pull"
).read_text()
POLICY_APPLY = (
    REPO_ROOT / "klokast-ops" / "tailscale" / "bin" / "ts-policy-apply"
).read_text()


class TailscalePolicyWrappersTest(unittest.TestCase):
    def test_policy_apply_does_not_print_private_policy_response(self):
        apply_endpoint = (
            '"https://api.tailscale.com/api/v2/tailnet/$TAILNET_ID/acl" '
            + "\\"
            + "\n  >/dev/null"
        )
        self.assertIn(apply_endpoint, POLICY_APPLY)

    def test_policy_pull_preserves_child_options_with_busybox_su(self):
        self.assertIn(
            "su \"$WRITE_USER\" -s /bin/sh -c 'exec \"$@\"' sh -- \"$@\"",
            POLICY_PULL,
        )


if __name__ == "__main__":
    unittest.main()
