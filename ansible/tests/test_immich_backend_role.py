#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = REPO_ROOT / "apps" / "immich" / "ansible" / "roles" / "immich-backend" / "tasks" / "main.yml"
DEFAULTS = REPO_ROOT / "apps" / "immich" / "ansible" / "roles" / "immich-backend" / "defaults" / "main.yml"
DEPLOY_TEMPLATE = REPO_ROOT / "apps" / "immich" / "ansible" / "roles" / "immich-backend" / "templates" / "immich-backend-deploy.sh.j2"
POSTGRES_ENV = REPO_ROOT / "apps" / "immich" / "ansible" / "roles" / "immich-backend" / "templates" / "immich-postgres.env.j2"


class ImmichBackendRoleTest(unittest.TestCase):
    def test_restic_sftp_installs_client_before_ssh_use(self):
        tasks = TASKS.read_text(encoding="utf-8")
        defaults = DEFAULTS.read_text(encoding="utf-8")

        self.assertIn("openssh-client-default", defaults)
        self.assertIn("openssh", defaults)
        self.assertIn("immich_restic_sftp_port: 22220", defaults)
        self.assertIn("Ensure active Restic SFTP client tools are installed", tasks)
        self.assertIn("Ensure passive Restic SFTP server tools are installed", tasks)
        self.assertIn("Keep passive Restic SFTP OpenSSH running", tasks)
        self.assertIn("immich_restic_sftp_client_packages", tasks)
        self.assertIn("immich_restic_sftp_server_packages", tasks)
        self.assertIn("Resolve the active backend MagicDNS name for Restic SFTP", tasks)
        self.assertIn("Resolve the passive backend MagicDNS name for Restic SFTP", tasks)
        self.assertIn("Ensure active backend resolves the passive Restic SFTP host", tasks)
        self.assertIn(
            "ListenAddress {{ immich_passive_backend_tailscale_ipv4.stdout | trim }}:{{ immich_restic_sftp_port }}",
            tasks,
        )
        self.assertIn("ssh-keyscan -p {{ immich_restic_sftp_port | quote }}", tasks)
        self.assertIn('key_options: "from=\\"{{ immich_active_backend_tailscale_ipv4.stdout | trim }}\\",restrict"', tasks)
        self.assertIn("immich_manage_default_restic_sftp | default(false) | bool", tasks)
        self.assertLess(
            tasks.index("Ensure active Restic SFTP client tools are installed"),
            tasks.index("Trust the passive backend SSH host key for Restic SFTP"),
        )
        self.assertLess(
            tasks.index("Keep passive Restic SFTP OpenSSH running"),
            tasks.index("Trust the passive backend SSH host key for Restic SFTP"),
        )
        self.assertLess(
            tasks.index("Resolve the passive backend MagicDNS name for Restic SFTP"),
            tasks.index("Configure passive Restic SFTP OpenSSH policy"),
        )
        self.assertLess(
            tasks.index("Resolve the active backend MagicDNS name for Restic SFTP"),
            tasks.index("Authorize active Restic SFTP key on the passive backend"),
        )
        self.assertLess(
            tasks.index("Ensure active backend resolves the passive Restic SFTP host"),
            tasks.index("Trust the passive backend SSH host key for Restic SFTP"),
        )
        self.assertLess(
            tasks.index("Ensure active Restic SFTP client tools are installed"),
            tasks.index("Verify the active backend can reach the passive Restic SFTP target"),
        )
        self.assertLess(
            tasks.index("Ensure active backend resolves the passive Restic SFTP host"),
            tasks.index("Verify the active backend can reach the passive Restic SFTP target"),
        )

    def test_deploy_owns_postgres_and_valkey_named_volumes(self):
        deploy = DEPLOY_TEMPLATE.read_text(encoding="utf-8")
        postgres_env = POSTGRES_ENV.read_text(encoding="utf-8")

        self.assertIn("PGDATA=/var/lib/postgresql/data/pgdata", postgres_env)
        self.assertIn("ensure_volume_owner() {", deploy)
        self.assertIn('-v "$volume:/target"', deploy)
        self.assertIn('-c "chown -R $uid:$gid /target"', deploy)
        self.assertIn("postgres_runtime_uid=$(image_user_id \"$immich_postgres_ref\" postgres u)", deploy)
        self.assertIn("postgres_runtime_gid=$(image_user_id \"$immich_postgres_ref\" postgres g)", deploy)
        self.assertIn("valkey_runtime_uid=$(image_user_id \"$valkey_ref\" valkey u)", deploy)
        self.assertIn("valkey_runtime_gid=$(image_user_id \"$valkey_ref\" valkey g)", deploy)
        self.assertIn(
            'ensure_volume_owner "$volume_postgres" "$postgres_runtime_uid" "$postgres_runtime_gid"',
            deploy,
        )
        self.assertIn(
            'ensure_volume_owner "$volume_valkey" "$valkey_runtime_uid" "$valkey_runtime_gid"',
            deploy,
        )
        self.assertLess(
            deploy.index('ensure_volume_owner "$volume_postgres"'),
            deploy.index("podman_cmd pod create"),
        )
        self.assertLess(
            deploy.index('ensure_volume_owner "$volume_valkey"'),
            deploy.index("podman_cmd pod create"),
        )


if __name__ == "__main__":
    unittest.main()
