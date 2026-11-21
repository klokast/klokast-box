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
        return argparse.Namespace(**dict(dict(instance='fixture', resume=None, detach=False, cancel=False), **values))

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
            if 'start' in argv:return {'state': 'complete', 'operation': argv[-1], 'reason': 'update verified'}
            return {'state': 'ready'}
        code, output = self.run_update(self.args(), 'boxa', remote, ['boxa', 'boxb'])
        self.assertEqual(code, 0, output)
        self.assertIn('dom0-preflight', calls[0][1])
        self.assertIn('switchover', calls[1][1])
        self.assertEqual(calls[2][0], 'boxb-ops')
        self.assertEqual(calls[2][1][1:6], ['start', '--box', 'boxa', '--role', 'dom0'])
        lines = [line[9:] for line in output.splitlines()]
        operation = calls[2][1][-1]
        self.assertEqual(lines[:2], ['Checking that no agent session is open on boxa-air.', 'All checks passed.'])
        self.assertIn('Updating boxa-dom0 through boxb-ops; operation ' + operation + '.', lines)
        self.assertIn('Ctrl-C or closing this terminal does not stop the update; it continues on boxb-ops.', lines)
        self.assertIn('Follow it again: kk update boxa-dom0 --resume ' + operation, lines)
        self.assertNotIn('UTC', output)

    def test_busy_runner_prevents_switch_and_update(self):
        with patch.object(client, 'controller', return_value='boxa'), \
                patch.object(client.air, 'declarations', return_value=('tail0.ts.net', ['boxa'])), \
                patch.object(client, 'remote', side_effect=RuntimeError('waiting Claude main:8')) as remote:
            with self.assertRaisesRegex(RuntimeError, 'main:8'), contextlib.redirect_stdout(io.StringIO()):
                client.dom0_update(self.args(), 'boxa')
            self.assertEqual(remote.call_count, 1)

    def test_failed_switch_never_updates(self):
        answers = iter([{'state': 'ready'}, {'state': 'failed'}])
        calls = []
        def remote(user, host, argv, **kwargs):
            calls.append(argv);return next(answers)
        with self.assertRaisesRegex(RuntimeError, 'switch is incomplete'):
            self.run_update(self.args(), 'boxa', remote, ['boxa'])
        self.assertEqual(len(calls), 2)

    def test_standby_needs_no_switch(self):
        answers = iter([{'state': 'ready'}, {'state': 'complete', 'operation': OPERATION}])
        calls = []
        def remote(user, host, argv, **kwargs):
            calls.append(argv);return next(answers)
        code, output = self.run_update(self.args(), 'boxa', remote, ['boxb'])
        self.assertEqual(code, 0, output)
        self.assertEqual(len(calls), 2)

    def test_resume_follows_the_job_without_the_runner_check(self):
        # A reboot can have stopped the runner; the job itself checks it when needed.
        states = iter([{'state': 'updating', 'operation': OPERATION, 'progress': 'waiting for boxa-dom0', 'progress_at': 1},
                       {'state': 'complete', 'operation': OPERATION, 'reason': 'update verified'}])
        calls = []
        def remote(user, host, argv, **kwargs):
            calls.append(argv);return next(states)
        with patch.object(client.time, 'sleep'):
            code, output = self.run_update(self.args(resume=OPERATION), 'boxa', remote, ['boxb'])
        self.assertEqual(code, 0, output)
        self.assertNotIn('dom0-preflight', json.dumps(calls))
        self.assertEqual(calls[0][1:], ['start', '--box', 'boxa', '--role', 'dom0', '--operation', OPERATION])
        self.assertEqual(calls[1][1:], ['status', '--box', 'boxa', '--role', 'dom0', '--operation', OPERATION])
        self.assertIn('complete: update verified', output)

    def test_interrupt_stops_only_the_display(self):
        def remote(user, host, argv, **kwargs):
            if 'start' in argv:return {'state': 'updating', 'operation': OPERATION}
            return {'state': 'ready'}
        with patch.object(client.time, 'sleep', side_effect=KeyboardInterrupt):
            code, output = self.run_update(self.args(), 'boxa', remote, ['boxb'])
        self.assertEqual(code, 130)
        self.assertIn('Stopped following. The update continues on boxb-ops. Follow it again: kk update boxa-dom0 '
                      '--resume ' + OPERATION, output)

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
            with self.assertRaisesRegex(RuntimeError, r'1 attached tmux client\(s\), running work: 7 sleep \(parent 1\)'):
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
                         ['waiting for boxa-dom0 to answer after the reboot: 0 of 2 minutes',
                          'waiting for boxa-dom0 to answer after the reboot: 1 of 2 minutes'])


class Dom0Job(unittest.TestCase):
    def worker(self, result):
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
                                        'checks': [], 'created_at': 1.0}))
            with patch.object(vm_image_update.cycle, 'ROOT', root), \
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
        self.assertEqual((record['state'], record['reason']), ('complete', 'update verified; image ' + 'b' * 24))
        self.assertEqual(record['checks'], ['No other update work is ongoing: OK'])

    def test_worker_records_the_failure(self):
        _calls, record = self.worker(RuntimeError('an agent session or other work is open on boxa-air'))
        self.assertEqual(record['state'], 'failed')
        self.assertIn('open on boxa-air', record['error'])


if __name__ == '__main__':unittest.main()
