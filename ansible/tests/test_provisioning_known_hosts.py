#!/usr/bin/env python3
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
PROVISION_BOX = REPO_ROOT / "ansible" / "bin" / "provision-box"
BOOTSTRAP_DOM0 = REPO_ROOT / "ansible" / "bin" / "bootstrap-dom0"
REINSTALL_BOX = REPO_ROOT / "ansible" / "bin" / "reinstall-box"
ALL_VARS = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all.yml"


class ProvisioningKnownHostsTest(unittest.TestCase):
    def test_phase_wrappers_use_box_scoped_controller_files(self):
        wrappers = (
            (PROVISION_BOX, 'RUN_DIR="$REPO_DIR/.run/provision-box-${BOX}"'),
            (BOOTSTRAP_DOM0, 'RUN_DIR="$REPO_DIR/.run/bootstrap-dom0-${NODE}"'),
        )

        for path, run_dir in wrappers:
            with self.subTest(wrapper=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn(run_dir, text)
                self.assertIn(
                    'BOOTSTRAP_KNOWN_HOSTS_FILE="$RUN_DIR/bootstrap_known_hosts"',
                    text,
                )
                self.assertIn(
                    'DOM0_KNOWN_HOSTS_FILE="$RUN_DIR/dom0_known_hosts"',
                    text,
                )
                self.assertIn(
                    'CONNECTION_VARS_FILE="$RUN_DIR/controller-connection-vars.json"',
                    text,
                )
                self.assertIn('chmod 0700 "$RUN_DIR"', text)
                self.assertIn(
                    'chmod 0600 "$BOOTSTRAP_KNOWN_HOSTS_FILE" "$DOM0_KNOWN_HOSTS_FILE"',
                    text,
                )
                connection_vars = 'cmd+=(-e "@$CONNECTION_VARS_FILE")'
                operator_args = 'cmd+=("${EXTRA_ARGS[@]}")'
                self.assertIn(connection_vars, text)
                self.assertLess(text.index(connection_vars), text.index(operator_args))

    def test_reinstall_wrapper_uses_box_scoped_controller_file(self):
        text = REINSTALL_BOX.read_text(encoding="utf-8")

        self.assertIn('RUN_DIR="$REPO_DIR/.run/reinstall-box-${BOX}"', text)
        self.assertIn(
            'BOOTSTRAP_KNOWN_HOSTS_FILE="$RUN_DIR/bootstrap_known_hosts"',
            text,
        )
        self.assertIn('chmod 0700 "$RUN_DIR"', text)
        self.assertIn('chmod 0600 "$BOOTSTRAP_KNOWN_HOSTS_FILE"', text)

    def test_inventory_fallbacks_are_per_user_and_per_box(self):
        data = yaml.safe_load(ALL_VARS.read_text(encoding="utf-8"))

        self.assertEqual(
            data["bootstrap_known_hosts_file"],
            "{{ lookup('env', 'HOME') }}/.ssh/known_hosts_klokast_{{ node_name }}_bootstrap",
        )
        self.assertEqual(
            data["dom0_known_hosts_file"],
            "{{ lookup('env', 'HOME') }}/.ssh/known_hosts_klokast_{{ node_name }}_dom0",
        )

    def test_provisioning_paths_do_not_use_shared_tmp_files(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PROVISION_BOX, BOOTSTRAP_DOM0, REINSTALL_BOX, ALL_VARS)
        )

        self.assertNotIn("/tmp/klokast-bootstrap-known_hosts", combined)
        self.assertNotIn("/tmp/klokast-dom0-known_hosts", combined)


if __name__ == "__main__":
    unittest.main()
