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
                patch.object(workspace,'loaded',return_value=[{'id':'thread','status':{'type':'active'}}]):
            with self.assertRaisesRegex(RuntimeError,'still running'):workspace.close('a'*24,0)
        self.assertFalse(any(c.args and c.args[0]=='thread/archive' for c in rpc.call.call_args_list))


class SingletonPlacement(unittest.TestCase):
    def test_current_location_overrides_device_preferences_and_remembered_runner(self):
        placement={'phase':'ready','current':'k001','operation':'a'*24}
        values={box:dict(available=True,workspace=True,maintenance=False,pending=False,placement=placement) for box in ('k001','k002')}
        self.assertEqual(air.choose(list(values),['k002','k001'],'k002',inspect=values.__getitem__),('k001',True))
        with self.assertRaisesRegex(ValueError,'only valid'):air.choose(list(values),[],None,'k002',values.__getitem__)

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


class ControllerMove(unittest.TestCase):
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
