#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "ansible" / "roles" / "podman-host"


class PodmanHostRoleTest(unittest.TestCase):
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
        self.assertIn('"${runroot}/containers"', template)
        self.assertIn('"${runroot}/libpod/tmp"', template)
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
