import contextlib
import importlib.util
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/tests'))
import live_tmux_guard  # noqa: F401  Before the workspace code.


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


air = load('client_air', ROOT / 'klokast-dev/lib/air.py')
workspace = load('workspace_air', ROOT / 'ansible/lib/air_workspace.py')


class SelectionTest(unittest.TestCase):
    def status(self, **kwargs):
        return dict(dict(available=True, workspace=False, maintenance=False), **kwargs)

    def choose(self, values, last=None, explicit=None):
        return air.choose(['k001', 'k002'], ['k002', 'k001'], last, explicit, values.__getitem__)

    def test_defaults_and_fallback(self):
        values = {'k001': self.status(), 'k002': self.status()}
        self.assertEqual(self.choose(values), ('k002', False))
        values['k002']['available'] = False
        self.assertEqual(self.choose(values), ('k001', False))
        with self.assertRaisesRegex(ValueError, 'unavailable|probe failed'):
            self.choose(values, explicit='k002')

    def test_remembered_workspace_reconnects_and_a_closed_runner_is_skipped(self):
        values = {'k001': self.status(workspace=True), 'k002': self.status()}
        self.assertEqual(self.choose(values, last='k001'), ('k001', True))
        values['k001'].update(maintenance=True, reservation={'operation': 'a' * 24, 'owner': 'dom0'})
        with patch('sys.stderr', new_callable=io.StringIO) as notice:
            self.assertEqual(self.choose(values, last='k001'), ('k002', False))
        self.assertIn('skipped k001-air: closed for dom0 update operation ' + 'a' * 24, notice.getvalue())

    def test_missing_remembered_workspace_uses_preference(self):
        self.assertEqual(self.choose({'k001': self.status(), 'k002': self.status()}, last='k001'), ('k002', False))

    def test_explicit_requires_declaration(self):
        with self.assertRaisesRegex(ValueError, 'not declared'):
            self.choose({}, explicit='k003')

    def test_explicit_closed_runner_names_its_closing_operation(self):
        values = {'k001': self.status(workspace=True), 'k002': self.status()}
        values['k001'].update(maintenance=True, reservation={'operation': 'a' * 24, 'owner': 'air'})
        with self.assertRaisesRegex(ValueError, 'k001-air: closed for runner replacement operation ' + 'a' * 24 +
                                    '; it opens again when that operation ends'):
            self.choose(values, explicit='k001')

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

    def test_status_shows_the_closing_operation_and_a_missing_prerequisite(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, XDG_CONFIG_HOME=temporary):
            instance = Path(temporary) / 'instance'
            instance.mkdir()
            (instance / 'klokast-instance.json').write_text(json.dumps({
                'schema-version': 1, 'tailscale': {'tailnet-dns-name': 'fixture.ts.net'},
                'boxes': {'k001': {}, 'k002': {}}, 'airunners': ['k001-air', 'k002-air']}))
            values = {
                'k001': dict(self.status(), attached=1, busy_pids=[]),
                'k002': dict(self.status(), attached=0, busy_pids=[]),
            }
            values['k001'].update(maintenance=True, reservation={'operation': 'replacement-id', 'owner': 'air'})
            values['k002'].update(available=False, readiness_errors=['required runner command is missing: mosh-server'])
            with patch.object(air, 'probe', side_effect=values.__getitem__), patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(air.main(['--instance', str(instance), '--status']), 0)
            self.assertIn('closed=True workspace=False attached=1 work=0 closed_by=replacement-id', output.getvalue())
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
        self.assertEqual(workspace.describe(rows, [12, 13]), ['12 python3 (parent 11)', '13 bash (parent 1)'])

    def test_idle_has_no_delay_after_activity(self):
        idle = dict(attached=0, busy_pids=[], sessions=[])
        self.assertTrue(workspace.idle_check(idle, now=100)['idle'])
        for active in (dict(idle, attached=1), dict(idle, busy_pids=[42]),
                       dict(idle, codex_threads=['live'])):
            self.assertFalse(workspace.idle_check(active, now=101)['idle'])
            result = workspace.idle_check(idle, now=102)
            self.assertTrue(result['idle'])
            self.assertEqual(result['hold_seconds'], 0)
        idle['sessions'] = [{'activity': 103}]
        self.assertTrue(workspace.idle_check(idle, now=104)['idle'])
        workspace.write(self.directory / 'activity.json',
                        dict(idle_since=None, last_busy=104, latest_tmux=103))
        self.assertTrue(workspace.idle_check(idle, now=104)['idle'])

    def test_image_idle_matches_fork_parent_but_preserves_unknown_work(self):
        rows={42:dict(parent=1,owned=True,name='codex',argv=['codex','fork','parent']),
              43:dict(parent=1,owned=True,name='codex',argv=['codex','fork','other']),
              44:dict(parent=42,owned=True,name='python3',argv=['python3','work.py'])}
        # A forked TUI keeps its parent ID in argv. Codex reports the commands of its threads natively.
        self.assertEqual(workspace.agent_work(rows,{},['child'],['parent']),[(43,None)])
        value=dict(attached=0,busy_pids=[42,43,44],codex_threads=['child'],sessions=[])
        native=dict(threads=['child'],fork_parents=['parent'],latest=10)
        with patch.object(workspace,'status',side_effect=lambda:dict(value)), \
                patch.object(workspace,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(native),'')):
            # The probe that passed decides; status lists every process that is not an idle shell.
            self.assertTrue(workspace.image_idle()['idle'])
            # The final check detaches each tmux client; other maintenance still waits for a detach.
            value['attached']=1
            self.assertTrue(workspace.image_idle()['idle'])
            self.assertFalse(workspace.idle_check(dict(value,busy_pids=[]))['idle'])
        with patch.object(workspace,'status',return_value=dict(value,attached=0)), \
                patch.object(workspace,'run',side_effect=RuntimeError('Codex discussion is active or needs attention')):
            self.assertFalse(workspace.image_idle()['idle'])
        refusal=subprocess.CompletedProcess([],1,'','AIR image workspace: Claude Code discussion is active or needs attention: PID 8 is waiting\n')
        with patch.object(workspace,'status',return_value=dict(value,attached=0)), \
                patch.object(workspace,'run',return_value=refusal):
            self.assertEqual(workspace.image_idle()['reason'],
                             'Claude Code discussion is active or needs attention: PID 8 is waiting')
        self.assertFalse(workspace.known_codex(rows[42]['argv'],['child']))

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
        self.assertTrue(workspace.idle_check(current, now=100)['idle'])
        self.assertTrue(workspace.idle_check(current, now=7300)['idle'])

    def test_idle_daemon_does_not_block_but_loaded_discussions_and_other_commands_do(self):
        rows={10:dict(parent=1,owned=True,name='codex',argv=['/usr/bin/codex','app-server','daemon','pid-update-loop']),
              11:dict(parent=1,owned=True,name='codex',argv=['/usr/bin/codex','app-server','--listen','unix://','--analytics-default-enabled','--managed-daemon']),
              12:dict(parent=11,owned=True,name='python3',argv=['python3','work.py'])}
        self.assertEqual(workspace.classify(rows,99,live_codex=False),[12])
        self.assertEqual(workspace.classify(rows,99,live_codex=True),[10,11,12])
        value=dict(attached=0,busy_pids=[],sessions=[],codex_threads=['live'])
        self.assertFalse(workspace.idle_check(value,now=10000)['idle'])

    def test_closure_belongs_to_one_operation_and_never_expires(self):
        with patch.object(workspace.os,'geteuid',return_value=0),patch.object(workspace,'image_idle',return_value={'idle':True}):
            self.assertEqual(workspace.close('a'*24,'air'),{'schema_version':2,'operation':'a'*24,'owner':'air'})
            # The same operation closes again on resume; another operation cannot take it over.
            self.assertEqual(workspace.close('a'*24,'air')['operation'],'a'*24)
            with self.assertRaisesRegex(RuntimeError,'closed for runner replacement operation a{24}; resume'):
                workspace.close('b'*24,'dom0')
            with self.assertRaisesRegex(RuntimeError,'not for operation b{24}'):
                workspace.release('b'*24)
        self.assertEqual(workspace.closure(),
                         {'schema_version':2,'operation':'a'*24,'owner':'air'})

    def test_close_refuses_a_running_agent_and_keeps_the_runner_open(self):
        with patch.object(workspace.os,'geteuid',return_value=0), \
                patch.object(workspace,'image_idle',return_value={'idle':False,'reason':'Claude Code session x runs'}):
            with self.assertRaisesRegex(RuntimeError,'an agent runs on the runner.*session x runs'):
                workspace.close('a'*24,'air')
        self.assertIsNone(workspace.closure())

    def test_planned_reservation_of_an_older_helper_does_not_close_the_runner(self):
        workspace.write(self.directory/'maintenance.json',{'operation':'old','phase':'planned','expires_at':10**10})
        self.assertIsNone(workspace.closure())
        with patch.object(workspace.os,'geteuid',return_value=0),patch.object(workspace,'image_action') as restore:
            self.assertEqual(workspace.release('a'*24),{'cleared':True})
        restore.assert_not_called();self.assertFalse((self.directory/'maintenance.json').exists())

    def test_release_restores_and_reports_a_missing_checkpoint(self):
        for restored,expected in (({'restored':False},'not restored: checkpoint missing'),({'phase':'complete'},'restored')):
            with self.subTest(expected=expected):
                workspace.write(self.directory/'maintenance.json',{'schema_version':2,'operation':'a'*24,'owner':'dom0'})
                with patch.object(workspace.os,'geteuid',return_value=0),patch.object(workspace,'image_action',return_value=restored):
                    self.assertEqual(workspace.release('a'*24),{'cleared':True,'workspace':expected})
                self.assertFalse((self.directory/'maintenance.json').exists())

    def test_image_begin_detaches_clients_after_the_final_check(self):
        (self.directory/'workspace.lock').touch()
        order=[]
        with patch.object(workspace.os,'geteuid',return_value=0),patch.object(workspace.time,'time',return_value=100), \
                patch.object(workspace,'image_idle',side_effect=lambda:order.append('check') or {'idle':True}), \
                patch.object(workspace,'detach_clients',side_effect=lambda:order.append('detach') or 2), \
                patch.object(workspace,'image_action',side_effect=lambda action,operation:order.append(action)), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(workspace.main(['image-begin','--operation','a'*24,'--owner','air']),0)
        # The closure that refuses a new connection exists before the detach.
        self.assertEqual(order,['check','detach','checkpoint'])
        self.assertEqual((json.loads(out.getvalue())['detached_clients'],json.loads((self.directory/'maintenance.json').read_text())['owner']),
                         (2,'air'))
        (self.directory/'maintenance.json').unlink();order.clear()
        with patch.object(workspace.os,'geteuid',return_value=0), \
                patch.object(workspace,'image_idle',return_value={'idle':False,'reason':'Claude Code discussion is active'}), \
                patch.object(workspace,'detach_clients') as detach,patch.object(workspace,'image_action') as action, \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(workspace.main(['image-begin','--operation','a'*24,'--owner','dom0']),1)
            self.assertEqual(workspace.main(['image-begin','--operation','a'*24]),1)
        detach.assert_not_called();action.assert_not_called()
        self.assertIn('needs --operation and --owner',error.getvalue())
        self.assertIn('an agent runs on the runner or its activity check is unavailable: Claude Code discussion is active',error.getvalue())

    def test_detach_ends_each_client_with_a_message(self):
        listing=subprocess.CompletedProcess([],0,'/dev/pts/3\n/dev/pts/7\nbad;name\n','')
        with patch.object(workspace,'run',side_effect=[listing,None,None]) as run, \
                patch.object(workspace.socket,'gethostname',return_value='k002-air'):
            self.assertEqual(workspace.detach_clients(),2)
        detach=[c.args[0] for c in run.call_args_list[1:]]
        self.assertEqual([argv[:5] for argv in detach],[['tmux','-u','detach-client','-t','/dev/pts/3'],
                                                       ['tmux','-u','detach-client','-t','/dev/pts/7']])
        self.assertEqual(detach[0][5:],['-E','printf "%s\\n" \'k002-air is being updated, so the tmux workspace closed. '
                                       'It returns when the update ends. Check it on the MacBook: kk status\''])
        self.assertTrue(all(c.kwargs=={'user':True,'check':False} for c in run.call_args_list))
        # Without a tmux server there is no client; the stop ends any other connection.
        with patch.object(workspace,'run',return_value=subprocess.CompletedProcess([],1,'','no server running')):
            self.assertEqual(workspace.detach_clients(),0)

    def test_open_reattaches_without_detaching_clients_or_new_name(self):
        (self.directory / 'workspace.lock').touch()
        with patch.object(workspace.os, 'geteuid', return_value=1004), patch.object(workspace.pwd, 'getpwnam') as user, \
                patch.object(workspace, 'status', return_value=dict(available=True, maintenance=False, reservation=None)), \
                patch.object(workspace.subprocess, 'call', return_value=0) as execute:
            user.return_value.pw_uid = 1004
            self.assertEqual(workspace.main(['open']), 0)
            self.assertEqual(execute.call_args.args[0], ['tmux', '-u', 'new-session', '-A', '-s', 'main', '-c', str(workspace.REPO)])

    def test_closed_runner_refuses_to_open_and_names_the_operation(self):
        (self.directory/'workspace.lock').touch()
        closed={'schema_version':2,'operation':'a'*24,'owner':'dom0'}
        with patch.object(workspace.os,'geteuid',return_value=1004),patch.object(workspace.pwd,'getpwnam') as user, \
                patch.object(workspace,'status',return_value=dict(available=True,maintenance=True,reservation=closed)), \
                patch.object(workspace.subprocess,'call') as attach,patch('sys.stderr',new_callable=io.StringIO) as error:
            user.return_value.pw_uid=1004
            self.assertEqual(workspace.main(['open','--reconnect']),1)
        attach.assert_not_called()
        self.assertIn('stays closed until dom0 update operation '+'a'*24+' finishes',error.getvalue())

    def test_handoff_and_placement_actions_are_removed(self):
        # Discussions stay on their runner: no action moves, receives or places a workspace.
        for action in ('ready', 'idle', 'handoff-complete', 'placement'):
            with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                workspace.main([action])

    def test_role_removes_the_handoff_and_placement_files(self):
        import yaml
        tasks={t['name']:t for t in yaml.safe_load((ROOT/'ansible/roles/air-workspace/tasks/main.yml').read_text())}
        removed=tasks['Remove the discussion handoff and workspace placement files']
        self.assertEqual(removed['ansible.builtin.file']['state'],'absent')
        self.assertIn('/var/lib/klokast/air/placement.json',removed['loop'])
        self.assertIn('/usr/local/bin/klokast-codex-handoff',removed['loop'])
        installed=tasks['Install the runner workspace checkpoint modules']['loop']
        self.assertFalse({'codex_handoff.py','codex_discussion.py','codex_storage_v0163.json'}&set(installed))

    def test_editor_is_the_busybox_vi_that_the_alpine_image_provides(self):
        role=(ROOT/'ansible/roles/air-workspace/tasks/main.yml').read_text()
        for variable in ('EDITOR','VISUAL','GIT_EDITOR'):
            self.assertIn(f'export {variable}=vi\n',role)
        self.assertNotIn('=vim',role)

    def test_shell_tmux_and_git_settings_are_root_owned_and_no_home_startup_file_is_retained(self):
        import yaml
        sys.path.insert(0,str(ROOT/'ansible/roles/vm-template-builder/files'))
        import vm_infrastructure_storage as storage
        tasks={t['name']:t for t in yaml.safe_load((ROOT/'ansible/roles/air-workspace/tasks/main.yml').read_text())}
        for task in tasks.values():
            copy=task.get('ansible.builtin.copy',{})
            # The role writes no file in the retained home; that state is not declared.
            self.assertFalse(str(copy.get('dest','')).startswith('/home/agent/'),task['name'])
            self.assertNotIn('ansible.builtin.blockinfile',task,task['name'])
        for name,dest in (('Install the runner interactive shell behavior','/etc/bash/klokast-air.sh'),
                          ('Install the runner tmux settings','/etc/tmux.conf'),('Install the runner Git author','/etc/gitconfig')):
            copy=tasks[name]['ansible.builtin.copy']
            self.assertEqual((copy['dest'],copy['owner'],copy['mode']),(dest,'root','0644'))
        removed=tasks['Remove the home startup files that this role and the retired container role installed']['loop']
        self.assertEqual(set(removed)&set(storage.AIR_HOME),set())
        self.assertNotIn('.local/bin',' '.join(storage.AIR_HOME)+tasks['Install the runner interactive shell behavior']['ansible.builtin.copy']['content'])

    def test_failed_login_attach_returns_to_the_interactive_shell(self):
        import pty
        import textwrap
        role=(ROOT/'ansible/roles/air-workspace/tasks/main.yml').read_text()
        block=role.split('- name: Install the runner interactive shell behavior',1)[1].split('    content: |\n',1)[1].split('\n    owner:',1)[0]
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
