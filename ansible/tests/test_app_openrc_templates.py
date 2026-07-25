#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class AppOpenRcTemplateTest(unittest.TestCase):
    def test_static_site_cleans_oci_runtime_before_container_restart(self):
        template = (
            REPO_ROOT
            / "apps/static-site/ansible/roles/static-site-runtime/templates/static-site-web.init.j2"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "podman_cmd container cleanup {{ static_site_web_container_name | quote }}",
            template,
        )
        self.assertLess(
            template.index("podman_cmd container cleanup"),
            template.index("podman_cmd container start"),
        )

    def test_nextcloud_private_ingress_retries_boot_converge(self):
        template = (
            REPO_ROOT
            / "apps/nextcloud/ansible/roles/nextcloud-private-ingress/templates/"
            "nextcloud-private-ingress.init.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("for _ in $(seq 1 12); do", template)
        self.assertIn("/usr/local/sbin/nextcloud-private-ingress-converge", template)
        self.assertIn("sleep 5", template)

    def test_immich_private_ingress_allows_slow_sidecar_startup(self):
        tasks = (
            REPO_ROOT
            / "apps/immich/ansible/roles/immich-private-ingress/tasks/main.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("retries: 120", tasks)
        self.assertIn("delay: 5", tasks)
        self.assertIn("immich_private_ingress_tailscale_container_name", tasks)


if __name__ == "__main__":
    unittest.main()
