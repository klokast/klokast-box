"""Manual dom0 dispatch with closed, mocked controller transport."""
import argparse
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


class ManualDom0(unittest.TestCase):
    def args(self):
        return argparse.Namespace(instance='fixture', resume=None, detach=False, cancel=False)

    def test_switch_then_update_on_verified_peer(self):
        calls = []
        def remote(user, host, argv, **kwargs):
            calls.append((host, argv))
            if 'switchover' in argv:return {'state': 'complete', 'active': 'boxb'}
            return {'state': 'complete'}
        with patch.object(client, 'controller', side_effect=['boxa', 'boxb']), \
                patch.object(client, 'declared_controllers', return_value=['boxa', 'boxb']), \
                patch.object(client, 'remote', side_effect=remote):
            self.assertEqual(client.dom0_update(self.args(), 'boxa'), 0)
        self.assertIn('dom0-preflight', calls[0][1])
        self.assertIn('switchover', calls[1][1])
        self.assertEqual(calls[2][0], 'boxb-ops')
        self.assertIn('--manual-dom0', calls[2][1])

    def test_busy_runner_prevents_switch_and_update(self):
        with patch.object(client, 'controller', return_value='boxa'), \
                patch.object(client, 'remote', side_effect=RuntimeError('waiting Claude main:8')) as remote:
            with self.assertRaisesRegex(RuntimeError, 'main:8'):
                client.dom0_update(self.args(), 'boxa')
            self.assertEqual(remote.call_count, 1)

    def test_failed_switch_never_updates(self):
        with patch.object(client, 'controller', return_value='boxa'), \
                patch.object(client, 'declared_controllers', return_value=['boxa', 'boxb']), \
                patch.object(client, 'remote', side_effect=[{'state':'ready'}, {'state':'failed'}]) as remote:
            with self.assertRaisesRegex(RuntimeError, 'switch is incomplete'):
                client.dom0_update(self.args(), 'boxa')
            self.assertEqual(remote.call_count, 2)

    def test_standby_needs_no_switch(self):
        with patch.object(client, 'controller', return_value='boxb'), \
                patch.object(client, 'remote', side_effect=[{'state':'ready'}, {'state':'unchanged'}]) as remote:
            self.assertEqual(client.dom0_update(self.args(), 'boxa'), 0)
            self.assertEqual(remote.call_count, 2)

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

    def test_both_runners_and_reservations_are_checked(self):
        view = {'instance': {'boxes': {'boxa':{}, 'boxb':{}},
                             'airunners':['boxa-air','boxb-air']},
                'controllers': {'active':'boxa','standby':'boxb'}}
        ready = {'idle':True, 'available':True}
        with patch.object(dom0_updates.air_maintenance, 'remote', side_effect=[ready, dict(ready,
                reservation={'operation':'other'}, sessions=[{'name':'main:8'}])]):
            with self.assertRaisesRegex(RuntimeError, 'boxb-air.*main:8'):
                dom0_updates.preflight('boxb', view=view)
        own = dict(ready, pending=True, maintenance=True, reservation={'operation':'a'*24})
        with patch.object(dom0_updates.air_maintenance, 'remote', side_effect=[ready, own]):
            dom0_updates.preflight('boxb', view=view, operation='a'*24)
        with patch.object(dom0_updates.air_maintenance, 'remote', return_value=dict(ready,
                idle=False, reason='waiting Codex discussion abc')):
            with self.assertRaisesRegex(RuntimeError, 'discussion abc'):
                dom0_updates.preflight('boxb', view=view)


if __name__ == '__main__':unittest.main()
