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
        self.old = {'stage': 'accepted', 'operation_id': 'a' * 24, 'image': 'b' * 24, 'lv_uuid': 'old',
                    'requested_configuration': {'engine_commit': 'old-public', 'instance_commit': 'old-instance'}}
        self.new = dict(self.old, operation_id='c' * 24, image='d' * 24, lv_uuid='new',
                        reboot_verification={'status': 'complete'})
        self.observed = {'assignment.json': dict(self.old, stage='ready'), 'replacement.json': self.old}
        for target, name, kwargs in (
            (self.m, 'placement', {'return_value': ('active', 'boxa', 'boxb')}),
            (self.m.inputs, 'revision', {'return_value': 'e' * 40}),
            (self.m.runtime, 'vm_update_installation_lock', {'side_effect': contextlib.nullcontext}),
            (self.m.replacement, 'authority', {'return_value': 'boxa'}),
            (self.m.replacement, 'records', {'return_value': self.observed}),
            (self.m.runpy, 'run_path', {'return_value': {'verify_credentials': lambda *args: None}}),
            (self.m, 'command', {'return_value': ''}),
            (self.m, 'operation', {}),
            (self.m, 'local_image', {'side_effect': [{}, {'state': 'candidate-reused', 'operation_id': self.old['image']}, {}]}),
        ):
            mock = patch.object(target, name, **kwargs); value = mock.start(); self.addCleanup(mock.stop)
            setattr(self, 'mock_' + name, value)

    def test_unchanged_image_with_source_changes_never_converges_or_replaces(self):
        value = self.m.run()
        self.assertFalse(value['replaced']); self.assertTrue(value['configuration_update_needed'])
        self.assertEqual([c.args[1] for c in self.mock_operation.call_args_list], ['retire', 'resume', 'retire'])
        self.assertFalse(any(c.kwargs.get('reboot') for c in self.mock_operation.call_args_list))

    def test_already_built_unused_image_is_installed_then_reboot_verified(self):
        self.mock_local_image.side_effect = [{}, {'state': 'candidate-reused', 'operation_id': self.new['image']}, {}]
        self.mock_records.side_effect = [self.observed, {'replacement.json': self.new},
                                        {'replacement.json': self.new, 'assignment.json': self.new}]
        self.assertTrue(self.m.run()['replaced'])
        self.assertEqual(self.mock_local_image.call_args_list[0].args[2], 'cleanup-before')
        self.assertEqual(self.mock_local_image.call_args_list[-1].args[2], 'cleanup')
        calls = self.mock_operation.call_args_list
        self.assertEqual([c.args[1] for c in calls], ['retire', 'replace', 'resume', 'retire'])
        self.assertTrue(calls[2].kwargs['reboot'])

    def test_pending_replacement_or_reboot_blocks_preparation(self):
        for state in ({'stage': 'booted'}, {'reboot_verification': {'status': 'required'}},
                      {'reboot_verification': {'status': 'pending'}}):
            with self.subTest(state=state):
                self.observed['replacement.json'] = dict(self.old, **state)
                with self.assertRaisesRegex(RuntimeError, '--resume'): self.m.run()
        self.mock_operation.assert_not_called(); self.mock_local_image.assert_not_called()

    def test_standby_invocation_skips_without_sources_or_jobs(self):
        self.mock_placement.return_value = ('standby', 'boxa', 'boxb')
        self.assertEqual(self.m.run()['state'], 'skipped')
        self.mock_revision.assert_not_called(); self.mock_operation.assert_not_called()

    def test_authority_change_after_prepare_blocks_replacement(self):
        self.mock_authority.side_effect = ['boxa', 'boxa', RuntimeError('authority changed')]
        with self.assertRaisesRegex(RuntimeError, 'authority changed'): self.m.run()
        self.assertEqual([c.args[1] for c in self.mock_operation.call_args_list], ['retire'])

    def test_upstream_failure_blocks_preparation(self):
        self.mock_revision.side_effect = RuntimeError('upstream unavailable')
        with self.assertRaisesRegex(RuntimeError, 'upstream'): self.m.run()
        self.mock_operation.assert_not_called(); self.mock_local_image.assert_not_called()

    def test_build_failure_does_not_start_replacement(self):
        self.mock_local_image.side_effect = [{}, RuntimeError('build failed')]
        with self.assertRaisesRegex(RuntimeError, 'build failed'): self.m.run()
        self.assertEqual([c.args[1] for c in self.mock_operation.call_args_list], ['retire'])

    def test_reboot_failure_reports_durable_resume_and_does_not_retire(self):
        self.mock_local_image.side_effect = [{}, {'state': 'candidate-built', 'operation_id': self.new['image']}]
        state = dict(self.new, reboot_verification={'status': 'pending'})
        self.mock_records.side_effect = [self.observed, {'replacement.json': state}, {'replacement.json': state}]
        self.mock_operation.side_effect = [None, None, RuntimeError('unreachable after reboot')]
        with self.assertRaisesRegex(RuntimeError, '--resume ' + state['operation_id'] + ' --verify-reboot'): self.m.run()
        self.assertEqual(self.mock_operation.call_count, 3)

    def test_incomplete_credential_handoff_blocks_all_work(self):
        def fail(*args): raise RuntimeError('handoff incomplete')
        self.mock_run_path.return_value = {'verify_credentials': fail}
        with self.assertRaisesRegex(RuntimeError, 'handoff incomplete'): self.m.run()
        self.mock_operation.assert_not_called(); self.mock_local_image.assert_not_called()

    def test_source_change_during_preparation_stops_before_replacement(self):
        self.mock_revision.side_effect = ['e' * 40, 'e' * 40, 'f' * 40]
        with self.assertRaisesRegex(RuntimeError, 'sources changed'): self.m.run()
        self.assertEqual([c.args[1] for c in self.mock_operation.call_args_list], ['retire'])

    def test_replacement_failure_reports_recorded_resume_without_rollback(self):
        self.mock_local_image.side_effect = [{}, {'state': 'candidate-built', 'operation_id': self.new['image']}]
        state = dict(self.new, stage='booted')
        self.mock_records.side_effect = [self.observed, {'replacement.json': state}]
        self.mock_operation.side_effect = [None, RuntimeError('replacement failed')]
        with self.assertRaisesRegex(RuntimeError, '--resume ' + state['operation_id']): self.m.run()
        self.assertEqual([c.args[1] for c in self.mock_operation.call_args_list], ['retire', 'replace'])

    def test_dry_plan_does_not_run_mutations(self):
        self.assertEqual(self.m.run(True)['state'], 'planned')
        self.mock_operation.assert_not_called(); self.mock_local_image.assert_not_called()


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
