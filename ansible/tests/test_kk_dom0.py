"""Manual dom0 dispatch with closed, mocked controller transport."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'klokast-dev/lib'))
sys.path.insert(0, str(ROOT / 'ansible/lib'))
import platform_client as client
import dom0_updates
import vm_image_update

OPERATION = 'a' * 24


class ManualDom0(unittest.TestCase):
    def args(self, **values):
        return argparse.Namespace(**dict(dict(instance='fixture', detach=False, cancel=False), **values))

    def run_update(self, args, box, remote, active):
        output = io.StringIO()
        with patch.object(client, 'controller', side_effect=active), \
                patch.object(client, 'declared_controllers', return_value=['boxa', 'boxb']), \
                patch.object(client.air, 'declarations', return_value=('tail0.ts.net', ['boxa', 'boxb'])), \
                patch.object(client, 'remote', side_effect=remote), contextlib.redirect_stdout(output):
            code = client.dom0_update(args, box)
        return code, output.getvalue()

    def test_switch_then_start_the_job_on_the_verified_peer(self):
        calls = []
        def remote(user, host, argv, **kwargs):
            calls.append((host, argv))
            if 'switchover' in argv:return {'state': 'complete', 'active': 'boxb'}
            if 'start' in argv:return {'state': 'complete', 'operation': OPERATION, 'request': 'new', 'reason': 'update verified'}
            if 'status' in argv:return {'state': 'complete', 'operation': 'b' * 24}
            return {'state': 'ready'}
        code, output = self.run_update(self.args(), 'boxa', remote, ['boxa', 'boxb'])
        self.assertEqual(code, 0, output)
        self.assertEqual(calls[0][1][1:], ['status', '--box', 'boxa', '--role', 'dom0'])
        self.assertIn('dom0-preflight', calls[1][1])
        self.assertIn('switchover', calls[2][1])
        self.assertEqual(calls[3][0], 'boxb-ops')
        self.assertEqual(calls[3][1][1:], ['start', '--box', 'boxa', '--role', 'dom0'])
        lines = [line[9:] for line in output.splitlines()]
        self.assertEqual(lines[:2], ['Checking that no agent session is open on boxa-air.', 'All checks passed.'])
        self.assertIn('Updating boxa-dom0; job ' + OPERATION + ' on boxb-ops. Ctrl-C or closing this '
                      'terminal does not stop the update. Follow it again: kk update boxa-dom0', lines)
        self.assertNotIn('UTC', output)

    def test_reboot_uses_the_same_checks_and_requests_a_reboot_job(self):
        calls = []
        def remote(user, host, argv, **kwargs):
            calls.append((host, argv))
            if 'start' in argv:return {'state': 'complete', 'operation': OPERATION, 'request': 'new', 'reason': 'reboot verified'}
            # A failed update job does not make the reboot skip its checks.
            if 'status' in argv:return {'state': 'failed', 'operation': 'b' * 24}
            return {'state': 'ready'}
        output = io.StringIO()
        with patch.object(client, 'controller', return_value='boxb'), \
                patch.object(client, 'declared_boxes', return_value=['boxa', 'boxb']), \
                patch.object(client.air, 'declarations', return_value=('tail0.ts.net', ['boxa', 'boxb'])), \
                patch.object(client, 'remote', side_effect=remote), contextlib.redirect_stdout(output):
            code = client.main(['--instance', 'fixture', 'reboot', 'boxa-dom0'])
        self.assertEqual(code, 0, output.getvalue())
        self.assertIn('dom0-preflight', calls[1][1])
        self.assertEqual(calls[2][1][1:], ['start', '--box', 'boxa', '--role', 'dom0', '--reboot'])
        lines = [line[9:] for line in output.getvalue().splitlines()]
        self.assertIn('Rebooting boxa-dom0; job ' + OPERATION + ' on boxb-ops. Ctrl-C or closing this '
                      'terminal does not stop the reboot. Follow it again: kk reboot boxa-dom0', lines)

    def test_reboot_refuses_a_vm_target(self):
        error = io.StringIO()
        with patch.object(client, 'declared_boxes', return_value=['boxa']), contextlib.redirect_stderr(error), \
                patch.object(client, 'remote') as remote:
            code = client.main(['--instance', 'fixture', 'reboot', 'boxa-dmz'])
        self.assertNotEqual(code, 0)
        self.assertIn('kk reboot accepts only a BOX-dom0 target', error.getvalue())
        remote.assert_not_called()

    def test_busy_runner_prevents_switch_and_update(self):
        with patch.object(client, 'controller', return_value='boxa'), \
                patch.object(client.air, 'declarations', return_value=('tail0.ts.net', ['boxa'])), \
                patch.object(client, 'remote', side_effect=[{'state': 'absent'}, RuntimeError('waiting Claude main:8')]) as remote:
            with self.assertRaisesRegex(RuntimeError, 'main:8'), contextlib.redirect_stdout(io.StringIO()):
                client.dom0_update(self.args(), 'boxa')
            self.assertEqual(remote.call_count, 2)

    def test_failed_switch_never_updates(self):
        answers = iter([{'state': 'absent'}, {'state': 'ready'}, {'state': 'failed'}])
        calls = []
        def remote(user, host, argv, **kwargs):
            calls.append(argv);return next(answers)
        with self.assertRaisesRegex(RuntimeError, 'switch is incomplete'):
            self.run_update(self.args(), 'boxa', remote, ['boxa'])
        self.assertEqual(len(calls), 3)

    def test_standby_needs_no_switch(self):
        answers = iter([{'state': 'absent'}, {'state': 'ready'}, {'state': 'complete', 'operation': OPERATION}])
        calls = []
        def remote(user, host, argv, **kwargs):
            calls.append(argv);return next(answers)
        code, output = self.run_update(self.args(), 'boxa', remote, ['boxb'])
        self.assertEqual(code, 0, output)
        self.assertEqual(len(calls), 3)

    def test_repeated_request_restarts_the_failed_job_without_the_runner_check(self):
        # A reboot can have stopped the runner; the job itself checks it when needed.
        states = iter([{'state': 'failed', 'operation': OPERATION},
                       {'state': 'updating', 'operation': OPERATION, 'progress': 'waiting for boxa-dom0', 'progress_at': 1,
                        'request': 'restarted', 'previous': 'failed'},
                       {'state': 'complete', 'operation': OPERATION, 'reason': 'update of boxa-dom0 is complete',
                        'result': {'image': 'd' * 24}}])
        calls = []
        def remote(user, host, argv, **kwargs):
            calls.append(argv);return next(states)
        with patch.object(client.time, 'sleep'):
            code, output = self.run_update(self.args(), 'boxa', remote, ['boxb'])
        self.assertEqual(code, 0, output)
        self.assertNotIn('dom0-preflight', json.dumps(calls))
        self.assertEqual(calls[1][1:], ['start', '--box', 'boxa', '--role', 'dom0'])
        self.assertEqual(calls[2][1:], ['status', '--box', 'boxa', '--role', 'dom0', '--operation', OPERATION])
        self.assertIn(' Restarting the failed update job ' + OPERATION + ' of boxa-dom0 on boxb-ops. ', output)
        self.assertIn(' Waiting for boxa-dom0\n', output)
        # The image shows once, after the stage of the job.
        self.assertIn(' Update of boxa-dom0 is complete; image ' + 'd' * 24 + '\n', output)

    def test_interrupt_stops_only_the_display(self):
        def remote(user, host, argv, **kwargs):
            if 'start' in argv:return {'state': 'updating', 'operation': OPERATION}
            return {'state': 'ready'}
        with patch.object(client.time, 'sleep', side_effect=KeyboardInterrupt):
            code, output = self.run_update(self.args(), 'boxa', remote, ['boxb'])
        self.assertEqual(code, 130)
        self.assertIn('Stopped following. The update continues on boxb-ops. Check it: kk status boxa-dom0', output)

    def test_activity_after_selection_prevents_reboot(self):
        view = {'instance': {'airunners': []}, 'controllers': {'active': 'boxb'}}
        actions = []
        def step(box, operation, action, directory, *args):
            actions.append(action)
            if action == 'status':return {'pending': {'operation': 'a'*24, 'state': 'selected'}}
            return {'state': 'selected'}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dom0_updates, 'STATE', Path(directory)), \
                patch.object(dom0_updates, 'authority', return_value=view), \
                patch.object(dom0_updates, 'step', side_effect=step), \
                patch.object(dom0_updates, 'preflight', side_effect=[{'state':'ready'}, RuntimeError('new attached session')]):
            with self.assertRaisesRegex(RuntimeError, 'new attached session'):
                dom0_updates.execute('boxa', resume='a'*24, manual=True, lock_held=True)
        self.assertEqual(actions, ['status', 'select'])

    def test_only_the_target_runner_and_its_reservation_are_checked(self):
        view = {'instance': {'boxes': {'boxa':{}, 'boxb':{}},
                             'airunners':['boxa-air','boxb-air']},
                'controllers': {'active':'boxa','standby':'boxb'}}
        ready = {'idle':True, 'available':True}
        # The reboot of boxb does not stop boxa-air, so its work does not matter.
        with patch.object(dom0_updates.air_maintenance, 'remote', return_value=ready) as remote:
            dom0_updates.preflight('boxb', view=view)
        remote.assert_called_once_with('boxb', 'image-idle')
        with patch.object(dom0_updates.air_maintenance, 'remote', return_value=dict(ready,
                reservation={'operation':'other'})):
            with self.assertRaisesRegex(RuntimeError, 'open on boxb-air, which the boxb-dom0 reboot stops: '
                                        'maintenance operation other is not finished'):
                dom0_updates.preflight('boxb', view=view)
        own = dict(ready, pending=True, maintenance=True, reservation={'operation':'a'*24})
        with patch.object(dom0_updates.air_maintenance, 'remote', return_value=own):
            dom0_updates.preflight('boxb', view=view, operation='a'*24)
        with patch.object(dom0_updates.air_maintenance, 'remote', return_value=dict(ready,
                idle=False, reason='waiting Codex discussion abc')):
            with self.assertRaisesRegex(RuntimeError, 'discussion abc. Close it, then run the command again.'):
                dom0_updates.preflight('boxb', view=view)
        with patch.object(dom0_updates.air_maintenance, 'remote', return_value=dict(ready,
                idle=False, attached=1, busy_pids=[7], busy_work=['7 sleep (parent 1)'])):
            with self.assertRaisesRegex(RuntimeError, r'stops: running work: 7 sleep \(parent 1\)\. Close it'):
                dom0_updates.preflight('boxb', view=view)
        # Only a runner image older than the client detach waits for attached clients.
        with patch.object(dom0_updates.air_maintenance, 'remote', return_value=dict(ready, idle=False, attached=1)):
            with self.assertRaisesRegex(RuntimeError, r'stops: 1 attached tmux client\(s\)\. Close it'):
                dom0_updates.preflight('boxb', view=view)
        view['instance']['airunners'] = ['boxa-air']
        with patch.object(dom0_updates.air_maintenance, 'remote') as remote:
            self.assertEqual(dom0_updates.preflight('boxb', view=view)['state'], 'ready')
        remote.assert_not_called()

    def test_unanswered_reboot_names_the_bound_the_cause_and_the_log(self):
        view = {'instance': {'airunners': []}, 'controllers': {'active': 'boxb'}}
        def step(box, operation, action, directory, *args):
            if action == 'status':return {'pending': {'operation': OPERATION, 'state': 'rebooting'}}
            if action == 'verify':raise dom0_updates.UpdateError('ansible-playbook 74-dom0-update.yml failed with exit status 4')
            return {'state': 'started'}
        published = []
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dom0_updates, 'STATE', Path(directory)), \
                patch.object(dom0_updates, 'VERIFY_SECONDS', 120), \
                patch.object(dom0_updates, 'authority', return_value=view), \
                patch.object(dom0_updates, 'step', side_effect=step), \
                patch.object(dom0_updates, 'progress', side_effect=published.append), \
                patch.object(dom0_updates.time, 'sleep'), \
                patch.object(dom0_updates.time, 'monotonic', side_effect=[0, 0, 0, 70, 70, 130]):
            with self.assertRaises(RuntimeError) as failure:
                dom0_updates.execute('boxa', resume=OPERATION, manual=True, lock_held=True)
        self.assertIn('boxa-dom0 did not pass the verification within 2 minutes after the reboot '
                      '(last cause: no SSH answer through Tailscale)', str(failure.exception))
        self.assertIn('Resume operation ' + OPERATION, str(failure.exception))
        self.assertIn(OPERATION + '/verify.log', str(failure.exception))
        self.assertEqual([p for p in published if p.startswith('waiting')],
                         ['waiting for boxa-dom0 to pass the verification after the reboot: 0 of 2 minutes',
                          'waiting for boxa-dom0 to pass the verification after the reboot: 1 of 2 minutes; '
                          'last cause: no SSH answer through Tailscale'])

    def test_reboot_closes_the_runner_right_after_its_check_and_opens_it_after_a_failed_health_check(self):
        view = {'instance': {'airunners': ['boxa-air']}, 'controllers': {'active': 'boxb'}}
        events = []
        def step(box, operation, action, directory, *args):
            events.append(action);return {}
        def health(box, directory, label):
            events.append('health');raise RuntimeError('dom0 health check failed')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dom0_updates, 'STATE', Path(directory)), \
                patch.object(dom0_updates, 'authority', return_value=view), \
                patch.object(dom0_updates, 'step', side_effect=step), \
                patch.object(dom0_updates, 'health', side_effect=health), \
                patch.object(dom0_updates, 'progress', side_effect=lambda text: events.append(text)), \
                patch.object(dom0_updates, 'preflight', side_effect=lambda *a, **k: events.append('preflight')), \
                patch.object(dom0_updates.air_maintenance, 'before_stop', side_effect=lambda *a, **k: events.append('close')), \
                patch.object(dom0_updates.air_maintenance, 'release', side_effect=lambda *a: events.append('open')):
            with self.assertRaisesRegex(RuntimeError, 'dom0 health check failed'):
                dom0_updates.execute('boxa', operation=OPERATION, manual=True, lock_held=True, verify_reboot=True)
        self.assertEqual(events, ['reading the update state of boxa-dom0', 'status',
                                  'checking that no agent session is open on boxa-air', 'preflight',
                                  'saving the boxa-air workspace and closing it to new sessions', 'close',
                                  'checking the health of boxa-dom0 before the reboot', 'health', 'open'])

    def test_resume_after_the_accept_restores_the_runner_and_names_the_image(self):
        view = {'instance': {'airunners': ['boxa-air']}, 'controllers': {'active': 'boxb'}}
        events = []
        def step(box, operation, action, directory, *args):
            events.append(action)
            return {'pending': None, 'accepted': {'operation': OPERATION, 'image': 'b' * 24, 'reboot_test': True}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dom0_updates, 'STATE', Path(directory)), \
                patch.object(dom0_updates, 'authority', return_value=view), \
                patch.object(dom0_updates, 'step', side_effect=step), \
                patch.object(dom0_updates, 'progress', side_effect=events.append), \
                patch.object(dom0_updates.air_maintenance, 'release', side_effect=lambda *a: events.append('open')):
            result = dom0_updates.execute('boxa', operation=OPERATION, manual=True, lock_held=True, verify_reboot=True)
        self.assertEqual(events, ['reading the update state of boxa-dom0', 'status',
                                  'boxa-dom0 passed and recorded the verified reboot in an earlier run; '
                                  'its VMs do not stop again',
                                  'restoring the boxa-air workspace', 'open'])
        self.assertEqual((result['state'], result['image'], result['replaced'], result['reboot_verified']),
                         ('complete', 'b' * 24, False, True))


class Dom0Job(unittest.TestCase):
    def worker(self, result, reboot=False):
        calls = []
        def execute(box, **kwargs):
            calls.append((box, kwargs))
            if isinstance(result, Exception):raise result
            return result
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / OPERATION).mkdir()
            path = root / OPERATION / 'image-job.json'
            path.write_text(json.dumps({'operation': OPERATION, 'box': 'boxa', 'role': 'dom0', 'state': 'waiting',
                                        'checks': [], 'created_at': 1.0, **({'reboot': True} if reboot else {})}))
            # The worker arms a real job alarm; it must not fire in a later test.
            with patch.object(vm_image_update.cycle, 'ROOT', root), \
                    patch('signal.signal'), patch('signal.alarm'), \
                    patch.object(vm_image_update.cycle.source, 'require_controller'), \
                    patch.object(vm_image_update.cycle.runtime, 'vm_update_installation_lock',
                                 return_value=contextlib.nullcontext()), \
                    patch.object(vm_image_update, 'write', side_effect=lambda p, v: p.write_text(json.dumps(v))), \
                    patch.object(dom0_updates, 'execute', side_effect=execute), \
                    patch.dict('os.environ', {}, clear=False):
                try:vm_image_update.worker('boxa', OPERATION, 'dom0')
                except RuntimeError:pass
            return calls, json.loads(path.read_text())

    def test_worker_runs_the_recorded_dom0_transaction(self):
        calls, record = self.worker({'state': 'complete', 'image': 'b' * 24, 'operation': OPERATION})
        self.assertEqual(calls, [('boxa', {'operation': OPERATION, 'manual': True, 'lock_held': True})])
        self.assertEqual((record['state'], record['reason']), ('complete', 'update of boxa-dom0 is complete'))
        self.assertEqual(record['checks'], ['No other update work is ongoing: OK'])

    def test_reboot_job_runs_the_reboot_test(self):
        calls, record = self.worker({'state': 'complete', 'image': 'b' * 24, 'replaced': False}, reboot=True)
        self.assertEqual(calls, [('boxa', {'operation': OPERATION, 'manual': True, 'lock_held': True,
                                           'verify_reboot': True})])
        self.assertEqual((record['state'], record['reason']), ('complete', 'reboot of boxa-dom0 is complete'))

    def test_reboot_job_shows_each_warning_as_its_own_event(self):
        record = {'state': 'complete', 'reason': 'reboot of boxa-dom0 is complete', 'changed_at': 1.0,
                  'result': {'image': 'b' * 24, 'warnings': ['The job stopped the VM old on boxa-dom0, which the '
                                                             'Platform does not declare.']}}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            client.show_stage(record, client.describe(record, 'boxa-dom0'))
        lines = output.getvalue().splitlines()
        self.assertEqual([line.split(' ', 2)[-1] for line in lines],
                         ['Reboot of boxa-dom0 is complete; image ' + 'b' * 24,
                          'Warning: The job stopped the VM old on boxa-dom0, which the Platform does not declare.'])
        for line in lines:self.assertRegex(line, r'^1970-01-01 00:00:01 ')

    def test_complete_job_without_an_image_names_no_image(self):
        record = {'state': 'complete', 'reason': 'reboot of boxa-dom0 is complete', 'result': {'state': 'complete'}}
        self.assertEqual(client.describe(record, 'boxa-dom0'), 'Reboot of boxa-dom0 is complete')

    def test_status_points_out_undeclared_vms_and_containers(self):
        report = {'machines': [{'target': 'boxa-dom0', 'role': 'dom0', 'state': 'reachable', 'updated_at': 1,
                                'alpine': '3.24.4', 'packages': 3}],
                  'drift': {'vms': [{'box': 'boxa', 'guest': 'old'}],
                            'containers': [{'target': 'boxa-dmz', 'container': 'web-proxy'}]}}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            issues = client.show_machines(report) + client.show_containers(report['drift'])
        self.assertEqual(issues, ['boxa-dom0 runs the VM old, which the Platform does not declare.',
                                  'boxa-dmz runs the container web-proxy, which no declared application lists.'])
        for issue in issues:self.assertIn(issue, output.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(client.show_containers({'vms': [], 'containers': []}), [])
            self.assertEqual(client.show_containers(None), [])
        self.assertIn('Each running container belongs to a declared application.', output.getvalue())
        self.assertIn('not compared with the declaration', output.getvalue())

    def test_a_request_never_turns_an_update_job_into_a_reboot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / OPERATION).mkdir()
            path = root / OPERATION / 'image-job.json'
            path.write_text(json.dumps(
                {'operation': OPERATION, 'box': 'boxa', 'role': 'dom0', 'state': 'waiting', 'checks': [], 'created_at': 1.0}))
            with patch.object(vm_image_update.cycle, 'ROOT', root), \
                    patch.object(vm_image_update.cycle.source, 'require_controller'), \
                    patch.object(vm_image_update.inputs, 'update_checkout'), \
                    patch.object(dom0_updates, 'authority'), \
                    patch.object(vm_image_update.subprocess, 'Popen') as start:
                with self.assertRaisesRegex(RuntimeError, 'running update job of boxa-dom0; follow it with kk update boxa-dom0$'):
                    vm_image_update.start('boxa', 'dom0', reboot=True)
                start.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, 'only a dom0 job'):
                    vm_image_update.start('boxa', 'dmz', reboot=True)
                # A failed update job stays failed; the reboot is a new job.
                path.write_text(json.dumps(dict(json.loads(path.read_text()), state='failed')))
                record = vm_image_update.start('boxa', 'dom0', reboot=True)
                self.assertEqual((record['request'], record['reboot']), ('new', True))
                self.assertNotEqual(record['operation'], OPERATION)
                self.assertEqual(json.loads(path.read_text())['state'], 'failed')

    def test_no_active_controller_names_the_result_of_each_controller(self):
        def remote(_user, host, _argv, **_kwargs):
            if host == 'boxa-ops':
                raise RuntimeError('boxa-ops: controller is not active: hostname=boxa-ops role=standby active=false active_box=boxb')
            raise RuntimeError('boxb-ops: @@@@\nWARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\nHost key verification failed.')
        with patch.object(client, 'declared_controllers', return_value=['boxa', 'boxb']), \
                patch.object(client, 'remote', side_effect=remote):
            with self.assertRaises(RuntimeError) as caught:client.controller('fixture')
        message = str(caught.exception)
        self.assertIn('no active controller', message)
        self.assertIn('\n  boxa-ops: controller is not active: hostname=boxa-ops role=standby', message)
        self.assertIn('\n  boxb-ops: Host key verification failed.', message)
        self.assertIn('ssh smith@BOX-ops true', message)

    def test_one_active_controller_is_selected(self):
        def remote(_user, host, _argv, **_kwargs):
            if host == 'boxa-ops':raise client.subprocess.TimeoutExpired('ssh', 90)
            return {'active': True, 'role': 'active', 'hostname': 'boxb-ops'}
        with patch.object(client, 'declared_controllers', return_value=['boxa', 'boxb']), \
                patch.object(client, 'remote', side_effect=remote):
            self.assertEqual(client.controller('fixture'), 'boxb')

    def test_two_active_controllers_stop_the_command(self):
        with patch.object(client, 'declared_controllers', return_value=['boxa', 'boxb']), \
                patch.object(client, 'remote', side_effect=lambda _u, host, _a, **_k:
                             {'active': True, 'role': 'active', 'hostname': host}):
            with self.assertRaisesRegex(RuntimeError, 'more than one controller reports that it is active'):
                client.controller('fixture')

    def test_worker_records_the_failure(self):
        _calls, record = self.worker(RuntimeError('an agent session or other work is open on boxa-air'))
        self.assertEqual(record['state'], 'failed')
        self.assertIn('open on boxa-air', record['error'])


if __name__ == '__main__':unittest.main()
