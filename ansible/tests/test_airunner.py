"""Controller-side runner placement, dry-run and interrupted enrollment behavior."""
import contextlib
import io
from pathlib import Path
import unittest
from unittest.mock import patch
from test_infrastructure_guest import load, ROOT


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.cli = load('airunner_cli_test', ROOT / 'ansible/bin/airunner')
        self.view = {'registry': {'boxes': {'boxa': {}}, 'airunners': ['boxa-air']}}

    def test_unknown_or_undeclared_guest_is_not_provisioned(self):
        for box, view in [('boxb', self.view), ('boxa', {'registry': {'boxes': {'boxa': {}}, 'airunners': []}})]:
            with self.assertRaises(RuntimeError): self.cli.plan('provision', box, view)

    def test_dry_run_has_no_build_or_remote_mutation(self):
        with patch.object(self.cli, 'desired', return_value=self.view), patch.object(self.cli, 'provision') as provision, \
                patch.object(self.cli.runtime, 'vm_update_installation_lock') as lock, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.cli.main(['provision', '--box', 'boxa', '--dry-run-plan']), 0)
        provision.assert_not_called(); lock.assert_not_called()

    def test_standby_refusal_precedes_any_mutation(self):
        with patch.object(self.cli, 'desired', side_effect=self.cli.source.SourceError('controller is standby')), \
                patch.object(self.cli, 'provision') as provision, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.cli.main(['provision', '--box', 'boxa']), 1)
        provision.assert_not_called()

    def test_interrupted_enrollment_retains_bootstrap_for_retry(self):
        with patch.object(self.cli, 'prepare_image', return_value='a' * 24), \
                patch.object(self.cli, 'guest_config', return_value={}), \
                patch.object(self.cli, 'network') as network, \
                patch.object(self.cli, 'ansible', side_effect=RuntimeError('enrollment interrupted')), \
                patch.object(self.cli, 'verify') as verify, self.assertRaises(RuntimeError):
            self.cli.provision('boxa', self.view, 1004, 1004)
        network.assert_called_once_with('boxa', self.view, bootstrap=True)
        verify.assert_not_called()

    def test_success_removes_bootstrap_before_verification(self):
        events = []
        with patch.object(self.cli, 'prepare_image', return_value='a' * 24), \
                patch.object(self.cli, 'guest_config', return_value={}), \
                patch.object(self.cli, 'network', side_effect=lambda *a, **kw: events.append('bootstrap' if kw else 'close')), \
                patch.object(self.cli, 'ansible', side_effect=lambda *a, **kw: events.append('provision')), \
                patch.object(self.cli, 'verify', side_effect=lambda *a: events.append('verify')):
            self.cli.provision('boxa', self.view, 1004, 1004)
        self.assertEqual(events, ['bootstrap', 'provision', 'close', 'verify'])


if __name__ == '__main__': unittest.main()
