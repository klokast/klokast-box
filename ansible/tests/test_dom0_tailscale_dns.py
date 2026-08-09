#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOM0_TAILSCALE_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "dom0-tailscale" / "tasks" / "main.yml"
)
DOM0_PLAYBOOK_OVERVIEW = (
    REPO_ROOT / "ansible" / "overview-playbooks" / "playbooks-2x-dom0.md"
)


class Dom0TailscaleDnsTest(unittest.TestCase):
    def test_dom0_tailscale_repairs_tailnet_dns(self):
        text = DOM0_TAILSCALE_TASKS.read_text(encoding="utf-8")

        self.assertIn("Collect Tailscale preferences", text)
        self.assertIn("tailscale debug prefs", text)
        self.assertIn("tailscale_dns_is_disabled", text)
        self.assertIn("tailscale_dns_is_overwritten", text)
        self.assertIn("resolv\\\\.conf overwritten|dns-fight", text)
        self.assertIn("tailscale set --accept-dns=false", text)
        self.assertIn("tailscale set --accept-dns=true", text)
        self.assertIn("wait_for_connection", text)
        self.assertIn("rc-service tailscale restart", text)
        self.assertIn("async: 120", text)
        self.assertIn("poll: 0", text)
        self.assertIn("ansible_async_dir: /tmp/klokast-ansible-async", text)
        self.assertNotIn("( sleep 1; rc-service tailscale restart )", text)
        self.assertIn("Commit lbu changes", text)
        self.assertIn("Detect stale Tailscale daemon after package upgrade", text)
        self.assertIn("tailscale_daemon_version_mismatch", text)
        self.assertIn("regex_replace('-.*$', '')", text)
        self.assertIn("Launch a detached Tailscale restart when daemon version lags the CLI", text)
        self.assertIn("Wait for dom0 after stale Tailscale daemon restart", text)
        self.assertIn("Verify the Tailscale OpenRC service after daemon restart", text)
        self.assertIn("Recollect Tailscale runtime status after daemon restart", text)
        self.assertIn("Verify the restarted Tailscale daemon", text)
        self.assertIn("tailscale_status_parsed.BackendState", text)
        self.assertIn("Collect lbu status after Tailscale convergence", text)
        self.assertIn("dom0_tailscale_lbu_dirty", text)
        self.assertIn("Persist Tailscale runtime state changes", text)
        self.assertIn("^[AUD] (var/lib/tailscale|etc/resolv\\\\.conf)(/|$)", text)
        self.assertIn("Prevent udhcpc from overwriting Tailscale-owned DNS", text)
        self.assertIn("/etc/udhcpc/udhcpc.conf", text)
        self.assertIn('RESOLV_CONF="no"', text)

    def test_dom0_health_requires_exclusive_tailscale_dns_ownership(self):
        health = (
            REPO_ROOT
            / "ansible"
            / "roles"
            / "dom0-health-verification"
            / "tasks"
            / "main.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("/etc/udhcpc/udhcpc.conf", health)
        self.assertIn('udhcpc is not configured with RESOLV_CONF="no"', health)

    def test_dom0_overview_mentions_dns_repair(self):
        text = DOM0_PLAYBOOK_OVERVIEW.read_text(encoding="utf-8")

        self.assertIn("repairs overwritten `/etc/resolv.conf`", text)


if __name__ == "__main__":
    unittest.main()
