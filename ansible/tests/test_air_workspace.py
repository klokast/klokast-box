import importlib.util
import json
import io
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
        with self.assertRaisesRegex(ValueError, 'unavailable|probe failed'):
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

    def test_explicit_pending_runner_warns_and_remains_accessible(self):
        values={'k001':self.status(),'k002':self.status()}
        values['k001'].update(maintenance=True,pending=True)
        with patch('sys.stderr',new_callable=io.StringIO) as warning:
            self.assertEqual(self.choose(values,explicit='k001'),('k001',False))
        self.assertIn('finish work and detach',warning.getvalue())

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

    def test_probe_separates_transport_command_timeout_and_response_failures(self):
        failures = [
            (subprocess.CompletedProcess([], 255, '', 'Permission denied'), 'SSH connection failed (exit 255): Permission denied'),
            (subprocess.CompletedProcess([], 1, '', 'cannot inspect tmux sessions: access denied'), 'remote workspace status command failed (exit 1)'),
            (subprocess.CompletedProcess([], 0, 'not JSON', ''), 'remote workspace status is invalid JSON'),
            (subprocess.CompletedProcess([], 0, '[]', ''), 'incompatible workspace status'),
            (subprocess.TimeoutExpired('ssh', 20), 'probe timed out after 20 seconds'),
            (FileNotFoundError('ssh is missing'), 'could not run the local SSH probe'),
        ]
        for result, message in failures:
            with self.subTest(message=message):
                kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch.object(air.subprocess, 'run', **kwargs):
                    value = air.probe('k001')
                self.assertFalse(value['available'])
                self.assertIn(message, value['error'])

    def test_probe_accepts_readiness_details_and_older_helpers(self):
        value = dict(schema_version=1, hostname='k001-air', attached=0, busy_pids=[], **self.status())
        for details in ({}, {'readiness_errors': ['runner repository is missing: /test/repo']}):
            expected = dict(value, **details)
            with patch.object(air.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(expected), '')):
                self.assertEqual(air.probe('k001'), expected)

    def test_status_shows_pending_operation_and_missing_prerequisite(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, XDG_CONFIG_HOME=temporary):
            instance = Path(temporary) / 'instance'
            instance.mkdir()
            (instance / 'klokast-instance.json').write_text(json.dumps({
                'schema-version': 1, 'tailscale': {'tailnet-dns-name': 'fixture.ts.net'},
                'boxes': {'k001': {}, 'k002': {}}, 'airunners': ['k001-air', 'k002-air']}))
            placement = {'current': 'k001', 'phase': 'ready'}
            values = {
                'k001': dict(self.status(), attached=1, busy_pids=[], placement=placement),
                'k002': dict(self.status(), attached=0, busy_pids=[], placement=placement),
            }
            values['k001'].update(pending=True, reservation={'operation': 'replacement-id'})
            values['k002'].update(available=False, readiness_errors=['required runner command is missing: mosh-server'])
            with patch.object(air, 'probe', side_effect=values.__getitem__), patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(air.main(['--instance', str(instance), '--status']), 0)
            self.assertIn('pending=True operation=replacement-id', output.getvalue())
            self.assertIn('error=required runner command is missing: mosh-server', output.getvalue())


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        state = patch.object(workspace, 'STATE', self.directory)
        state.start(); self.addCleanup(state.stop)

    def test_controller_locale_uses_printable_tmux_fields(self):
        calls=[]
        def run(argv,**kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv,0,'main|0|900|800\nuser|notes|1|901|850\n' if argv[0]=='tmux' else '', '')
        with patch.object(workspace,'run',side_effect=run),patch.object(workspace,'processes',return_value={}), \
                patch.object(workspace,'codex_writers',return_value=[]), \
                patch.object(workspace.os,'geteuid',return_value=0), \
                patch.object(workspace,'REPO',self.directory):
            value=workspace.status()
        self.assertEqual(value['attached'],1)
        self.assertTrue(value['workspace'])
        self.assertEqual(value['sessions'][1]['name'],'user|notes')
        self.assertNotIn('\t',next(c for c in calls if c[0]=='tmux')[-1])

    def test_missing_runner_prerequisites_are_reported_separately(self):
        for missing in ('repository', 'tmux', 'mosh-server'):
            with self.subTest(missing=missing):
                def run(argv, **kwargs):
                    if argv[0] == 'tmux':
                        self.assertNotEqual(missing, 'tmux')
                        return subprocess.CompletedProcess(argv, 1, '', 'no server running')
                    return subprocess.CompletedProcess(argv, int(argv[-1] == 'command -v ' + missing), '', '')
                repo = self.directory / 'absent' if missing == 'repository' else self.directory
                with patch.object(workspace, 'run', side_effect=run), patch.object(workspace, 'processes', return_value={}), \
                        patch.object(workspace, 'codex_writers', return_value=[]), \
                        patch.object(workspace.os, 'geteuid', return_value=0), patch.object(workspace, 'REPO', repo):
                    value = workspace.status()
                self.assertFalse(value['available'])
                self.assertEqual(len(value['readiness_errors']), 1)
                self.assertIn(missing, value['readiness_errors'][0])

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
        self.assertTrue(workspace.idle_check(idle, now=7300)['idle'])
        busy = dict(idle, attached=1)
        self.assertFalse(workspace.idle_check(busy, now=7400)['idle'])
        self.assertFalse(workspace.idle_check(idle, now=15000)['idle'])
        self.assertTrue(workspace.idle_check(idle, now=22200)['idle'])
        idle['sessions'] = [{'activity': 22201}]
        self.assertFalse(workspace.idle_check(idle, now=22202)['idle'])

    def test_exited_process_does_not_block_but_stopped_and_unknown_work_do(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            for pid, state in ((10, 'Z'), (11, 'T'), (12, 'S')):
                directory = proc / str(pid)
                directory.mkdir()
                (directory / 'status').write_text(
                    'Name:\tcodex\nState:\t' + state + '\nPPid:\t1\nUid:\t1004 1004 1004 1004\n')
                (directory / 'cmdline').write_bytes(b'')
            rows = workspace.processes(1004, proc)
        self.assertEqual(workspace.classify(rows, 99), [11, 12])
        current = dict(attached=0, sessions=[], busy_pids=workspace.classify({10: rows[10]}, 99))
        self.assertFalse(workspace.idle_check(current, now=100)['idle'])
        self.assertTrue(workspace.idle_check(current, now=7300)['idle'])

    def test_whole_workspace_completion_releases_only_verified_idle_source(self):
        operation='a'*24;session='11111111-1111-4111-8111-111111111111'
        child=workspace.hashlib.sha256((operation+session).encode()).hexdigest()[:24]
        home=self.directory/'handoffs'
        placement=dict(operation=operation,phase='ready',origin='k002',destination='k001',current='k001')
        workspace.write(self.directory/'placement.json',placement)
        workspace.write(home/'workspaces'/operation/'manifest.json',dict(operation=operation,phase='closed',discussions=[session]))
        source=home/child/'source.json';ledger=home/'threads'/(session+'.json')
        workspace.write(source,dict(session=session,threads=[session],phase='moved',sha256='f'*64,completed_at=1000))
        workspace.write(ledger,dict(operation=child,phase='moved',sha256='f'*64))
        idle=dict(attached=0,busy_pids=[],codex_threads=[],sessions=[])
        with patch.object(workspace,'HANDOFFS',home),patch.object(workspace.os,'geteuid',return_value=0), \
                patch.object(workspace.socket,'gethostname',return_value='k002-air'), \
                patch.object(workspace.time,'time',return_value=1001):
            for change in ({'attached':1},{'busy_pids':[123]},{'codex_threads':[session]},
                           {'sessions':[{'activity':1001}]}):
                with patch.object(workspace,'status',return_value=dict(idle,**change)):
                    self.assertFalse(workspace.handoff_complete(operation,'')['released'])
            self.assertFalse((self.directory/'activity.json').exists())
            with patch.object(workspace,'status',return_value=idle):
                self.assertTrue(workspace.handoff_complete(operation,'')['released'])
                self.assertTrue(workspace.idle_check(idle,now=1001)['idle'])
                workspace.write(ledger,dict(operation='b'*24,phase='moved',sha256='f'*64))
                with self.assertRaisesRegex(RuntimeError,'handoff is incomplete or has changed'):
                    workspace.handoff_complete(operation,'')
            for change in ({'phase':'starting'},{'operation':'c'*24},{'current':'k002'}):
                workspace.write(self.directory/'placement.json',dict(placement,**change))
                with self.assertRaisesRegex(RuntimeError,'verified destination'):
                    workspace.handoff_complete(operation,'')

    def test_idle_daemon_does_not_block_but_loaded_discussions_and_other_commands_do(self):
        rows={10:dict(parent=1,owned=True,name='codex',argv=['/usr/bin/codex','app-server','daemon','pid-update-loop']),
              11:dict(parent=1,owned=True,name='codex',argv=['/usr/bin/codex','app-server','--listen','unix://','--analytics-default-enabled','--managed-daemon']),
              12:dict(parent=11,owned=True,name='python3',argv=['python3','work.py'])}
        self.assertEqual(workspace.classify(rows,99,live_codex=False),[12])
        self.assertEqual(workspace.classify(rows,99,live_codex=True),[10,11,12])
        value=dict(attached=0,busy_pids=[],sessions=[],codex_threads=['live'])
        self.assertFalse(workspace.idle_check(value,now=10000)['idle'])

    def test_completed_handoff_releases_only_selected_discussion_hold(self):
        session='11111111-1111-4111-8111-111111111111';operation='a'*24
        home=self.directory/'handoffs';source=home/operation/'source.json'
        workspace.write(source,dict(session=session,threads=[session],phase='moved',sha256='hash',completed_at=1000))
        workspace.write(home/'threads'/(session+'.json'),dict(operation=operation,phase='moved',sha256='hash'))
        previous=dict(idle_since=1000,last_threads=[session],last_unknown_work=0,last_attached=0)
        current=dict(attached=0,busy_pids=[],codex_threads=[],sessions=[])
        with patch.object(workspace,'HANDOFFS',home),patch.object(workspace.os,'geteuid',return_value=0), \
                patch.object(workspace,'status',return_value=current),patch.object(workspace.time,'time',return_value=1001):
            for change in ({'last_unknown_work':1},{'last_attached':1},{'last_threads':['other']}):
                workspace.write(self.directory/'activity.json',dict(previous,**change))
                self.assertFalse(workspace.handoff_complete(operation,session)['released'])
            workspace.write(self.directory/'activity.json',previous)
            self.assertTrue(workspace.handoff_complete(operation,session)['released'])
            self.assertTrue(workspace.idle_check(current,now=1001)['idle'])

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

    def test_pending_open_warns_without_ending_the_connection(self):
        (self.directory/'workspace.lock').touch()
        with patch.object(workspace.os,'geteuid',return_value=1004),patch.object(workspace.pwd,'getpwnam') as user, \
                patch.object(workspace,'status',return_value=dict(available=True,pending=True,maintenance=True)), \
                patch.object(workspace.subprocess,'call',return_value=0) as attach, \
                patch('sys.stderr',new_callable=io.StringIO) as warning:
            user.return_value.pw_uid=1004
            self.assertEqual(workspace.main(['open']),0)
            attach.assert_called_once()
            self.assertIn('recovery',warning.getvalue())

    def test_planned_maintenance_reconnect_also_warns(self):
        (self.directory/'workspace.lock').touch()
        with patch.object(workspace.os,'geteuid',return_value=1004),patch.object(workspace.pwd,'getpwnam') as user, \
                patch.object(workspace,'status',return_value=dict(available=True,pending=False,maintenance=True,workspace=True)), \
                patch.object(workspace.subprocess,'call',return_value=0),patch('sys.stderr',new_callable=io.StringIO) as warning:
            user.return_value.pw_uid=1004
            self.assertEqual(workspace.main(['open','--reconnect']),0)
            self.assertIn('please finish work and detach',warning.getvalue())

    def test_completed_move_reports_location_without_false_maintenance_warning(self):
        (self.directory/'workspace.lock').touch()
        value=dict(available=True,pending=True,maintenance=True,reservation=None,
                   placement={'phase':'ready','current':'k001'})
        with patch.object(workspace.os,'geteuid',return_value=1004),patch.object(workspace.pwd,'getpwnam') as user, \
                patch.object(workspace,'status',return_value=value),patch.object(workspace.socket,'gethostname',return_value='k002-air'), \
                patch.object(workspace.subprocess,'call',return_value=0),patch('sys.stderr',new_callable=io.StringIO) as warning:
            user.return_value.pw_uid=1004
            self.assertEqual(workspace.main(['open']),0)
        self.assertIn('shared workspace is on k001-air',warning.getvalue())
        self.assertNotIn('pending',warning.getvalue())

    def test_failed_login_attach_returns_to_the_interactive_shell(self):
        import pty
        import textwrap
        role=(ROOT/'ansible/roles/air-workspace/tasks/main.yml').read_text()
        block=role.split('- name: Install shared runner shell behavior',1)[1].split('    block: |\n',1)[1].split('\n- name:',1)[0]
        block=textwrap.dedent(block)
        helper=self.directory/'helper';helper.write_text('#!/bin/sh\nexit 1\n');helper.chmod(0o700)
        tmux=self.directory/'tmux';tmux.write_text('#!/bin/sh\nexit 0\n');tmux.chmod(0o700)
        block=block.replace('/usr/local/bin/klokast-air-workspace',str(helper))
        env=dict(os.environ,PATH=str(self.directory)+':'+os.environ['PATH']);env.pop('TMUX',None)
        master,slave=pty.openpty()
        try:
            p=subprocess.Popen(['bash','--noprofile','--norc','-ic',block+'\nprintf SHELL_REMAINS_OPEN'],
                stdin=slave,stdout=slave,stderr=slave,env=env)
            p.wait(timeout=10);os.close(slave);slave=None
            output=b''
            while True:
                try:data=os.read(master,4096)
                except OSError:break
                if not data:break
                output+=data
            self.assertIn(b'SHELL_REMAINS_OPEN',output)
        finally:
            if slave is not None:os.close(slave)
            os.close(master)


if __name__ == '__main__':
    unittest.main()
