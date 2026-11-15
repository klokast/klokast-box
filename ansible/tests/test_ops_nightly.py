"""Nightly decisions must wait for complete replacement and reboot evidence."""
import contextlib
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_infrastructure_guest import load, ROOT


class NightlyFixture(unittest.TestCase):
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

    def scheduled(self, *argv):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        state = Path(directory.name)
        with patch.object(self.m, 'STATE', state), contextlib.redirect_stdout(None), contextlib.redirect_stderr(None):
            code = self.m.main(list(argv))
        return code, state


class NightlyTests(NightlyFixture):
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

    def test_failed_standby_update_reports_failure_and_permits_service_roles(self):
        self.mock_service_run.side_effect = RuntimeError('incomplete VM operation; resume with: X')
        result = self.m.run()
        self.assertEqual((result['state'], result['standby']), ('failed', 'boxb'))
        self.assertIn('resume with: X', result['error'])
        with tempfile.TemporaryDirectory() as state, patch.object(self.m, 'STATE', Path(state)), \
                patch.object(self.m, 'service_runs', return_value=[{'state':'complete'}]) as services, \
                contextlib.redirect_stdout(None):
            self.assertEqual(self.m.main(['--service-role', 'dmz']), 1)
            services.assert_called_once_with(False, roles=['dmz'])
            saved = json.loads((Path(state) / 'current.json').read_text())
        self.assertEqual((saved['state'], saved['services']), ('failed', [{'state':'complete'}]))

    def test_dry_run_and_busy_run_never_replace_the_scheduled_record(self):
        code, state = self.scheduled('--dry-run-plan')
        self.assertEqual((code, list(state.iterdir())), (0, []))
        with patch.object(self.m, 'run_lock', return_value=None):
            code, state = self.scheduled()
        self.assertEqual(code, 75)
        self.assertFalse((state / 'current.json').exists())

    def test_every_stop_records_a_failed_run(self):
        import os, signal
        def die(*args): raise SystemExit(1)
        def term(*args): os.kill(os.getpid(), signal.SIGTERM)
        def bug(*args): raise TypeError('unexpected record')
        previous = signal.getsignal(signal.SIGTERM); self.addCleanup(signal.signal, signal.SIGTERM, previous)
        for action, error in ((die, 'stopped: exit status 1; inspect the run log'),
                              (term, 'stopped: terminated by signal SIGTERM; inspect the run log'),
                              (bug, 'TypeError: unexpected record')):
            with self.subTest(error=error):
                self.mock_placement.side_effect = action
                code, state = self.scheduled()
                saved = json.loads((state / 'current.json').read_text())
                self.assertEqual((code, saved['state'], saved['error']), (1, 'failed', error))
                self.assertIn('finished', saved)
                self.assertIn('Traceback', Path(saved['log']).read_text())

    def test_status_reports_a_run_that_stopped_without_a_record_as_interrupted(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(self.m, 'STATE', Path(directory)):
            self.assertEqual(self.m.status(), {'state': 'none'})
            self.m.inputs.save(Path(directory) / 'current.json', {'state': 'running', 'pid': 2**22 + 7, 'log': 'x'})
            self.assertEqual(self.m.status()['state'], 'interrupted')

    def test_controller_uses_common_dry_plan(self):
        self.m.run(True)
        self.mock_service_run.assert_called_once_with('boxb', 'ops', True, {})


class AlternationTests(NightlyFixture):
    """The controller that the previous run updated becomes active before the update."""
    def setUp(self):
        super().setUp()
        self.previous = {'state': 'complete', 'standby_update': 'replaced', 'active': 'boxa', 'standby': 'boxb',
                         'started': '2026-11-14T03:00:00Z'}
        self.marker = {'role': 'active', 'box': 'boxa', 'updated_at': '2026-11-13T03:10:00Z'}
        self.standby = {'pending': [], 'installed': {'image': 'f' * 24}}
        self.services = type('Services', (), {'step': lambda box, role, action: self.standby})
        for target, name, kwargs in (
                (self.m.replacement, 'command', {'side_effect': lambda argv, **kw: json.dumps(self.marker)}),
                (self.m, 'switch_first', {'return_value': {'state': 'switched', 'active': 'boxb', 'standby': 'boxa',
                                                           'switch': 'c' * 24}})):
            mock = patch.object(target, name, **kwargs); value = mock.start(); self.addCleanup(mock.stop)
            setattr(self, 'mock_' + name, value)
        mock = patch.dict('sys.modules', vm_service_updates=self.services); mock.start(); self.addCleanup(mock.stop)

    def test_passed_standby_update_switches_first_and_runs_no_update(self):
        result = self.m.run(False, self.previous, None, ('--service-role', 'dmz'))
        self.assertEqual((result['state'], result['active']), ('switched', 'boxb'))
        self.mock_switch_first.assert_called_once_with('boxa', 'boxb', ('--service-role', 'dmz'))
        self.mock_service_run.assert_not_called()

    def test_verified_dom0_replacement_also_permits_the_next_night_switch(self):
        previous = dict(self.previous, standby_update='unchanged',
                        dom0={'state': 'complete', 'replaced': True})
        self.assertEqual(self.m.run(False, previous)['state'], 'switched')
        self.mock_service_run.assert_not_called()

    def test_dom0_selection_survives_controller_handoff(self):
        from types import SimpleNamespace
        args = SimpleNamespace(services=False, service_role=['dmz'], dom0=True)
        self.assertEqual(self.m.forwarded(args), ['--service-role', 'dmz', '--dom0'])

    def test_switch_is_not_due_without_fresh_complete_evidence(self):
        for change, reason in (({'standby_update': 'failed'}, 'no new standby image (standby update: failed)'),
                               ({'standby_update': 'unchanged'}, 'no new standby image (standby update: unchanged)'),
                               ({'state': 'none', 'standby_update': None}, 'no new standby image'),
                               ({'standby': 'boxc'}, 'another controller pair'),
                               ({'started': '2026-11-13T03:00:00Z'}, 'authority changed')):
            with self.subTest(change=change):
                result = self.m.run(False, dict(self.previous, **change))
                self.assertEqual(result['alternation']['state'], 'not due')
                self.assertIn(reason, result['alternation']['reason'])
                self.assertEqual(result['standby_update'], 'replaced')
        def unavailable(box, role, action): raise RuntimeError('service step failed: status')
        with patch.object(self.services, 'step', unavailable):
            self.assertIn('unavailable: service step failed', self.m.run(False, self.previous)['alternation']['reason'])
        self.standby['pending'] = [{'operation_id': 'd' * 24}]
        self.assertIn('unfinished', self.m.run(False, self.previous)['alternation']['reason'])
        self.mock_switch_first.assert_not_called()

    def test_run_after_a_switch_and_dry_run_never_switch(self):
        self.assertNotIn('alternation', self.m.run(False, self.previous, 'c' * 24))
        self.assertEqual(self.m.run(False, self.previous, 'c' * 24)['after_switch'], 'c' * 24)
        self.assertEqual(self.m.run(True, self.previous)['alternation']['state'], 'due')
        self.mock_switch_first.assert_not_called()

    def test_abandoned_switch_keeps_the_active_controller_updating(self):
        self.mock_switch_first.return_value = {'state': 'abandoned', 'operation': 'c' * 24, 'error': 'checkout behind'}
        result = self.m.run(False, self.previous)
        self.assertEqual((result['standby'], result['alternation']['state']), ('boxb', 'abandoned'))
        self.mock_service_run.assert_called_once_with('boxb', 'ops', False, {})

    def test_only_a_new_standby_image_permits_tomorrows_switch(self):
        self.mock_service_run.return_value = {'state': 'complete', 'replaced': False}
        self.assertEqual(self.m.run(False, self.previous, 'c' * 24)['standby_update'], 'unchanged')
        self.mock_service_run.side_effect = RuntimeError('probe failed')
        self.assertEqual(self.m.run(False, self.previous, 'c' * 24)['standby_update'], 'failed')
        self.mock_service_run.side_effect = None
        self.mock_service_run.return_value = {'state': 'skipped', 'reason': 'explicit retained data migration required'}
        self.assertEqual(self.m.run(False, self.previous, 'c' * 24)['standby_update'], 'skipped')

    def test_scheduled_run_reads_its_previous_record_and_switched_run_skips_services(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        state = Path(directory.name); self.m.inputs.save(state / 'current.json', self.previous)
        with patch.object(self.m, 'STATE', state), patch.object(self.m, 'service_runs') as services, \
                contextlib.redirect_stdout(None):
            self.assertEqual(self.m.main(['--services']), 0)
        services.assert_not_called()
        self.mock_switch_first.assert_called_once_with('boxa', 'boxb', ['--services'])
        self.assertEqual(json.loads((state / 'current.json').read_text())['state'], 'switched')

    def test_after_switch_run_waits_for_the_local_cron_run_and_records_the_switch(self):
        with patch.object(self.m, 'run_lock', return_value=None) as lock:
            code, state = self.scheduled('--after-switch', 'c' * 24)
        self.assertEqual(code, 75); lock.assert_called_once_with(600)
        code, state = self.scheduled('--after-switch', 'c' * 24)
        saved = json.loads((state / 'current.json').read_text())
        self.assertEqual((code, saved['after_switch'], saved['standby_update']), (0, 'c' * 24, 'replaced'))
        self.mock_switch_first.assert_not_called()
        import io
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as error:
            self.m.main(['--after-switch', 'not-an-operation'])
        self.assertIn('--after-switch needs one switch operation ID', error.getvalue())

    def test_failed_service_roles_keep_the_standby_evidence(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        state = Path(directory.name)
        with patch.object(self.m, 'STATE', state), patch.object(self.m, 'service_runs', side_effect=RuntimeError('dmz')), \
                contextlib.redirect_stdout(None):
            self.assertEqual(self.m.main(['--service-role', 'dmz', '--after-switch', 'c' * 24]), 1)
        saved = json.loads((state / 'current.json').read_text())
        self.assertEqual((saved['state'], saved['standby_update']), ('failed', 'replaced'))


class SwitchFirstTests(unittest.TestCase):
    def setUp(self):
        self.m = load('ops_nightly_switch_test', ROOT / 'ansible/bin/ops-controller-nightly')
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for target, name, value in ((self.m.controller_switch, 'ROOT', self.root),
                                    (self.m.secrets, 'token_hex', lambda size: 'c' * 24)):
            mock = patch.object(target, name, value); mock.start(); self.addCleanup(mock.stop)

    def switch(self, phase=None):
        def switchover(argv, **kwargs):
            self.assertEqual([str(v) for v in argv[1:]], ['switchover', '--old-active', 'boxa', '--new-active', 'boxb',
                                                          '--operation-id', 'c' * 24])
            if phase is None: return ''
            if phase != 'absent':
                (self.root / ('c' * 24 + '.json')).write_text(json.dumps({'phase': phase, 'error': 'cause'}))
            raise RuntimeError('nightly command failed: ops-controller-ha')
        return patch.object(self.m, 'command', side_effect=switchover)

    def test_complete_switch_starts_the_new_active_run_with_the_same_selection(self):
        with self.switch(), patch.object(self.m.replacement, 'command', return_value='') as remote, \
                contextlib.redirect_stdout(None):
            result = self.m.switch_first('boxa', 'boxb', ['--service-role', 'dmz'])
        self.assertEqual((result['state'], result['active'], result['standby']), ('switched', 'boxb', 'boxa'))
        self.assertEqual(remote.call_args.args[0], ['tailscale', 'ssh', 'smith@boxb-ops', 'sh', '-s', '--',
                                                    '--service-role', 'dmz', '--after-switch', 'c' * 24])
        self.assertIn('start_new_session=True', remote.call_args.kwargs['stdin'])

    def test_abandoned_or_unstarted_switch_keeps_the_old_active(self):
        for phase in ('abandoned', 'absent'):
            with self.subTest(phase=phase), self.switch(phase), patch.object(self.m.replacement, 'command') as remote, \
                    contextlib.redirect_stdout(None), contextlib.redirect_stderr(None):
                self.assertEqual(self.m.switch_first('boxa', 'boxb', [])['state'], 'abandoned')
            remote.assert_not_called()

    def test_partial_switch_or_missing_handoff_stops_with_the_exact_command(self):
        with self.switch('demoted'), contextlib.redirect_stdout(None), \
                self.assertRaisesRegex(RuntimeError, 'phase demoted; resume it on boxa-ops with: .*--resume ' + 'c' * 24):
            self.m.switch_first('boxa', 'boxb', [])
        failure = self.m.subprocess.CalledProcessError(255, 'tailscale')
        with self.switch(), patch.object(self.m.replacement, 'command', side_effect=failure), contextlib.redirect_stdout(None), \
                self.assertRaisesRegex(RuntimeError, 'made boxb-ops active.*ops-controller-nightly --services'):
            self.m.switch_first('boxa', 'boxb', ['--services'])

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

    def test_source_preflight_updates_or_stops_before_image_work(self):
        import infrastructure_images as images
        import subprocess
        from types import SimpleNamespace
        with patch.object(images.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='{}', stderr='')) as remote:
            images.local_action('boxa', images.PROFILES[0], 'a'*40, 'prepare')
            script=remote.call_args.kwargs['input'].split('work=$(mktemp',1)[0]
        for condition in ('behind','dirty','diverged','unreachable'):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as d:
                root=Path(d); origin=root/'origin'; source=root/'source'
                def git(*args, cwd=origin):
                    return subprocess.check_output(['git',*args],cwd=cwd,text=True,stderr=subprocess.DEVNULL).strip()
                origin.mkdir();git('init','-q','-b','main')
                git('config','user.email','test@example.invalid');git('config','user.name','Test')
                (origin/'file').write_text('base');git('add','.');git('commit','-qm','base')
                git('clone','-q',str(origin),str(source))
                (origin/'file').write_text('next');git('commit','-qam','next');revision=git('rev-parse','HEAD')
                # Upstream main is already after the frozen revision.
                (origin/'file').write_text('later');git('commit','-qam','later')
                if condition=='dirty':(source/'file').write_text('local')
                if condition=='diverged':
                    git('config','user.email','test@example.invalid',cwd=source)
                    git('config','user.name','Test',cwd=source)
                    (source/'other').write_text('local');git('add','.',cwd=source);git('commit','-qm','local',cwd=source)
                if condition=='unreachable':git('remote','set-url','origin',str(root/'absent'),cwd=source)
                prefix=script.replace('/home/smith/src/klokast/klokast-box',str(source)).replace(
                    '/usr/local/sbin/klokast-controller-guard --require-local-image-box "$box" >/dev/null','true')
                result=subprocess.run(['sh','-s','--','boxa',revision,'prepare','-',images.PROFILES[0]],
                                      input=prefix,capture_output=True,text=True)
                if condition=='behind':
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(git('rev-parse','HEAD',cwd=source),revision)
                    # The tracking ref is current: no published commit shows as unpublished.
                    self.assertEqual(git('status','--short','--branch',cwd=source),'## main...origin/main [behind 1]')
                else:
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn('Controller boxa-ops',result.stderr)

    def test_prepare_publishes_each_build_stage_while_it_runs(self):
        import infrastructure_images as images
        import os
        script = '''
echo 'platform-update: check local image prerequisites on boxa; operation abc' >&2
read -r line </dev/stdin || :
echo 'platform-update: build the candidate in a networkless Xen guest on boxa' >&2
echo '{"state":"candidate-built"}'
'''
        seen = []
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, KLOKAST_PROGRESS=str(Path(d)/'progress.json')), \
                patch.object(images, 'progress', side_effect=seen.append):
            result = images.streamed(['sh', '-c', script], 'input\n', 30)
        self.assertEqual((result.returncode, json.loads(result.stdout)), (0, {'state': 'candidate-built'}))
        self.assertEqual(seen, ['image build: check local image prerequisites on boxa',
                                'image build: build the candidate in a networkless Xen guest on boxa'])
        self.assertIn('networkless Xen guest', result.stderr)

    def test_progress_is_silent_without_a_job_file(self):
        import os
        import platform_maintenance
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, KLOKAST_PROGRESS=str(Path(d)/'progress.json')):
            platform_maintenance.progress('checking the image receipt and checksums')
            self.assertEqual(json.loads((Path(d)/'progress.json').read_text())['progress'], 'checking the image receipt and checksums')
        with patch.dict(os.environ), patch.object(platform_maintenance, 'write') as write:
            os.environ.pop('KLOKAST_PROGRESS', None)
            platform_maintenance.progress('unused')
        write.assert_not_called()
        with patch.dict(os.environ, KLOKAST_PROGRESS='/fixture/absent/progress.json'), \
                patch.object(platform_maintenance, 'write', side_effect=PermissionError('denied')):
            platform_maintenance.progress('a display failure does not stop the update')

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
