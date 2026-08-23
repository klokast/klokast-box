#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "ansible" / "roles" / "ops-controller" / "files" / "klokast-controller-guard"
HA = REPO_ROOT / "ansible" / "bin" / "ops-controller-ha"
AUTHKEY = REPO_ROOT / "klokast-ops" / "tailscale" / "bin" / "ts-authkey-mint"
HA_SOURCE = HA.read_text(encoding="utf-8")
EXAMPLE_CONFIG = REPO_ROOT / "ops" / "controller-ha.example.yml"
OPS_CONTROLLER_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "ops-controller" / "tasks" / "main.yml"
).read_text(encoding="utf-8")
OPS_CONTROLLER_VERIFY = (
    REPO_ROOT / "ansible" / "roles" / "ops-controller-verification" / "tasks" / "main.yml"
).read_text(encoding="utf-8")
PRIVATE_STATE_TRANSFER = (
    REPO_ROOT / "ansible" / "roles" / "ops-private-state-transfer" / "tasks" / "main.yml"
).read_text(encoding="utf-8")
OPS_VARS = (
    REPO_ROOT / "ansible" / "inventory" / "group_vars" / "ops.yml"
).read_text(encoding="utf-8")
REHOME = (REPO_ROOT / "ansible" / "bin" / "rehome-public-checkout").read_text(
    encoding="utf-8"
)


class OpsControllerHaTest(unittest.TestCase):
    def run_guard(self, *args):
        return subprocess.run(
            [str(GUARD), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_missing_marker_is_legacy_active_for_rollout(self):
        result = self.run_guard(
            "--marker",
            "/tmp/does-not-exist-klokast-controller-ha.json",
            "--require-active",
            "--status",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertFalse(status["configured"])
        self.assertTrue(status["active"])
        self.assertEqual(status["role"], "legacy-unconfigured")

    def test_standby_marker_blocks_active_requirement(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as marker:
            marker.write('{"schema_version":1,"role":"standby","hostname":"boxa-ops","active_box":"boxb"}\n')
            marker.flush()

            result = self.run_guard("--marker", marker.name, "--require-active")

        self.assertEqual(result.returncode, 78)
        self.assertIn("controller is not active", result.stderr)

    def test_authkey_mint_checks_guard_before_provider_secret(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as marker:
            marker.write('{"schema_version":1,"role":"standby","hostname":"boxa-ops","active_box":"boxb"}\n')
            marker.flush()
            env = os.environ.copy()
            env["KLOKAST_CONTROLLER_GUARD"] = str(GUARD)
            env["KLOKAST_CONTROLLER_HA_MARKER"] = marker.name

            result = subprocess.run(
                [
                    str(AUTHKEY),
                    "--purpose",
                    "ops",
                    "--hostname",
                    "boxa-ops",
                    "--tags",
                    "tag:ops",
                ],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("controller is not active", result.stderr)
        self.assertNotIn("secret file not readable", result.stderr)

    def test_bootstrap_standby_dry_run_uses_standby_flag(self):
        result = subprocess.run(
            [
                str(HA),
                "--config",
                str(EXAMPLE_CONFIG),
                "bootstrap-standby",
                "--box",
                "boxa",
                "--active",
                "boxb",
                "--dry-run-plan",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("provision-ops-vm --box boxa --standby", result.stdout)

    def test_marker_directory_remains_guard_readable(self):
        self.assertIn("doas install -d -m 0755 -o root -g root /etc/klokast", HA_SOURCE)
        self.assertNotIn("doas install -d -m 0700 -o root -g root /etc/klokast", HA_SOURCE)
        self.assertIn("base64.b64encode(payload.encode", HA_SOURCE)
        self.assertIn("base64 -d", HA_SOURCE)
        self.assertIn("- path: /etc/klokast\n      owner: root\n      group: root\n      mode: \"0755\"", OPS_CONTROLLER_TASKS)

    def test_sync_state_preserves_destination_repo_checkout(self):
        self.assertNotIn("rm -rf /home/smith/src/klokast/klokast-box", HA_SOURCE)

    def test_controller_ha_has_no_static_active_controller(self):
        config = EXAMPLE_CONFIG.read_text(encoding="utf-8")
        self.assertNotIn("preferred_active:", config)

    def test_checked_in_config_is_only_an_explicit_example(self):
        self.assertFalse((REPO_ROOT / "ops" / "controller-ha.yml").exists())
        self.assertIn("controller-ha.example.yml", str(EXAMPLE_CONFIG))
        self.assertNotIn("DEFAULT_CONFIG", HA_SOURCE)

    def test_strict_config_rejects_unknown_and_duplicate_fields(self):
        for content, message in (
            (
                "schema_version: 1\nremote_user: smith\nrepo_dir: ~/src/klokast/klokast-box\n"
                "preferred_active: boxa\ncontrollers: [{box: boxa, hostname: boxa-ops}]\n",
                "unknown field: preferred_active",
            ),
            (
                "schema_version: 1\nremote_user: smith\nrepo_dir: ~/src/klokast/klokast-box\n"
                "controllers: [{box: boxa, hostname: boxa-ops}, {box: boxa, hostname: boxa-ops}]\n",
                "duplicate controller box: boxa",
            ),
        ):
            with self.subTest(message=message), tempfile.NamedTemporaryFile(
                "w", encoding="utf-8"
            ) as config:
                config.write(content)
                config.flush()
                result = subprocess.run(
                    [str(HA), "--config", config.name, "validate-config"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)

    def test_airunner_has_no_implicit_public_config(self):
        environment = os.environ.copy()
        environment.pop("KLOKAST_CONTROLLER_HA_CONFIG", None)
        with mock.patch.dict(os.environ, environment, clear=True):
            loader = SourceFileLoader("ops_controller_ha_resolution_test", str(HA))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            module = importlib.util.module_from_spec(spec)
            loader.exec_module(module)
            with mock.patch.object(module.getpass, "getuser", return_value="agent"), \
                    mock.patch.object(module.sys, "platform", "linux"):
                with self.assertRaises(SystemExit):
                    module.resolve_config_path()

    def test_planned_switchover_is_fail_closed(self):
        self.assertIn('subparsers.add_parser("switchover")', HA_SOURCE)
        self.assertIn("both controllers must be reachable", HA_SOURCE)
        self.assertIn("repository must match its live upstream", HA_SOURCE)
        self.assertIn("timeout 12 git ls-remote", HA_SOURCE)
        switchover_source = HA_SOURCE.split("def switchover", 1)[1].split(
            "def sanitize_standby", 1
        )[0]
        self.assertLess(
            switchover_source.index('set_marker(config, args.old_active, "standby"'),
            switchover_source.index('set_marker(config, args.new_active, "active"'),
        )

    def test_standby_sanitization_requires_confirmation(self):
        self.assertIn('subparsers.add_parser("sanitize-standby")', HA_SOURCE)
        self.assertIn("--confirm is required to remove standby credentials", HA_SOURCE)

    def test_controller_checkout_uses_public_https_without_deploy_key(self):
        self.assertIn("repo: https://github.com/klokast/klokast-box.git", OPS_VARS)
        self.assertNotIn("Generate the infra GitHub deploy key", OPS_CONTROLLER_TASKS)
        self.assertIn("Inspect obsolete controller GitHub deploy-key material", OPS_CONTROLLER_VERIFY)
        self.assertIn("- /bin/su\n      - -s\n      - /bin/sh", OPS_CONTROLLER_VERIFY)
        self.assertIn("timeout 20 git -C", OPS_CONTROLLER_VERIFY)
        self.assertIn("Rehome existing controller checkouts onto public history", OPS_CONTROLLER_TASKS)
        self.assertIn("dest: /usr/local/sbin/rehome-public-checkout", OPS_CONTROLLER_TASKS)
        self.assertIn("merge-base --is-ancestor", REHOME)
        self.assertIn(".private-history-", REHOME)
        self.assertNotIn("rm -", REHOME)

    def test_standby_private_state_excludes_provider_credentials(self):
        self.assertIn("Remove root-only provider credentials from a standby controller", PRIVATE_STATE_TRANSFER)
        self.assertIn("Assert standby controller has no root-only provider credentials", OPS_CONTROLLER_VERIFY)
        self.assertIn("ops_controller_check_ha.active", OPS_CONTROLLER_VERIFY)
        self.assertIn("/etc/klokast/secret-authority/instance-bootstrap/github-app.pem", HA_SOURCE)
        self.assertIn("/etc/klokast/secret-authority/allowed-signers-private-instance", HA_SOURCE)
        self.assertIn("/etc/klokast/secret-authority/allowed-signers-static-site", HA_SOURCE)
        self.assertIn("/etc/klokast/private-instance/github-readonly", HA_SOURCE)


if __name__ == "__main__":
    unittest.main()
