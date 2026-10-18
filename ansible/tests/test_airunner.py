"""Controller-side runner placement, dry-run and interrupted enrollment behavior."""
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
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

    def test_absent_local_controller_has_no_central_build_fallback(self):
        with patch.object(self.cli.socket, 'gethostname', return_value='boxb-ops'), \
                patch.object(self.cli.subprocess, 'run', return_value=SimpleNamespace(returncode=255)) as remote, \
                patch.object(self.cli.infrastructure_images.subprocess, 'check_output') as local:
            with self.assertRaisesRegex(RuntimeError, 'no fallback'):
                self.cli.prepare_image('boxa', 'air-alpine-v1')
        local.assert_not_called()
        self.assertEqual(remote.call_args.args[0][:6], ['tailscale', 'ssh', 'smith@boxa-ops', 'sh', '-s', '--'])
        self.assertIn('prepare', remote.call_args.args[0])

    def test_own_controller_does_not_self_ssh(self):
        with patch.object(self.cli.socket, 'gethostname', return_value='boxa-ops'), \
                patch.object(self.cli.infrastructure_images.subprocess, 'check_output', return_value='{"state":"failed"}') as local, \
                patch.object(self.cli.subprocess, 'run') as remote:
            with self.assertRaisesRegex(RuntimeError, 'qualified image'):
                self.cli.prepare_image('boxa', 'ops-alpine-v1')
        remote.assert_not_called(); local.assert_called_once()

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
        with patch.object(self.cli, 'command'), patch.object(self.cli, 'prepare_image', return_value='a' * 24), \
                patch.object(self.cli, 'guest_config', return_value={}), \
                patch.object(self.cli, 'network') as network, \
                patch.object(self.cli, 'ansible', side_effect=RuntimeError('enrollment interrupted')), \
                patch.object(self.cli, 'verify') as verify, self.assertRaises(RuntimeError):
            self.cli.provision('boxa', self.view, 1004, 1004)
        network.assert_called_once_with('boxa', self.view, bootstrap=True)
        verify.assert_not_called()

    def test_network_passes_matching_compiler_metadata_to_verification(self):
        compiled = {'registry_sha256': 'a' * 64, 'compiler_version': 7}
        with patch.object(self.cli.compiler, 'compile_registry', return_value=compiled), \
                patch.object(self.cli, 'ansible') as ansible:
            self.cli.network('boxa', self.view, bootstrap=True)
        variables = ansible.call_args.args[2]
        self.assertEqual(json.loads(variables['platform_resources_desired_json']), compiled)
        self.assertEqual(variables['platform_resources_registry_sha256'], compiled['registry_sha256'])
        self.assertEqual(variables['platform_resources_compiler_version'], compiled['compiler_version'])

    def test_success_removes_bootstrap_before_verification(self):
        events = []
        with patch.object(self.cli, 'command', side_effect=lambda *a: events.append('git-preflight')), \
                patch.object(self.cli, 'prepare_image', return_value='a' * 24), \
                patch.object(self.cli, 'guest_config', return_value={}), \
                patch.object(self.cli, 'network', side_effect=lambda *a, **kw: events.append('bootstrap' if kw else 'close')), \
                patch.object(self.cli, 'ansible', side_effect=lambda play, *a, **kw: events.append(play)), \
                patch.object(self.cli, 'verify', side_effect=lambda *a: events.append('verify')):
            self.cli.provision('boxa', self.view, 1004, 1004)
        self.assertEqual(events, ['git-preflight', 'bootstrap', '69-air-provision.yml', 'close',
                                  '69-air-user-bootstrap.yml', 'verify'])

    def test_missing_git_capability_stops_before_vm_creation(self):
        with patch.object(self.cli, 'command', side_effect=RuntimeError('App is missing')), \
                patch.object(self.cli, 'prepare_image') as image, \
                patch.object(self.cli, 'network') as network, self.assertRaises(RuntimeError):
            self.cli.provision('boxa', self.view, 1004, 1004)
        image.assert_not_called(); network.assert_not_called()

    def test_failed_registration_does_not_leave_bootstrap_network_open(self):
        events = []
        def ansible(play, *args, **kwargs):
            if play == '69-air-user-bootstrap.yml': raise RuntimeError('registration denied')
        with patch.object(self.cli, 'command'), patch.object(self.cli, 'prepare_image', return_value='a' * 24), \
                patch.object(self.cli, 'guest_config', return_value={}), \
                patch.object(self.cli, 'network', side_effect=lambda *a, **kw: events.append('open' if kw else 'close')), \
                patch.object(self.cli, 'ansible', side_effect=ansible), patch.object(self.cli, 'verify') as verify, \
                self.assertRaisesRegex(RuntimeError, 'registration denied'):
            self.cli.provision('boxa', self.view, 1004, 1004)
        self.assertEqual(events, ['open', 'close'])
        verify.assert_not_called()

    def test_retirement_revokes_after_stop_and_before_success(self):
        events = []
        args = SimpleNamespace(action='retire', box='boxa', test_only=False, agent_uid=1004, agent_gid=1004)
        with patch.object(self.cli, 'ansible', side_effect=lambda *a, **kw: events.append('stop')), \
                patch.object(self.cli, 'command', side_effect=lambda *a, **kw: events.append('revoke')), \
                patch.object(self.cli, 'network', side_effect=lambda *a: events.append('network')):
            self.cli.retire(args, self.view)
        self.assertEqual(events, ['stop', 'revoke', 'network'])

    def test_working_cutover_detaches_before_acquiring_the_child_lock(self):
        with patch.object(self.cli, 'desired', return_value=self.view), \
                patch.object(self.cli.runtime, 'require_active_controller'), \
                patch.object(self.cli.runtime, 'vm_update_installation_lock') as lock, \
                patch.object(self.cli, 'detach_cutover') as detach:
            self.assertEqual(self.cli.main(['migrate','--box','boxa','--phase','cutover']), 0)
        detach.assert_called_once(); lock.assert_not_called()

    def test_competing_operation_lock_prevents_migration(self):
        with patch.object(self.cli, 'desired', return_value=self.view), \
                patch.object(self.cli.runtime, 'require_active_controller'), \
                patch.object(self.cli.runtime, 'vm_update_installation_lock', side_effect=RuntimeError('operation busy')), \
                patch.object(self.cli, 'migrate') as migrate, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.cli.main(['migrate','--box','boxa']), 1)
        migrate.assert_not_called()

    def test_retirement_requires_updated_desired_state(self):
        with self.assertRaisesRegex(RuntimeError, 'remove'):
            self.cli.plan('retire', 'boxa', self.view)
        self.view['registry']['airunners'].append('boxa-ops-airunner')
        with self.assertRaisesRegex(RuntimeError, 'remove the legacy'):
            self.cli.plan('retire-legacy', 'boxa', self.view)


if __name__ == '__main__': unittest.main()
