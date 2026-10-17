import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


air = load('client_air', ROOT / 'klokast-dev/lib/air.py')
workspace = load('workspace_air', ROOT / 'ansible/lib/air_workspace.py')


class SelectionTest(unittest.TestCase):
    def status(self, **kwargs):
        return dict(available=True, workspace=False, maintenance=False, pending=False, **kwargs)

    def choose(self, values, last=None, explicit=None):
        return air.choose(['k001', 'k002'], ['k002', 'k001'], last, explicit, values.__getitem__)

    def test_defaults_and_fallback(self):
        values = {'k001': self.status(), 'k002': self.status()}
        self.assertEqual(self.choose(values), ('k002', False))
        values['k002']['available'] = False
        self.assertEqual(self.choose(values), ('k001', False))
        with self.assertRaisesRegex(ValueError, 'no usable runner'):
            self.choose(values, explicit='k002')

    def test_remembered_workspace_and_maintenance(self):
        values = {'k001': self.status(), 'k002': self.status()}
        values['k001'].update(workspace=True, maintenance=True)
        self.assertEqual(self.choose(values, last='k001'), ('k001', True))
        self.assertEqual(self.choose(values), ('k002', False))
        values['k001']['pending'] = True
        self.assertEqual(self.choose(values, last='k001'), ('k002', False))

    def test_missing_remembered_workspace_uses_preference(self):
        self.assertEqual(self.choose({'k001': self.status(), 'k002': self.status()}, last='k001'), ('k002', False))

    def test_explicit_requires_declaration(self):
        with self.assertRaisesRegex(ValueError, 'not declared'):
            self.choose({}, explicit='k003')

    def test_config_scopes_device_and_tailnet_and_clears_selection(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, XDG_CONFIG_HOME=temporary):
            with air.settings('first.ts.net') as value:
                value.update(prefer=['k001', 'k002'], last='k001')
            with air.settings('second.ts.net') as value:
                self.assertEqual(value, {'prefer': ['k002', 'k001'], 'last': None})
            with air.settings('first.ts.net') as value:
                self.assertEqual(value['last'], 'k001')
            instance = Path(temporary) / 'instance'
            instance.mkdir()
            (instance / 'klokast-instance.json').write_text(json.dumps({'schema-version': 1,
                'tailscale': {'tailnet-dns-name': 'first.ts.net'}, 'boxes': {'k001': {}, 'k002': {}},
                'airunners': ['k002-air', 'k001-air']}))
            self.assertEqual(air.main(['--instance', str(instance), '--prefer', 'k002,k001']), 0)
            with air.settings('first.ts.net') as value:
                self.assertIsNone(value['last'])
            self.assertEqual(air.config_path().stat().st_mode & 0o777, 0o600)

    def test_mosh_preserves_ssh_configuration_and_clients(self):
        with patch.object(air.shutil, 'which', return_value='/usr/bin/mosh'), patch.object(air.subprocess, 'run') as run:
            run.return_value.returncode = 0
            self.assertEqual(air.attach('k001', True), 0)
            args = run.call_args.args[0]
            self.assertIn('agent@k001-air', args)
            self.assertIn('--reconnect', args)
            self.assertNotIn('-d', args)
            self.assertFalse(any('ProxyCommand' in value for value in args))


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        state = patch.object(workspace, 'STATE', self.directory)
        state.start(); self.addCleanup(state.stop)

    def test_detached_work_blocks_but_idle_shells_and_probe_do_not(self):
        rows = {1: dict(parent=0, owned=False, name='init', argv=['init']),
                10: dict(parent=1, owned=True, name='tmux: server', argv=['tmux']),
                11: dict(parent=10, owned=True, name='bash', argv=['-bash']),
                12: dict(parent=11, owned=True, name='python3', argv=['python3', 'long-job.py']),
                13: dict(parent=1, owned=True, name='bash', argv=['bash', '-c', 'sleep 90']),
                20: dict(parent=1, owned=True, name='sshd', argv=['sshd']),
                21: dict(parent=20, owned=True, name='python3', argv=['python3', 'probe'])}
        self.assertEqual(workspace.classify(rows, 21), [12, 13])

    def test_hold_starts_after_activity_and_expires(self):
        idle = dict(attached=0, busy_pids=[], sessions=[])
        self.assertFalse(workspace.idle_check(idle, now=100)['idle'])
        self.assertTrue(workspace.idle_check(idle, now=1900)['idle'])
        busy = dict(idle, attached=1)
        self.assertFalse(workspace.idle_check(busy, now=2000)['idle'])
        self.assertFalse(workspace.idle_check(idle, now=4000)['idle'])
        self.assertTrue(workspace.idle_check(idle, now=5800)['idle'])
        idle['sessions'] = [{'activity': 5801}]
        self.assertFalse(workspace.idle_check(idle, now=5802)['idle'])

    def test_idle_daemon_does_not_block_but_loaded_discussions_and_other_commands_do(self):
        rows={10:dict(parent=1,owned=True,name='codex',argv=['/usr/bin/codex','app-server','daemon','pid-update-loop']),
              11:dict(parent=1,owned=True,name='codex',argv=['/usr/bin/codex','app-server','--listen','unix://','--analytics-default-enabled','--managed-daemon']),
              12:dict(parent=11,owned=True,name='python3',argv=['python3','work.py'])}
        self.assertEqual(workspace.classify(rows,99,live_codex=False),[12])
        self.assertEqual(workspace.classify(rows,99,live_codex=True),[10,11,12])
        value=dict(attached=0,busy_pids=[],sessions=[],codex_threads=['live'])
        self.assertFalse(workspace.idle_check(value,now=10000)['idle'])

    def test_competing_reservation_and_pending_never_expire(self):
        with patch.object(workspace.os, 'geteuid', return_value=0), patch.object(workspace.time, 'time', return_value=100):
            workspace.reservation('reserve', 'first', 1800)
            with self.assertRaisesRegex(RuntimeError, 'another operation'):
                workspace.reservation('reserve', 'second', 1800)
        workspace.write(self.directory / 'maintenance.json', {'operation': 'first', 'phase': 'pending', 'expires_at': 10})
        with patch.object(workspace.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'another operation'):
                workspace.reservation('reserve', 'second', 1800)
            with self.assertRaisesRegex(RuntimeError, 'verification'):
                workspace.reservation('clear', 'first', 1800)

    def test_open_reattaches_without_detaching_clients_or_new_name(self):
        (self.directory / 'workspace.lock').touch()
        with patch.object(workspace.os, 'geteuid', return_value=1004), patch.object(workspace.pwd, 'getpwnam') as user, \
                patch.object(workspace, 'status', return_value=dict(available=True, pending=False, maintenance=False)), \
                patch.object(workspace.subprocess, 'call', return_value=0) as execute:
            user.return_value.pw_uid = 1004
            self.assertEqual(workspace.main(['open']), 0)
            self.assertEqual(execute.call_args.args[0], ['tmux', 'new-session', '-A', '-s', 'main', '-c', str(workspace.REPO)])


if __name__ == '__main__':
    unittest.main()
