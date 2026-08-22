#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLOUD_OPS = REPO_ROOT / "klokast-ops"


class CloudOpsContractTest(unittest.TestCase):
    def test_ansible_defaults_use_exact_hetzner_runtime_identity(self):
        defaults = (
            CLOUD_OPS / "ansible" / "inventory" / "group_vars" / "all.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("ops_hostname: hetzner-ops", defaults)
        self.assertIn("  - tag:infra", defaults)
        self.assertIn("ops_tailscale_require_exact_hostname: true", defaults)

    def test_cloud_playbooks_reject_hostname_overrides(self):
        hetzner = (
            CLOUD_OPS / "ansible" / "playbooks" / "00-ops-server.yml"
        ).read_text(encoding="utf-8")
        vultr = (
            CLOUD_OPS / "ansible" / "playbooks" / "01-vultr-ops-infra-agent.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('ops_hostname == "hetzner-ops"', hetzner)
        self.assertIn('ops_hostname == "vultr-ops"', vultr)

    def test_cloud_ansible_temp_directory_is_user_scoped(self):
        config = (CLOUD_OPS / "ansible" / "ansible.cfg").read_text(encoding="utf-8")
        self.assertIn("local_tmp = ~/.ansible/tmp", config)
        self.assertNotIn("local_tmp = /tmp/", config)


if __name__ == "__main__":
    unittest.main()
