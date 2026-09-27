"""Baseline adoption follows the activated engine, not standing update policy."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import router_update_controller as controller


class RouterAuthorityTests(unittest.TestCase):
    def test_activated_engine_reader_accepts_receipt_without_policy(self):
        engine = 'a' * 40
        status = {
            'valid': True,
            'activation_present': True,
            'pending_activation': False,
            'engine_repository': 'https://github.com/klokast/klokast-box',
            'engine_ref': 'main',
            'engine_commit': engine,
            'activation_receipt_sha256': 'b' * 64,
        }
        with patch.object(controller, 'command', return_value=json.dumps(status)) as command:
            self.assertEqual(controller.approved_engine(), engine)
        argv = command.call_args.args[0]
        self.assertEqual(argv[-2:], ['engine', 'status'])
        self.assertIn('/usr/local/sbin/ksa-instance', argv)
        self.assertNotIn('vm-update-policy', argv)

    def test_reader_rejects_pending_or_invalid_activation(self):
        status = {
            'valid': True,
            'activation_present': True,
            'pending_activation': False,
            'engine_repository': 'https://github.com/klokast/klokast-box',
            'engine_ref': 'main',
            'engine_commit': 'a' * 40,
            'activation_receipt_sha256': 'b' * 64,
        }
        for change in ({'valid': False}, {'activation_present': False},
                       {'pending_activation': True}, {'activation_receipt_sha256': ''},
                       {'engine_repository': 'https://example.invalid/other'}):
            with self.subTest(change=change), patch.object(controller, 'command',
                    return_value=json.dumps({**status, **change})):
                self.assertIsNone(controller.approved_engine())

    def test_baseline_and_recovery_install_read_activation_receipt(self):
        for path in ('ansible/playbooks/74-router-baseline-adopt.yml',
                     'ansible/roles/router-update-recovery/tasks/main.yml'):
            with self.subTest(path=path):
                document = yaml.safe_load((REPO / path).read_text())
                tasks = document[0]['tasks'] if document and 'tasks' in document[0] else document
                source = next(task for task in tasks if task.get('register') in
                              ('router_baseline_source', 'router_recovery_source'))
                self.assertEqual(source['ansible.builtin.command']['argv'][-2:], ['engine', 'status'])
                self.assertIn('/usr/local/sbin/ksa-instance',
                              source['ansible.builtin.command']['argv'])


if __name__ == '__main__':
    unittest.main()
