#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "ansible" / "roles" / "router"


class RouterRoleTest(unittest.TestCase):
    def test_sysctl_service_is_enabled_in_boot_runlevel(self):
        tasks = (ROLE / "tasks" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("net.ipv4.ip_forward", tasks)
        self.assertIn("/sbin/rc-update", tasks)
        self.assertIn("- sysctl", tasks)
        self.assertIn("- boot", tasks)
        self.assertIn("name: sysctl", tasks)


if __name__ == "__main__":
    unittest.main()
