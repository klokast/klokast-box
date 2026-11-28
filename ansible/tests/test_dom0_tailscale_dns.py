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
    def test_dom0_uses_wan_dhcp_dns(self):
        text = DOM0_TAILSCALE_TASKS.read_text(encoding="utf-8")
        resolver_text = DOM0_TAILSCALE_RESOLVER_TASKS.read_text(encoding="utf-8")

        self.assertIn("Collect Tailscale preferences", text)
        self.assertIn("tailscale debug prefs", text)
        self.assertIn("Keep Tailscale updates under controller image policy", text)
        self.assertIn("--auto-update=false", text)
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
        self.assertIn("^[AUD] var/lib/tailscale(/|$)", text)
        self.assertIn("- etc/resolv.conf", text)
        self.assertIn("dom0-tailscale-lbu-exclusions", text)
        self.assertIn("Converge the WAN resolver before Tailscale", text)
        self.assertIn("tailscale debug prefs", resolver_text)
        self.assertIn("tailscale set --accept-dns=false", resolver_text)
        self.assertNotIn("--accept-dns=true", text + resolver_text)
        self.assertNotIn("/usr/sbin/resolvconf", text + resolver_text)
        # The dom0 update selection owns the one-time udhcpc cleanup.
        self.assertNotIn("udhcpc.conf\n", resolver_text)
        self.assertNotIn("lineinfile", resolver_text)
        self.assertIn("Request a DHCP renewal to write the WAN resolvers", resolver_text)
        self.assertIn("kill -USR1", resolver_text)
        self.assertIn("dom0_wan_resolver_was_missing", resolver_text)
        self.assertIn("tailscale_restart_required", text)
        self.assertIn("tailscale_dns_restart_required", text)
        self.assertIn("dom0_apk_release_reconcile.changed", text)
        self.assertIn("signature mismatch", text)
        self.assertIn("retries: 10", text)

        playbook = (
            REPO_ROOT / "ansible" / "playbooks" / "22-dom0-base-verify.yml"
        ).read_text(encoding="utf-8")
        self.assertLess(
            playbook.index("Repair the dom0 WAN resolver before APK network access"),
            playbook.index("Converge the exact steady-state dom0 package policy"),
        )
        self.assertLess(
            playbook.index("Converge Tailscale before APK network access"),
            playbook.index("Converge the exact steady-state dom0 package policy"),
        )

    def test_dom0_health_requires_wan_dhcp_dns_only(self):
        health = (
            REPO_ROOT
            / "ansible"
            / "roles"
            / "dom0-health-verification"
            / "tasks"
            / "main.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("/etc/udhcpc/udhcpc.conf", health)
        self.assertIn("udhcpc.conf sets RESOLV_CONF", health)
        self.assertIn("/etc/resolv.conf has no WAN DHCP DNS server", health)
        self.assertIn("Tailscale manages dom0 DNS", health)
        self.assertNotIn("/usr/sbin/resolvconf", health)
        self.assertIn('"-etc/resolv.conf" in lbu_paths', health)
        self.assertIn("obsolete udhcpc openresolv hook remains", health)
        self.assertIn("the next dom0 update returns DNS to the WAN DHCP resolvers", health)
        self.assertIn("can't reach (?:the )?configured DNS servers", health)
        self.assertIn("signature mismatch", health)
        self.assertIn("Tailscale self-update can bypass the controller APK policy", health)
        self.assertIn('"tailscale_auto_update_apply"', health)

    def test_dom0_overview_mentions_wan_dns(self):
        text = DOM0_PLAYBOOK_OVERVIEW.read_text(encoding="utf-8")

        self.assertIn("does not accept Tailnet DNS", text)
        self.assertIn("disable Tailscale self-update", text)


if __name__ == "__main__":
    unittest.main()
