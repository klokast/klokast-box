"""Workspace identity, the native Codex transport, and bounded restore recovery."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'ansible/lib'))
sys.path.insert(0,str(REPO/'ansible/tests'))
import live_tmux_guard  # noqa: F401  Before the workspace code.
import codex_workspace as workspace


class WorkspaceIdentity(unittest.TestCase):
    def test_interactive_shell_stays_frozen_until_its_foreground_client_resumes(self):
        rows={10:{'argv':['bash'],'parent':1},11:{'argv':['codex'],'parent':10}}
        events=[]
        with patch('air_workspace.processes',return_value=rows),patch.object(workspace,'tmux',return_value=Mock(stdout='10\n')), \
                patch.object(workspace.os,'kill',side_effect=lambda pid,sig:events.append((pid,sig))):
            with workspace.frozen_tuis(['%1']):
                self.assertEqual(events,[(10,workspace.signal.SIGSTOP),(11,workspace.signal.SIGSTOP)])
        self.assertEqual(events[-2:],[(11,workspace.signal.SIGCONT),(10,workspace.signal.SIGCONT)])

    def test_non_login_tmux_preserves_title_and_field_separators(self):
        with patch.object(workspace.subprocess,'run',return_value=Mock(returncode=0)) as run:
            workspace.tmux('list-panes','-a')
        self.assertEqual(run.call_args.args[0][:2],['tmux','-u'])

    def test_visible_fork_replaces_retained_parent_without_copying_other_threads(self):
        threads=[{'id':'parent','name':'Work','cwd':'/home/agent/repo','forkedFromId':None},
                 {'id':'current','name':'Work','cwd':'/home/agent/repo','forkedFromId':'parent'},
                 {'id':'archive','name':'Other','cwd':'/home/agent/repo'}]
        self.assertEqual(workspace.match_discussion('⠹ Work | repo','/home/agent/repo',threads),'current')

    def test_ambiguous_or_missing_live_identity_stops_the_checkpoint(self):
        thread={'id':'first','name':'Work','cwd':'/home/agent/repo'}
        for threads in ([],[thread,dict(thread,id='second')]):
            with self.assertRaisesRegex(RuntimeError,'identify one'):
                workspace.match_discussion('Work | repo','/home/agent/repo',threads)

class NativeTransport(unittest.TestCase):
    def test_transport_uses_the_image_codex_and_never_a_home_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);local=root/'.local/bin';local.mkdir(parents=True)
            (local/'codex').write_text('#!/bin/sh\nexit 0\n');(local/'codex').chmod(0o700)
            image=root/'image-codex';image.write_text('#!/bin/sh\nexit 0\n');image.chmod(0o755)
            with patch.object(workspace,'HOME',root/'.codex'),patch.object(workspace,'CODEX',str(image)), \
                    patch.dict(workspace.os.environ,PATH=str(local)+':/usr/bin:/bin'):
                self.assertEqual(workspace.codex_binary(),str(image))
            with patch.object(workspace,'CODEX',str(root/'absent')):
                with self.assertRaisesRegex(RuntimeError,'not installed in the runner image'):workspace.codex_binary()

    def test_stdio_app_server_uses_the_managed_proxy_environment(self):
        proxy={'https_proxy':'http://192.168.200.41:7890','NO_PROXY':'localhost,.ts.net'}
        process=Mock(stdin=io.BytesIO(),stdout=io.BytesIO())
        with patch.dict(os.environ,{'https_proxy':'http://old.invalid','KEEP':'caller'},clear=True), \
                patch.object(workspace.platform_proxy,'environment',return_value=proxy), \
                patch.object(workspace,'codex_binary',return_value='/usr/local/bin/codex'), \
                patch.object(workspace.RPC,'call',return_value={}), \
                patch.object(workspace.subprocess,'Popen',return_value=process) as launch:
            with workspace.RPC():pass
        self.assertEqual(launch.call_args.kwargs['env'],dict(proxy,KEEP='caller'))


class Restore(unittest.TestCase):
    def test_interrupted_unarchive_resumes_before_the_staged_start(self):
        operation='a'*24
        value={'version':1,'operation':operation,'discussions':['one','two'],'sessions':[
            {'name':'qualification-test','windows':[{'index':0,'width':120,'height':40,
                'panes':[{'cwd':'/home/agent/repo'}]}]}]}
        archived={'one':True,'two':True};failed=[False]
        rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
        def call(method,params):
            sid=params['threadId']
            if method=='thread/read':
                return {'thread':{'path':str(workspace.HOME/('archived_sessions' if archived[sid] else 'sessions')/'rollout.jsonl')}}
            if sid=='two' and not failed[0]:
                failed[0]=True;raise RuntimeError('native unavailable')
            archived[sid]=False;return {}
        rpc.call.side_effect=call
        def tmux(*args,**kwargs):
            if args[0]=='new-session':raise RuntimeError('activation reached')
            return Mock(returncode=1,stdout='')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(workspace,'ROOT',root),patch.object(workspace,'RPC',return_value=rpc), \
                    patch.object(workspace,'codex_environment',return_value={}), \
                    patch.object(workspace,'live_writers',return_value=set()),patch.object(workspace,'tmux',side_effect=tmux):
                with self.assertRaisesRegex(RuntimeError,'native unavailable'):workspace.restore(value,operation)
                self.assertEqual(json.loads((root/'workspaces'/operation/'restored.json').read_text())['phase'],'starting')
                with self.assertRaisesRegex(RuntimeError,'activation reached'):workspace.restore(value,operation)
        self.assertEqual(archived,{'one':False,'two':False})
        self.assertEqual([c.args[1]['threadId'] for c in rpc.call.call_args_list if c.args[0]=='thread/unarchive'],['one','two','two'])

    def test_completed_native_start_keeps_an_existing_session_of_the_same_name(self):
        with tempfile.TemporaryDirectory() as d:
            operation='a'*24;root=Path(d);directory=root/'workspaces'/operation;directory.mkdir(parents=True)
            stage='restore-'+operation[:12]+'-0'
            (directory/'restored.json').write_text(json.dumps({'phase':'starting','operation':operation,
                'discussions':['one'],'sessions':[{'name':'main','stage':stage}]}))
            with patch.object(workspace,'ROOT',root),patch.object(workspace,'live_writers',return_value={'one'}), \
                    patch.object(workspace,'tmux',return_value=Mock(returncode=0)) as tmux, \
                    patch.object(workspace,'verify_restored'):
                result=workspace.restore({'version':1,'operation':operation,'sessions':[{'name':'main','windows':[]}]},operation)
                self.assertEqual(result['phase'],'complete')
                calls=[c.args for c in tmux.call_args_list]
                self.assertIn(('rename-session','-t','=main','main-before-'+operation[:8]),calls)
                self.assertIn(('rename-session','-t','='+stage,'main'),calls)
                self.assertFalse(any(c[0] in ('new-session','kill-session') for c in calls))


    def test_partial_native_start_never_creates_another_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            operation='a'*24;root=Path(d);directory=root/'workspaces'/operation;directory.mkdir(parents=True)
            (directory/'restored.json').write_text(json.dumps({'phase':'starting','discussions':['one','two'],'sessions':[]}))
            with patch.object(workspace,'ROOT',root),patch.object(workspace,'live_writers',return_value={'one'}), \
                    patch.object(workspace,'tmux') as tmux:
                with self.assertRaisesRegex(RuntimeError,'partial native start'):
                    workspace.restore({'version':1,'operation':operation,'sessions':[{}]},operation)
                tmux.assert_not_called()

    def test_interrupted_stage_with_work_or_an_attached_client_is_preserved(self):
        for command,attached in (('vim','0'),('bash','1')):
            with self.subTest(command=command,attached=attached),tempfile.TemporaryDirectory() as d:
                operation='a'*24;root=Path(d);directory=root/'workspaces'/operation;directory.mkdir(parents=True)
                (directory/'restored.json').write_text(json.dumps({'phase':'starting','discussions':['one'],'sessions':[]}))
                def tmux(*args,**kw):
                    return Mock(returncode=0,stdout=command+'\n' if args[0]=='list-panes' else attached+'\n')
                with patch.object(workspace,'ROOT',root),patch.object(workspace,'live_writers',return_value=set()), \
                        patch.object(workspace,'tmux',side_effect=tmux) as calls:
                    with self.assertRaisesRegex(RuntimeError,'contains work or a client'):
                        workspace.restore({'version':1,'operation':operation,'sessions':[{}]},operation)
                    self.assertFalse(any(c.args[0] in ('kill-session','new-session') for c in calls.call_args_list))


if __name__=='__main__':unittest.main()
