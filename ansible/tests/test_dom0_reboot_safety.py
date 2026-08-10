#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOM0_HEALTH_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "dom0-health-verification" / "tasks" / "main.yml"
)
XEN_GUEST_TEMPLATE = (
    REPO_ROOT / "ansible" / "roles" / "xen-guest" / "templates" / "xen-guest.cfg.j2"
)
DOM0_PLAYBOOK_OVERVIEW = (
    REPO_ROOT / "ansible" / "overview-playbooks" / "playbooks-2x-dom0.md"
)


class Dom0RebootSafetyTest(unittest.TestCase):
    def test_dom0_health_fails_dirty_lbu_status(self):
        text = DOM0_HEALTH_TASKS.read_text(encoding="utf-8")

        self.assertIn('lbu_status = capture(["lbu", "status"])', text)
        self.assertIn('lbu_status["stdout"].strip() == ""', text)
        self.assertIn("lbu status is not clean", text)

    def test_dom0_health_fails_tailscale_dns_fight(self):
        text = DOM0_HEALTH_TASKS.read_text(encoding="utf-8")

        self.assertIn("tailscale_dns_health", text)
        self.assertIn("resolv\\.conf overwritten|dns-fight", text)
        self.assertIn("Tailscale DNS health entries", text)

    def test_dom0_health_requires_tailscale_openrc_boot_enablement(self):
        text = DOM0_HEALTH_TASKS.read_text(encoding="utf-8")

        self.assertIn('Path("/etc/runlevels/default/tailscale")', text)
        self.assertIn('os.path.realpath(tailscale_openrc_path)', text)
        self.assertIn("Tailscale is not enabled in the OpenRC default runlevel", text)

    def test_xen_guest_configs_stay_pvh_without_device_model(self):
        text = XEN_GUEST_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn('type = "pvh"', text)
        self.assertNotIn("device_model", text)
        self.assertNotIn("device_model_version", text)
        self.assertNotIn("builder", text)

    def test_dom0_docs_explain_expected_xen_qemu_warning(self):
        text = DOM0_PLAYBOOK_OVERVIEW.read_text(encoding="utf-8")

        self.assertIn("qemu-xen is unavailable", text)
        self.assertIn("type = \"pvh\"", text)
        self.assertIn("no QEMU packages, binaries, or", text)
        self.assertIn("Do not add `device_model_version`", text)


if __name__ == "__main__":
    unittest.main()
