"""Workspace identity, source fencing, and bounded move recovery."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'ansible/lib'))
import codex_workspace as workspace
import codex_handoff as handoff
sys.path.insert(0,str(REPO/'klokast-dev/lib'))
import air


def controller_module():
    loader=importlib.machinery.SourceFileLoader('workspace_controller',str(REPO/'ansible/bin/air-workspace'))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    module=importlib.util.module_from_spec(spec);loader.exec_module(module);return module


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

    def test_ambiguous_or_missing_live_identity_stops_the_move(self):
        thread={'id':'first','name':'Work','cwd':'/home/agent/repo'}
        for threads in ([],[thread,dict(thread,id='second')]):
            with self.assertRaisesRegex(RuntimeError,'identify one'):
                workspace.match_discussion('Work | repo','/home/agent/repo',threads)

    def test_active_turn_never_archives_a_discussion(self):
        rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
        value={'phase':'inventoried','scope':None,'discussions':['thread']}
        with patch.object(workspace,'manifest',return_value=value),patch.object(workspace,'LiveRPC',return_value=rpc), \
                patch.object(handoff,'codex_binary',return_value='/test/codex'), \
                patch.object(workspace,'loaded',return_value=[{'id':'thread','status':{'type':'active'}}]):
            with self.assertRaisesRegex(RuntimeError,'still running'):workspace.close('a'*24,0)
        self.assertFalse(any(c.args and c.args[0]=='thread/archive' for c in rpc.call.call_args_list))


class SingletonPlacement(unittest.TestCase):
    def test_older_discussion_start_cannot_activate_a_fenced_runner(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'workspace.lock').touch()
            (root/'placement.json').write_text(json.dumps({'phase':'ready','current':'another-box'}))
            with patch.object(handoff,'WORKSPACE',root),self.assertRaisesRegex(RuntimeError,'valid workspace placement'):
                with handoff.workspace_access('start'):self.fail('fenced runner was allowed to start')

    def test_current_location_overrides_device_preferences_and_remembered_runner(self):
        placement={'phase':'ready','current':'k001','operation':'a'*24}
        values={box:dict(available=True,workspace=True,maintenance=False,pending=False,placement=placement) for box in ('k001','k002')}
        self.assertEqual(air.choose(list(values),['k002','k001'],'k002',inspect=values.__getitem__),('k001',True))
        self.assertEqual(air.choose(list(values),[],None,'k002',values.__getitem__),('k002',True))

    def test_source_finish_preserves_sessions_and_reopens_dead_panes_as_shells(self):
        value={'scope':None,'discussions':['one','two','three'],'sessions':[{'name':'main','windows':[
            {'index':i,'source_remain_on_exit':'off','panes':[{'source_pane':'%'+str(i),'cwd':'/home/agent/repo'}]}
            for i in (0,2,4)]}]}
        rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
        with patch.object(handoff,'RPC',return_value=rpc),patch.object(handoff,'quiesce'), \
                patch.object(handoff,'codex_binary',return_value='/test/codex'),patch.object(workspace.subprocess,'run'), \
                patch.object(workspace,'seal_workspace'), \
                patch.object(workspace,'tmux',return_value=Mock(returncode=0,stdout='1\n')) as tmux:
            workspace.finish_source(value)
        self.assertEqual(sum(c.args[0]=='respawn-pane' for c in tmux.call_args_list),3)
        self.assertFalse(any(c.args[0].startswith('kill-') for c in tmux.call_args_list))
        self.assertEqual([c.args[1]['threadId'] for c in rpc.call.call_args_list],['one','two','three'])

    def test_unavailable_current_placement_never_falls_back(self):
        placement={'phase':'ready','current':'k001','operation':'a'*24}
        values={'k001':dict(available=False,workspace=False,maintenance=True),
                'k002':dict(available=True,workspace=True,maintenance=False,placement=placement)}
        with self.assertRaisesRegex(ValueError,'refusing to open another copy'):
            air.choose(list(values),['k002'],None,inspect=values.__getitem__)

    def test_transferring_and_conflicting_records_stop_attachment(self):
        for phase in ('starting','draining','ready'):
            p={'phase':phase,'current':'k001','operation':'a'*24}
            values={box:dict(available=True,workspace=True,maintenance=False,placement=p.copy()) for box in ('k001','k002')}
            if phase=='ready':values['k002']['placement']['current']='k002'
            with self.assertRaises(ValueError):air.choose(list(values),[],None,inspect=values.__getitem__)


class DestinationRecovery(unittest.TestCase):
    def test_standby_only_allows_idle_clients_without_persisted_history(self):
        rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
        rpc.call.return_value={'data':[]}
        thread={'id':'empty','name':None,'preview':'','status':{'type':'idle'}}
        connection=Mock()
        with patch.object(workspace,'tmux',return_value=Mock(returncode=0,stdout='%1\t1\t0\n')), \
                patch('air_workspace.processes',return_value={}),patch.object(workspace,'pane_clients',return_value=[2]), \
                patch.object(workspace,'LiveRPC',return_value=rpc),patch.object(handoff,'codex_binary',return_value='/test/codex'), \
                patch.object(workspace,'loaded',return_value=[thread]),patch.object(workspace.Store,'connect',return_value=connection):
            connection.execute.return_value.fetchone.return_value=None
            self.assertEqual(workspace.empty_destination(),['%1'])
            connection.execute.return_value.fetchone.return_value=(1,)
            with self.assertRaisesRegex(RuntimeError,'open discussion'):workspace.empty_destination()
            connection.execute.return_value.fetchone.return_value=None;thread['status']['type']='active'
            with self.assertRaisesRegex(RuntimeError,'open discussion'):workspace.empty_destination()

    def test_completed_native_start_keeps_existing_destination_session(self):
        with tempfile.TemporaryDirectory() as d:
            operation='a'*24;root=Path(d);directory=root/'workspaces'/operation;directory.mkdir(parents=True)
            stage='move-'+operation[:12]+'-0'
            (directory/'restored.json').write_text(json.dumps({'phase':'starting','operation':operation,
                'discussions':['one'],'sessions':[{'name':'main','stage':stage}]}))
            with patch.object(handoff,'ROOT',root),patch.object(workspace,'live_writers',return_value={'one'}), \
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
            with patch.object(handoff,'ROOT',root),patch.object(workspace,'live_writers',return_value={'one'}), \
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
                with patch.object(handoff,'ROOT',root),patch.object(workspace,'live_writers',return_value=set()), \
                        patch.object(workspace,'tmux',side_effect=tmux) as calls:
                    with self.assertRaisesRegex(RuntimeError,'contains work or a client'):
                        workspace.restore({'version':1,'operation':operation,'sessions':[{}]},operation)
                    self.assertFalse(any(c.args[0] in ('kill-session','new-session') for c in calls.call_args_list))


class ControllerMove(unittest.TestCase):
    def test_controller_compresses_large_import_and_scales_its_bounded_timeout(self):
        import os
        controller=controller_module();payload={'history':os.urandom(3*1024*1024).hex()}
        with patch.object(controller.subprocess,'run',return_value=Mock(returncode=0,stdout=b'{"phase":"imported"}')) as run:
            self.assertEqual(controller.remote('k001',['import','--operation','a'*24],data=payload),{'phase':'imported'})
        kwargs=run.call_args.kwargs
        self.assertIn('--gzip',run.call_args.args[0][-1])
        self.assertGreater(kwargs['timeout'],180)
        self.assertLessEqual(kwargs['timeout'],900)
        from codex_discussion import read_payload
        import io
        self.assertEqual(read_payload(io.BytesIO(kwargs['input']),True),payload)

    def test_source_maintenance_waits_for_idle_and_uses_the_existing_transaction(self):
        controller=controller_module()
        with tempfile.TemporaryDirectory() as d:
            args=argparse.Namespace(origin='k002',destination='k001',operation='a'*24)
            root=Path(d);(root/'placement.json').write_text(json.dumps({'operation':args.operation,'phase':'ready','current':'k001'}))
            results=[{'idle':False,'hold_seconds':12},{'idle':True,'hold_seconds':0},{'state':'complete','image':'qualified'}]
            with patch.object(controller,'ROOT',root),patch.object(controller.time,'sleep') as sleep, \
                    patch.object(controller.subprocess,'run',side_effect=[Mock(returncode=0,stdout=json.dumps(v)) for v in results]) as run:
                record={'phase':'complete','operation':args.operation}
                self.assertEqual(controller.maintain_source(args,record)['phase'],'complete')
                sleep.assert_called_once_with(12)
                calls=[c.args[0] for c in run.call_args_list]
                self.assertEqual([c[1] for c in calls],['activity','activity','update'])
                self.assertEqual(len({c[c.index('--operation')+1] for c in calls}),1)
            (root/'placement.json').write_text(json.dumps({'operation':'b'*24,'phase':'ready','current':'k002'}))
            with patch.object(controller,'ROOT',root),patch.object(controller.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'placement changed'):
                    controller.maintain_source(args,{'phase':'complete','operation':args.operation})
                run.assert_not_called()

    def test_lost_import_reply_resumes_without_reclosing_and_publishes_only_verified_destination(self):
        controller=controller_module()
        with tempfile.TemporaryDirectory() as d:
            args=argparse.Namespace(origin='k002',destination='k001',operation='a'*24,workspace=None,wait=0)
            manifest={'sessions':[{'windows':[{'panes':[{'cwd':'/home/agent/repo'}]}]}],'discussions':['one','two']}
            events=[];failed=[False]
            def remote(box,arguments,**kw):
                action=arguments[0];events.append((box,action,kw.get('data')))
                if action=='status':return {}
                if action=='workspace-inspect':return manifest
                if action=='workspace-close':return manifest
                if action=='ready':return {'account_fingerprint':'same account'}
                if action=='placement':return kw['data']
                if action=='export':return {'selected':arguments[2]}
                if action=='import' and not failed[0]:failed[0]=True;raise RuntimeError('lost import reply')
                if action=='workspace-restore':return {'phase':'complete'}
                return {}
            with patch.object(controller,'ROOT',Path(d)),patch.object(controller.source,'require_controller'), \
                    patch.object(controller.source,'snapshot',return_value={'instance':{'airunners':['k002-air','k001-air']}}), \
                    patch.object(controller.runtime,'vm_update_installation_lock',side_effect=contextlib.nullcontext), \
                    patch.object(controller,'remote',side_effect=remote):
                with self.assertRaisesRegex(RuntimeError,'lost import'):controller.execute(args)
                self.assertEqual(json.loads((Path(d)/args.operation/'operation.json').read_text())['phase'],'copying')
                self.assertFalse(any(e[1]=='placement' and e[2]['phase']=='ready' for e in events))
                self.assertEqual(controller.execute(args)['phase'],'complete')
            self.assertEqual(sum(e[1]=='workspace-close' for e in events),1)
            restore_index=next(i for i,e in enumerate(events) if e[1]=='workspace-restore')
            self.assertTrue(all(i>restore_index for i,e in enumerate(events) if e[1]=='placement' and e[2]['phase']=='ready'))
            self.assertTrue(all(e[2]['current'] is None for e in events if e[1]=='placement' and e[2]['phase']=='starting'))


if __name__=='__main__':unittest.main()
