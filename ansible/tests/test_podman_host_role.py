#!/usr/bin/env python3
import unittest
from pathlib import Path
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "ansible" / "roles" / "podman-host"


class PodmanHostRoleTest(unittest.TestCase):
    def test_convergence_does_not_download_or_run_a_test_image_by_default(self):
        defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text(encoding="utf-8"))
        tasks = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text(encoding="utf-8"))
        egress = yaml.safe_load((REPO_ROOT / "ansible/roles/vm-egress-verification/tasks/main.yml").read_text(encoding="utf-8"))
        self.assertIs(defaults["podman_host_container_probe"], False)
        optional = [task for task in tasks if task.get("name") == "Run the optional Podman container test with exact cleanup"]
        self.assertEqual(len(optional), 1)
        self.assertEqual(optional[0]["when"], "podman_host_container_probe | bool")
        self.assertTrue(any("podman pull" in str(task.get("ansible.builtin.command", "")) for task in optional[0]["block"]))
        self.assertTrue(any("test image" in task["name"] for task in optional[0]["always"]))
        self.assertFalse(any("podman" in str(task.get("ansible.builtin.command", "")) and
                             "run" in str(task.get("ansible.builtin.command", "")) for task in egress))

    def test_runroot_cleanup_service_is_enabled_in_boot_runlevel(self):
        tasks = (ROLE / "tasks" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("klokast-podman-runroot-cleanup", tasks)
        self.assertIn("/sbin/rc-update", tasks)
        self.assertIn("- boot", tasks)

    def test_runroot_cleanup_service_only_removes_transient_runroot(self):
        template = (
            ROLE / "templates" / "klokast-podman-runroot-cleanup.init.j2"
        ).read_text(encoding="utf-8")
        self.assertIn('runroot="/tmp/storage-run-${uid}"', template)
        self.assertIn('rm -rf -- "${runroot}"', template)
        self.assertNotIn("/srv", template)
        self.assertNotIn(".local/share/containers/storage", template)

    def test_runroot_cleanup_service_prepares_tun_device(self):
        template = (
            ROLE / "templates" / "klokast-podman-runroot-cleanup.init.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("/sbin/modprobe tun", template)
        self.assertIn("mknod /dev/net/tun c 10 200", template)
        self.assertIn("chgrp netdev /dev/net/tun", template)
        self.assertIn("chmod 0666 /dev/net/tun", template)


if __name__ == "__main__":
    unittest.main()
