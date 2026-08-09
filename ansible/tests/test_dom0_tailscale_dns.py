#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOM0_TAILSCALE_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "dom0-tailscale" / "tasks" / "main.yml"
)
DOM0_TAILSCALE_RESOLVER_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "dom0-tailscale" / "tasks" / "resolver.yml"
)
DOM0_PLAYBOOK_OVERVIEW = (
    REPO_ROOT / "ansible" / "overview-playbooks" / "playbooks-2x-dom0.md"
)


class Dom0TailscaleDnsTest(unittest.TestCase):
    def test_dom0_tailscale_repairs_tailnet_dns(self):
        text = DOM0_TAILSCALE_TASKS.read_text(encoding="utf-8")
        resolver_text = DOM0_TAILSCALE_RESOLVER_TASKS.read_text(encoding="utf-8")

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
        self.assertIn("Launch a detached Tailscale restart when required", text)
        self.assertIn("Wait for dom0 after detached Tailscale daemon restart", text)
        self.assertIn("Verify the Tailscale OpenRC service after daemon restart", text)
        self.assertIn("Recollect Tailscale runtime status after daemon restart", text)
        self.assertIn("Verify the restarted Tailscale daemon", text)
        self.assertIn("tailscale_status_parsed.BackendState", text)
        self.assertIn("Collect lbu status after Tailscale convergence", text)
        self.assertIn("dom0_tailscale_lbu_dirty", text)
        self.assertIn("Persist Tailscale runtime state changes", text)
        self.assertIn("^[AUD] (var/lib/tailscale|etc/resolv\\\\.conf)(/|$)", text)
        self.assertIn("Converge the WAN resolver source before Tailscale", text)
        self.assertIn("Prevent udhcpc from overwriting Tailscale-owned DNS", resolver_text)
        self.assertIn("/etc/udhcpc/udhcpc.conf", resolver_text)
        self.assertIn('RESOLV_CONF="no"', resolver_text)
        self.assertIn("Feed WAN DHCP resolvers to openresolv", resolver_text)
        self.assertIn("/etc/udhcpc/post-bound/20-klokast-resolvconf", resolver_text)
        self.assertIn("/etc/udhcpc/post-renew/20-klokast-resolvconf", resolver_text)
        self.assertIn("/etc/udhcpc/pre-deconfig/20-klokast-resolvconf", resolver_text)
        self.assertIn('/usr/sbin/resolvconf -a "udhcpc.$interface"', resolver_text)
        self.assertIn("Request a DHCP renewal to publish the WAN resolver source", resolver_text)
        self.assertIn("kill -USR1", resolver_text)
        self.assertIn("Refresh the openresolv-generated resolver configuration", resolver_text)
        self.assertIn("/usr/sbin/resolvconf -u", resolver_text)
        self.assertIn("dom0_openresolv_repair_was_required", resolver_text)
        self.assertIn("tailscale_restart_required", text)
        self.assertIn("tailscale_dns_restart_required", text)
        self.assertIn("dom0_apk_release_reconcile.changed", text)
        self.assertIn("Clear the completed resolver restart request", text)
        self.assertIn("signature mismatch", text)
        self.assertIn("retries: 10", text)

        playbook = (
            REPO_ROOT / "ansible" / "playbooks" / "22-dom0-base-verify.yml"
        ).read_text(encoding="utf-8")
        self.assertLess(
            playbook.index("Repair the dom0 resolver source before APK network access"),
            playbook.index("Converge the exact steady-state dom0 package policy"),
        )
        self.assertLess(
            playbook.index("Restore Tailscale DNS before APK network access"),
            playbook.index("Converge the exact steady-state dom0 package policy"),
        )

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
        self.assertIn("openresolv has no WAN DNS source", health)
        self.assertIn("udhcpc openresolv hook is missing or not executable", health)
        self.assertIn("can't reach (?:the )?configured DNS servers", health)
        self.assertIn("signature mismatch", health)

    def test_dom0_overview_mentions_dns_repair(self):
        text = DOM0_PLAYBOOK_OVERVIEW.read_text(encoding="utf-8")

        self.assertIn("repairs overwritten `/etc/resolv.conf`", text)


if __name__ == "__main__":
    unittest.main()
