"""The provisioning interface keeps the controller, lifecycle, and lock gates."""
import contextlib
import io
import json
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from router_provision_fixtures import load_cli, REPO

sys.path.insert(0, str(REPO / "ansible/lib"))
import platform_source


class ProvisionCliTests(unittest.TestCase):
    def test_status_uses_the_protected_reader(self):
        cli = load_cli()
        output = io.StringIO()
        with patch.object(cli, 'provisioning_status', return_value={'box': 'boxa'}) as read, \
                contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(['status', '--box', 'boxa']), 0)
        read.assert_called_once_with('boxa')
        self.assertEqual(json.loads(output.getvalue()), {'box': 'boxa'})

    def test_phase_requires_the_parent_lock_before_controller_access(self):
        cli = load_cli()
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(cli.transport, 'require_controller') as controller, \
                contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(cli.main(['phase', '--box', 'boxa', '--phase', 'prepare']), 2)
        controller.assert_not_called()
        self.assertIn('parent installation lock', output.getvalue())

    def test_both_commands_reject_production_before_reading_router_state(self):
        cli = load_cli()
        status = {'active': True, 'configured': True, 'role': 'active', 'hostname': 'boxa-ops'}
        for argv in (['status', '--box', 'boxa'],
                     ['phase', '--box', 'boxa', '--phase', 'prepare']):
            with self.subTest(argv=argv), \
                    patch.dict(os.environ, {'KLOKAST_INSTALLATION_LOCK_FD': '9'}), \
                    patch.object(cli.transport.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='smith')), \
                    patch.object(cli.transport.socket, 'gethostname', return_value='boxa-ops'), \
                    patch.object(cli.transport, 'command', return_value=json.dumps(status)) as command, \
                    patch.object(platform_source, 'MODE') as mode, \
                    patch.object(platform_source, 'read_json', return_value={
                        'schema_version': 1, 'lifecycle': 'production'}), \
                    contextlib.redirect_stderr(io.StringIO()) as output:
                mode.lstat.return_value = SimpleNamespace(st_uid=0, st_mode=0o644)
                self.assertEqual(cli.main(argv), 2)
                command.assert_called_once()
                self.assertIn('development only', output.getvalue())
