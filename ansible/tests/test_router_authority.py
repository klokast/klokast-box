"""Baseline adoption follows the activated engine, not standing update policy."""
import json
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import router_update_controller as controller


def router_cli():
    path = REPO / 'ansible/bin/platform-router-update'
    loader = SourceFileLoader('router_update_cli_authority_test', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RouterAuthorityTests(unittest.TestCase):
    def test_accepted_legacy_qualification_checks_protected_source(self):
        cli = router_cli()
        inspected = {'evidence_directory': '/secure/op', 'operation': 'a' * 24,
                     'adoption_findings': ['router assignment or transaction state differs from the inspection mode']}
        guest, dom0 = {'observed_at': 'now'}, {'accepted_record_present': True}
        source = {'assignment': {'x': 1}, 'generation': {'x': 2}}
        with patch.object(cli.transport, 'load', side_effect=[guest, dom0]), \
                patch.object(cli, 'accepted_source_at', return_value=source) as reader, \
                patch.object(cli.router_records, 'assignment', return_value={'accepted': True}), \
                patch.object(cli.router_generations, 'generation', return_value={'origin': 'legacy'}), \
                patch.object(cli.router_updates, 'legacy_live') as validate:
            self.assertEqual(cli.qualify_inspected_legacy('boxa', inspected, 'b' * 40),
                             (guest, dom0, source))
        reader.assert_called_once_with('boxa', Path('/secure/op'), 'a' * 24, 'b' * 40, 'accepted-source')
        validate.assert_called_once()

    def test_accepted_legacy_qualification_refuses_changed_source(self):
        cli = router_cli()
        inspected = {'evidence_directory': '/secure/op', 'operation': 'a' * 24}
        with patch.object(cli, 'accepted_source_at', return_value={'changed': True}):
            with self.assertRaisesRegex(controller.UpdateError, 'changed during qualification'):
                cli.require_accepted_source_unchanged('boxa', inspected, 'b' * 40, {'changed': False})

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
