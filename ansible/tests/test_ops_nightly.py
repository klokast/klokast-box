"""Nightly decisions must wait for complete replacement and reboot evidence."""
import contextlib
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_infrastructure_guest import load, ROOT


class NightlyTests(unittest.TestCase):
    def setUp(self):
        self.m = load('ops_nightly_test', ROOT / 'ansible/bin/ops-controller-nightly')
        self.observed = {'assignment.json': {}, 'replacement.json': None, 'common': True}
        for target, name, kwargs in (
            (self.m, 'placement', {'return_value': ('active', 'boxa', 'boxb')}),
            (self.m.inputs, 'revision', {'return_value': 'e' * 40}),
            (self.m.runtime, 'vm_update_installation_lock', {'side_effect': contextlib.nullcontext}),
            (self.m.replacement, 'authority', {'return_value': 'boxa'}),
            (self.m.replacement, 'records', {'return_value': self.observed}),
            (self.m.runpy, 'run_path', {'return_value': {'verify_credentials': lambda *args: None}}),
            (self.m, 'service_run', {'return_value': {'state':'complete','replaced':True}}),
        ):
            mock = patch.object(target, name, **kwargs); value = mock.start(); self.addCleanup(mock.stop)
            setattr(self, 'mock_' + name, value)

    def test_migrated_controller_uses_the_same_service_coordinator(self):
        self.assertTrue(self.m.run()['replaced'])
        self.mock_service_run.assert_called_once_with('boxb', 'ops', False, {})

    def test_unmigrated_controller_never_schedules_root_data_migration(self):
        self.observed['common'] = False
        self.assertTrue(self.m.run()['migration_required'])
        self.mock_service_run.assert_not_called()

    def test_legacy_pending_replacement_or_reboot_requires_exact_resume(self):
        self.observed['common'] = False
        for state in ({'stage':'booted'}, {'stage':'accepted','reboot_verification':{'status':'required'}},
                      {'stage':'accepted','reboot_verification':{'status':'pending'}}):
            self.observed['replacement.json'] = dict(state,operation_id='a'*24)
            with self.assertRaisesRegex(RuntimeError, '--resume '+'a'*24): self.m.run()
        self.mock_service_run.assert_not_called()

    def test_standby_invocation_skips_without_sources_or_jobs(self):
        self.mock_placement.return_value = ('standby', 'boxa', 'boxb')
        self.assertEqual(self.m.run()['state'], 'skipped')
        self.mock_revision.assert_not_called(); self.mock_service_run.assert_not_called()

    def test_incomplete_credential_handoff_blocks_all_work(self):
        def fail(*args): raise RuntimeError('handoff incomplete')
        self.mock_run_path.return_value = {'verify_credentials': fail}
        with self.assertRaisesRegex(RuntimeError, 'handoff incomplete'): self.m.run()
        self.mock_service_run.assert_not_called()

    def test_controller_uses_common_dry_plan(self):
        self.m.run(True)
        self.mock_service_run.assert_called_once_with('boxb', 'ops', True, {})


class DurableRebootTests(unittest.TestCase):
    def setUp(self):
        import test_ops_replacement as fixtures
        self.f = fixtures.ReplacementTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.replace(); self.f.accept()
        self.evidence = self.f.root / 'reboot.json'
        self.before = {'guard': {'box': 'boxa', 'role': 'standby', 'active_box': 'boxb'},
                       'identity': {'tailscale': 'one'}, 'boot_id': 'first'}
        self.evidence.write_text(json.dumps(self.before))

    def record(self, complete=False):
        return self.f.m.reboot_record('boxa', self.f.state()['operation_id'], self.evidence, complete)

    def test_disconnect_preserves_original_identity_and_requires_new_boot(self):
        self.record()
        self.assertEqual(self.f.state()['reboot_verification']['status'], 'pending')
        with self.assertRaisesRegex(RuntimeError, 'new boot proof'): self.record(True)
        after = dict(self.before, boot_id='second'); self.evidence.write_text(json.dumps(after))
        self.record()
        self.assertEqual(self.f.state()['reboot_verification']['before'], self.before)
        self.record(True)
        self.assertEqual(self.f.state()['reboot_verification']['status'], 'complete')

    def test_changed_identity_refuses_and_keeps_pending_record(self):
        self.record()
        self.evidence.write_text(json.dumps(dict(self.before, boot_id='second', identity={'tailscale': 'foreign'})))
        with self.assertRaisesRegex(RuntimeError, 'identity'): self.record(True)
        self.assertEqual(self.f.state()['reboot_verification']['status'], 'pending')


class LocalImageTests(unittest.TestCase):
    def test_profiles_use_the_same_frozen_local_checkout(self):
        import infrastructure_images as images
        from types import SimpleNamespace
        for profile in images.PROFILES:
            with self.subTest(profile=profile), patch.object(images.subprocess, 'run',
                    return_value=SimpleNamespace(returncode=0, stdout='{"state":"candidate-reused"}', stderr='')) as run:
                images.local_action('boxa', profile, 'a' * 40, 'prepare')
                self.assertEqual(run.call_args.args[0][2], 'smith@boxa-ops')
                self.assertEqual(run.call_args.args[0][-1], profile)
                self.assertIn('checkout --quiet --detach "$revision"', run.call_args.kwargs['input'])
                self.assertIn('--profile "$profile"', run.call_args.kwargs['input'])

    def test_invalid_selectors_stop_before_remote_execution(self):
        import infrastructure_images as images
        with patch.object(images.subprocess, 'run') as run:
            for args in (('boxa', 'unknown', 'a' * 40, 'prepare'),
                         ('boxa', 'shared-alpine-v1', 'main', 'prepare'),
                         ('boxa', 'shared-alpine-v1', 'a' * 40, 'cleanup')):
                with self.assertRaisesRegex(RuntimeError, 'invalid local image'):
                    images.local_action(*args)
            run.assert_not_called()


if __name__ == '__main__': unittest.main()
