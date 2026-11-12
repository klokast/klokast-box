"""Native activity and wrapper decisions must preserve active work."""
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import air_image_workspace as image
import codex_workspace as workspace

class NativeActivity(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        for name,value in (('ROOT',Path(temporary.name)),):
            mock=patch.object(image.handoff,name,value);mock.start();self.addCleanup(mock.stop)
        mock=patch.object(image.handoff,'codex_binary',return_value='/test/codex');mock.start();self.addCleanup(mock.stop)
        mock=patch.object(image,'inspect_claude',return_value=[]);mock.start();self.addCleanup(mock.stop)

    def test_active_approval_and_error_threads_block_shutdown(self):
        for status in ('active','systemError','notLoaded'):
            rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
            with patch.object(image.subprocess,'check_output',return_value='{"status":"running"}'), \
                    patch.object(image,'LiveRPC',return_value=rpc),patch.object(workspace,'loaded',return_value=[{'id':'t','status':{'type':status}}]):
                with self.assertRaisesRegex(RuntimeError,'active or needs attention'):image.inspect()
    def test_background_jobs_block_shutdown(self):
        rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
        rpc.call.return_value={'data':[{'id':'job'}]}
        with patch.object(image.subprocess,'check_output',return_value='{"status":"running"}'), \
                patch.object(image,'LiveRPC',return_value=rpc),patch.object(workspace,'loaded',return_value=[{'id':'t','updatedAt':10,'status':{'type':'idle'}}]):
            with self.assertRaisesRegex(RuntimeError,'background terminals'):image.inspect()
    def test_no_daemon_needs_no_rpc(self):
        with patch.object(image.subprocess,'check_output',return_value='{"status":"stopped"}'),patch.object(image,'LiveRPC') as rpc:
            self.assertEqual(image.inspect()['threads'],[]);rpc.assert_not_called()
    def test_idle_fork_reports_its_native_parent_for_process_matching(self):
        rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
        rpc.call.return_value={'data':[], 'nextCursor':None}
        thread={'id':'child','forkedFromId':'parent','updatedAt':10,'status':{'type':'idle'}}
        with patch.object(image.subprocess,'check_output',return_value='{"status":"running"}'), \
                patch.object(image,'LiveRPC',return_value=rpc),patch.object(workspace,'loaded',return_value=[thread]):
            self.assertEqual(image.inspect(),{'threads':['child'],'fork_parents':['parent'],'latest':10,'claude':[],'claude_background':[]})
    def test_activity_after_fencing_never_stops_daemon(self):
        with tempfile.TemporaryDirectory() as d,patch.object(image.handoff,'ROOT',Path(d)), \
                patch.object(image,'inspect',side_effect=[{'threads':['t'],'claude':[]},RuntimeError('new turn')]), \
                patch.object(workspace,'inventory',return_value={'sessions':[],'discussions':['t']}), \
                patch.object(workspace,'frozen_tuis',return_value=contextlib.nullcontext()),patch.object(image.subprocess,'run') as stop:
            with self.assertRaisesRegex(RuntimeError,'new turn'):image.checkpoint('a'*24)
            stop.assert_not_called()
    def test_empty_tmux_server_can_be_saved_without_codex(self):
        with patch.object(workspace,'tmux',return_value=Mock(returncode=1,stderr='no server running',stdout='')),patch.object(workspace,'LiveRPC') as rpc:
            value=workspace.inventory('a'*24,same_host=True,persist=False)
            self.assertEqual(value['sessions'],[]);rpc.assert_not_called()
    def test_checkpoint_keeps_only_the_open_fork_and_closes_its_loaded_parent(self):
        with tempfile.TemporaryDirectory() as d,patch.object(image.handoff,'ROOT',Path(d)), \
                patch.object(image,'inspect',return_value={'threads':['child','parent'],'fork_parents':['parent'],'claude':[],'claude_background':[]}), \
                patch.object(workspace,'inventory',return_value={'sessions':[],'discussions':['child']}), \
                patch.object(workspace,'frozen_tuis',return_value=contextlib.nullcontext()), \
                patch.object(image,'stop_daemon') as stop:
            self.assertEqual(image.checkpoint('a'*24)['workspace']['discussions'],['child'])
            stop.assert_called_once()
    def test_checkpoint_does_not_close_unrepresented_discussions(self):
        with tempfile.TemporaryDirectory() as d,patch.object(image.handoff,'ROOT',Path(d)), \
                patch.object(image,'inspect',return_value={'threads':['external']}), \
                patch.object(workspace,'inventory',return_value={'discussions':[]}),patch.object(image.subprocess,'run') as stop:
            with self.assertRaisesRegex(RuntimeError,'outside'):image.checkpoint('a'*24)
            stop.assert_not_called()

class MacCommands(unittest.TestCase):
    def setUp(self):
        import importlib.util
        root=Path(__file__).resolve().parents[2]
        sys.path.insert(0,str(root/'klokast-dev/lib'))
        spec=importlib.util.spec_from_file_location('air_update_mac',root/'klokast-dev/lib/platform_client.py')
        self.client=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.client)
    def test_rejects_other_roles_before_connecting(self):
        import argparse
        for target in ('boxa-print','boxb-air','boxa','air'):
            args=argparse.Namespace(instance='/fixture',target=target,resume=None,status=False,detach=True)
            with patch.object(self.client,'declared_boxes',return_value=['boxa']),patch.object(self.client,'controller') as controller:
                with self.assertRaisesRegex(ValueError,'BOX-ROLE target on a declared box'):self.client.image_update(args)
                controller.assert_not_called()
    def test_each_common_role_selects_the_same_controller_job(self):
        import argparse
        for role in self.client.ROLES:
            args=argparse.Namespace(instance='/fixture',target='box-a-'+role,resume=None,status=False,detach=True)
            with patch.object(self.client,'declared_boxes',return_value=['box-a']), \
                    patch.object(self.client,'controller',return_value='boxb'),patch.object(self.client,'remote',return_value={'state':'waiting'}) as remote:
                self.client.image_update(args)
            command=remote.call_args.args[2]
            self.assertTrue(command[0].endswith('/ansible/bin/vm-image-update'))
            self.assertEqual(command[1:6],['start','--box','box-a','--role',role])
    def test_resume_reuses_controller_operation(self):
        import argparse
        args=argparse.Namespace(instance='/fixture',target='boxa-air',resume='b'*24,status=False,detach=True)
        with patch.object(self.client,'declared_boxes',return_value=['boxa']), \
                patch.object(self.client,'controller',return_value='boxb'),patch.object(self.client,'remote',return_value={'state':'waiting'}) as remote:
            self.client.image_update(args)
        self.assertEqual(remote.call_args.args[0:2],('smith','boxb-ops'))
        self.assertEqual(remote.call_args.args[2][-2:],['--operation','b'*24])
    def test_status_does_not_start_a_job(self):
        import argparse
        args=argparse.Namespace(instance='/fixture',target='boxa-air',resume=None,status=True,detach=False)
        with patch.object(self.client,'declared_boxes',return_value=['boxa']), \
                patch.object(self.client,'controller',return_value='boxb'),patch.object(self.client,'remote',return_value={}) as remote:
            self.client.image_update(args)
        self.assertEqual(remote.call_args.args[2][1],'status')
    def instance(self,directory):
        value={'boxes':{'boxa':{'vpn-egress':{'clients':[]}},
                        'boxb':{'substrate':{'shared-guests':{'iot':{'runtime-state':'stopped'}}}},'boxc':{}},
               'controllers':{'active':'boxa','standby':'boxb'},'airunners':['boxb-air','cloud-ops']}
        (Path(directory)/'klokast-instance.json').write_text(json.dumps(value))
        return value
    def test_list_agrees_with_the_controller_target_check(self):
        import vm_service_runtime
        with tempfile.TemporaryDirectory() as directory:
            view={'instance':self.instance(directory)}
            rows={target:(state,note) for target,state,note in self.client.update_targets(directory)}
        for box in view['instance']['boxes']:
            for role in self.client.ROLES:
                try:expected=vm_service_runtime.target(view,box,role)
                except RuntimeError:expected=None
                listed=rows.get(box+'-'+role)
                if box+'-'+role=='boxa-ops':self.assertIn('active controller',listed[1])
                else:self.assertEqual(listed[0] if listed else None,expected,box+'-'+role)
        self.assertIn('declared stopped',rows['boxb-iot'][1])
    def test_list_prints_declared_targets_without_connecting(self):
        import contextlib,io
        with tempfile.TemporaryDirectory() as directory,patch.object(self.client,'remote') as remote:
            self.instance(directory);output=io.StringIO()
            with contextlib.redirect_stdout(output):self.assertEqual(self.client.main(['--instance',directory,'update','--list']),0)
            remote.assert_not_called()
        lines=output.getvalue().splitlines()
        self.assertEqual(lines[0].split(),['TARGET','DECLARED','NOTE'])
        self.assertIn('boxa-vpn-egress  running',lines);self.assertNotIn('boxc-air',output.getvalue())
        self.assertIn('  kk promote boxb-ops',lines)
    def test_missing_or_unknown_target_names_the_list_command(self):
        import contextlib,io
        for arguments,message in ((['update'],'List the accepted targets with: kk update --list'),
                                  (['update','--list','boxa-air'],'--list accepts no target')):
            error=io.StringIO()
            with contextlib.redirect_stderr(error),self.assertRaises(SystemExit):self.client.main(arguments)
            self.assertIn('usage: kk update',error.getvalue());self.assertIn(message,error.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            self.instance(directory)
            with self.assertRaisesRegex(ValueError,'kk update --list'):self.client.update_target(directory,'boxa-print')
    def promote(self,directory,*arguments):
        import contextlib,io
        output=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):code=self.client.main(['--instance',directory,'promote',*arguments])
        return code,output.getvalue()
    def promotable(self,directory):
        value=self.instance(directory);value.update(tailscale={'tailnet-dns-name':'example.ts.net'},airunners=['boxa-air','boxb-air'])
        value['schema-version']=1;(Path(directory)/'klokast-instance.json').write_text(json.dumps(value))
    def test_promote_switches_from_the_live_active_controller(self):
        done={'operation':'c'*24,'state':'complete','active':'boxb'}
        with tempfile.TemporaryDirectory() as directory,patch.object(self.client,'controller',return_value='boxa'), \
                patch.object(self.client,'remote',return_value=done) as remote:
            self.promotable(directory);code,output=self.promote(directory,'boxb-ops')
            self.assertEqual(code,0,output);self.assertEqual(remote.call_args.args[:2],('smith','boxa-ops'))
            command=remote.call_args.args[2]
            self.assertEqual(command[:6],['/home/smith/src/klokast/klokast-box/ansible/bin/ops-controller-ha','switchover',
                '--old-active','boxa','--new-active','boxb'])
            self.assertIn('kk promote boxb-ops --resume '+command[-1],output)
            remote.reset_mock();code,output=self.promote(directory,'boxa-ops')
            self.assertEqual(code,0);self.assertIn('already the active controller',output);remote.assert_not_called()
            # A resume does not need one live active controller.
            with patch.object(self.client,'controller',side_effect=RuntimeError('no active')):
                code,output=self.promote(directory,'boxb-ops','--resume','d'*24)
            self.assertEqual(code,0,output);self.assertEqual(remote.call_args.args[2][-2:],['--operation-id','d'*24])
            remote.return_value={'state':'complete','active':'boxa'}
            code,output=self.promote(directory,'boxb-ops');self.assertEqual(code,2);self.assertIn('not verified',output)
    def test_promote_moves_the_workspace_from_its_published_placement(self):
        ready={'version':1,'operation':'e'*24,'phase':'ready','current':'boxa','origin':'boxb','destination':'boxa'}
        done={'state':'complete','operation':'f'*24,'current':'boxb','discussions':2}
        with tempfile.TemporaryDirectory() as directory,patch.object(self.client,'controller',return_value='boxa'), \
                patch.object(self.client,'remote',return_value=done) as remote, \
                patch.object(self.client.air,'probe',return_value={'placement':ready}):
            self.promotable(directory);code,output=self.promote(directory,'boxb-air')
            self.assertEqual(code,0,output);self.assertEqual(remote.call_args.args[:2],('smith','boxa-ops'))
            self.assertEqual(remote.call_args.args[2][:6],['/home/smith/src/klokast/klokast-box/ansible/bin/air-workspace','move',
                '--from','boxa','--to','boxb'])
            remote.reset_mock();code,output=self.promote(directory,'boxa-air')
            self.assertEqual(code,0);self.assertIn('already on boxa-air',output);remote.assert_not_called()
            moving=dict(ready,operation='a'*24,phase='draining',origin='boxa',destination='boxb')
            with patch.object(self.client.air,'probe',return_value={'placement':moving}):
                code,output=self.promote(directory,'boxb-air')
                self.assertEqual(code,2);self.assertIn('kk promote boxb-air --resume '+'a'*24,output);remote.assert_not_called()
                code,output=self.promote(directory,'boxb-air','--resume','a'*24)
                self.assertEqual(code,0,output);self.assertEqual(remote.call_args.args[2][2:],['--from','boxa','--to','boxb','--operation','a'*24])
                code,output=self.promote(directory,'boxa-air','--resume','a'*24);self.assertEqual(code,2)
            with patch.object(self.client.air,'probe',return_value={'error':'down'}):
                code,output=self.promote(directory,'boxb-air');self.assertEqual(code,2);self.assertIn('kk air --status',output)
    def test_promote_rejects_other_targets_and_lists_observed_state(self):
        ready={'operation':'e'*24,'phase':'ready','current':'boxb'}
        with tempfile.TemporaryDirectory() as directory,patch.object(self.client,'controller',return_value='boxa'), \
                patch.object(self.client,'remote') as remote,patch.object(self.client.air,'probe',return_value={'placement':ready}):
            self.promotable(directory)
            for target in ('boxc-ops','boxc-air','boxa-dmz','boxa'):
                code,output=self.promote(directory,target);self.assertEqual(code,2);self.assertIn('kk promote --list',output)
            with self.assertRaises(SystemExit):self.promote(directory)
            code,output=self.promote(directory,'--list');self.assertEqual(code,0,output);remote.assert_not_called()
        self.assertEqual([line.split() for line in output.splitlines()[1:6]],[['TARGET','ROLE','CURRENT'],
            ['boxa-ops','controller','active'],['boxb-ops','controller','standby'],
            ['boxa-air','workspace','standby'],['boxb-air','workspace','active']])

class ControllerJobs(unittest.TestCase):
    def setUp(self):
        import air_image_update,vm_image_update
        self.job=vm_image_update;self.fresh=air_image_update
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        for obj,name,kwargs in ((self.job.cycle,'ROOT',{'new':Path(temporary.name)}),
                (self.job.inputs,'update_checkout',{'return_value':'e'*40}),
                (self.job.cycle.source,'require_controller',{'return_value':None}),
                (self.job.cycle.source,'snapshot',{'return_value':{'instance':{'airunners':['boxa-air','boxb-air'],'controllers':{'active':'boxa','standby':'boxb'},
                    'boxes':{'boxa':{},'boxb':{'substrate':{'shared-guests':{'iot':{'runtime-state':'stopped'}}}}}}}})):
            m=patch.object(obj,name,**kwargs);m.start();self.addCleanup(m.stop)
    def test_status_reports_the_published_stage_only_while_updating(self):
        import platform_maintenance
        directory=self.job.cycle.ROOT/('a'*24);directory.mkdir()
        record=dict(operation='a'*24,box='boxa',state='updating',reason='preparing and verifying the replacement image',created_at=1)
        (directory/'image-job.json').write_text(json.dumps(record))
        with patch.dict(os.environ,KLOKAST_PROGRESS=str(directory/'progress.json')):
            platform_maintenance.progress('image build: assemble the scriptless builder boot environment')
        self.assertEqual(self.job.status('boxa')['progress'],'image build: assemble the scriptless builder boot environment')
        (directory/'image-job.json').write_text(json.dumps(dict(record,state='complete')))
        self.assertNotIn('progress',self.job.status('boxa','a'*24))

    def test_target_mismatch_cannot_overwrite_an_operation(self):
        with patch.object(self.job.subprocess,'Popen'):
            self.job.start('boxa','a'*24)
            with self.assertRaisesRegex(RuntimeError,'another update target'):self.job.start('boxb','a'*24)
            with self.assertRaisesRegex(RuntimeError,'another update target'):self.job.start('boxa','a'*24,'dmz')
    def test_request_uses_the_common_target_authority(self):
        with patch.object(self.job.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'hand off authority first'):self.job.start('boxa','a'*24,'ops')
            with self.assertRaisesRegex(RuntimeError,'declared stopped'):self.job.start('boxb','a'*24,'iot')
            with self.assertRaisesRegex(RuntimeError,'not declared'):self.job.start('boxc','a'*24,'dmz')
            launch.assert_not_called()
            record=self.job.start('boxb','b'*24,'ops')
        self.assertEqual((record['role'],record['reason']),('ops','checking that no other update work is ongoing'))
        self.assertEqual(launch.call_args.args[0][2:],['worker','--box','boxb','--role','ops','--operation','b'*24])
    def test_roles_on_one_box_have_separate_jobs(self):
        with patch.object(self.job.subprocess,'Popen'):
            self.job.start('boxa','a'*24,'dmz')
        self.assertEqual(self.job.status('boxa')['state'],'absent')
        self.assertEqual(self.job.status('boxa',role='dmz')['operation'],'a'*24)
    def test_completed_job_is_not_relaunched(self):
        directory=self.job.cycle.ROOT/('a'*24);directory.mkdir()
        (directory/'image-job.json').write_text(json.dumps({'state':'complete','box':'boxa','created_at':1}))
        with patch.object(self.job.subprocess,'Popen') as launch:
            self.assertEqual(self.job.start('boxa','a'*24)['state'],'complete');launch.assert_not_called()
    def test_repeated_request_joins_the_existing_job(self):
        import fcntl
        directory=self.job.cycle.ROOT/('a'*24);directory.mkdir()
        record=dict(state='updating',box='boxa',operation='a'*24,created_at=1)
        (directory/'image-job.json').write_text(json.dumps(record))
        with (directory/'worker.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with patch.object(self.job.subprocess,'Popen') as launch:
                self.assertEqual(self.job.start('boxa','b'*24),record)
                launch.assert_not_called()
                self.job.inputs.update_checkout.assert_not_called()
        self.assertFalse((self.job.cycle.ROOT/('b'*24)).exists())

    def test_new_worker_starts_after_controller_source_update(self):
        with patch.object(self.job.subprocess,'Popen',side_effect=lambda *a,**k:
                self.job.inputs.update_checkout.assert_called_once_with(self.job.cycle.source.REPO)):
            self.job.start('boxa','a'*24)

    def test_source_failure_does_not_launch_or_replace_a_failed_job_record(self):
        directory=self.job.cycle.ROOT/('a'*24);directory.mkdir()
        path=directory/'image-job.json'
        record=dict(state='failed',box='boxa',operation='a'*24,created_at=1,error='old failure')
        path.write_text(json.dumps(record))
        self.job.inputs.update_checkout.side_effect=RuntimeError('preserve local changes')
        with patch.object(self.job.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'preserve local changes'):
                self.job.start('boxa','a'*24)
            launch.assert_not_called()
        self.assertEqual(json.loads(path.read_text()),record)

    def test_versions_compare_numerically_and_reject_unverified_channels(self):
        self.assertGreater(self.fresh.version('0.170.0'),self.fresh.version('0.99.9'))
        with self.assertRaisesRegex(RuntimeError,'unsupported'):self.fresh.version('0.170.0-alpha')

    def test_pre_shutdown_freshness_check_stops_when_metadata_is_rate_limited(self):
        import github_metadata
        with patch.object(github_metadata,'fetch',side_effect=RuntimeError('GitHub metadata is rate limited')) as fetch, \
                patch.object(self.fresh.subprocess,'run') as remote:
            with self.assertRaisesRegex(RuntimeError,'rate limited'):
                self.fresh.require_current('boxa','1.2.3')
        fetch.assert_called_once_with('codex-stable');remote.assert_not_called()

class StoppedDaemon(unittest.TestCase):
    def setUp(self):
        mock=patch.object(image,'inspect_claude',return_value=[]);mock.start();self.addCleanup(mock.stop)

    def test_connection_refused_with_writers_is_busy(self):
        import subprocess
        with patch.object(image.handoff,'codex_binary',return_value='/fixture/codex'), \
                patch.object(image.subprocess,'check_output',side_effect=subprocess.CalledProcessError(1,['codex'])), \
                patch.object(workspace,'live_writers',return_value={'thread'}):
            with self.assertRaisesRegex(RuntimeError,'writers exist'):image.inspect()
    def test_connection_refused_without_writers_allows_offline_checkpoint(self):
        import subprocess
        with patch.object(image.handoff,'codex_binary',return_value='/fixture/codex'), \
                patch.object(image.subprocess,'check_output',side_effect=[subprocess.CalledProcessError(1,['codex']),'codex-cli 0.166.0']), \
                patch.object(workspace,'live_writers',return_value=set()):
            self.assertEqual(image.inspect(),{'threads':[],'latest':0,'claude':[],'claude_background':[]})


class WorkspaceProtocol(unittest.TestCase):
    def test_native_status_does_not_pollute_checkpoint_json(self):
        import os, subprocess
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            cli=root/'codex'
            cli.write_text('#!/usr/bin/env python3\nimport json\nprint(json.dumps(dict(status="stopped")))\n')
            cli.chmod(0o755)
            code=f"""
import json,contextlib
from pathlib import Path
from unittest.mock import patch
import air_image_workspace as image
with patch.object(image.handoff,'ROOT',Path({str(root)!r})), patch.object(image.handoff,'codex_binary',return_value={str(cli)!r}), patch.object(image,'inspect',return_value={{'threads':[],'claude':[]}}), patch.object(image.workspace,'inventory',return_value={{'sessions':[],'discussions':[]}}), patch.object(image.workspace,'frozen_tuis',return_value=contextlib.nullcontext()):
    print(json.dumps(image.checkpoint('a'*24)))
"""
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'lib'))
            result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,check=True,env=env)
            self.assertEqual(json.loads(result.stdout)['phase'],'closed')

    def test_failed_native_stop_is_reported(self):
        with patch.object(image.subprocess,'run',return_value=Mock(returncode=1,stderr='native failure')):
            with self.assertRaisesRegex(RuntimeError,'cannot stop.*native failure'):
                image.stop_daemon('/fixture/codex')


class PendingImageJob(unittest.TestCase):
    def setUp(self):
        # The worker names its progress file in its own environment.
        context=patch.dict(os.environ);context.start();self.addCleanup(context.stop)

    def test_pending_replacement_resumes_without_requiring_an_accepted_generation(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            (directory/'update-air.json').write_text(json.dumps({'vm_operation':'a'*24}))
            record={'state':'failed','operation':'b'*24,'box':'boxa','created_at':1}
            with patch.object(job,'status',return_value=record),patch.object(job.cycle,'ROOT',Path(d)), \
                    patch.object(job.cycle.source,'require_controller'),patch.object(signal,'signal'),patch.object(signal,'alarm'), \
                    patch.object(job.cycle.runtime,'vm_update_installation_lock',return_value=contextlib.nullcontext()), \
                    patch.object(job.cycle.service,'step',return_value={'installed':None,'pending':[{'operation_id':'a'*24}]}) as probe, \
                    patch.object(job.cycle,'step',return_value={'state':'complete'}) as resume:
                job.worker('boxa','b'*24)
            probe.assert_not_called()
            resume.assert_called_once_with('update','boxa','b'*24,'air',wait_for_lock=True)
            self.assertEqual(json.loads((directory/'image-job.json').read_text())['state'],'complete')

    def test_worker_waits_for_the_shared_lock_and_publishes_the_reason(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            path=directory/'image-job.json'
            record=dict(state='waiting',operation='b'*24,box='boxa',created_at=1)
            path.write_text(json.dumps(record))
            def lock(*,wait=False):
                self.assertTrue(wait)
                saved=json.loads(path.read_text())
                self.assertEqual(saved['reason'],'checking that no other update work is ongoing')
                self.assertEqual(saved['checks'],['boxa-air tmux sessions are detached: OK','Discussions on boxa-air are not active: OK'])
                return contextlib.nullcontext()
            with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'), \
                    patch.object(signal,'signal'),patch.object(signal,'alarm'), \
                    patch.object(job.air_maintenance,'remote',return_value={'idle':True}), \
                    patch.object(job.cycle.runtime,'vm_update_installation_lock',side_effect=lock), \
                    patch.object(job.cycle.service,'step',return_value={'installed':{'image':'old'}}), \
                    patch.object(job.cycle,'step',side_effect=lambda *a,**k:(
                        self.assertEqual(os.environ['KLOKAST_PROGRESS'],str(directory/'progress.json')) or
                        {'state':'complete','workspace':'not restored: checkpoint missing'})) as update:
                job.worker('boxa','b'*24)
            update.assert_called_once_with('update','boxa','b'*24,'air',wait_for_lock=True)
            self.assertEqual(json.loads(path.read_text())['state'],'complete')
            self.assertEqual(json.loads(path.read_text())['reason'],'update verified; tmux workspace not restored: checkpoint missing')
            self.assertEqual(json.loads(path.read_text())['checks'][-1],'No other update work is ongoing: OK')

    def test_worker_names_the_blocking_activity(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            path=directory/'image-job.json'
            path.write_text(json.dumps(dict(state='waiting',operation='b'*24,box='boxa',created_at=1)))
            seen=[]
            def sleep(_seconds):seen.append(json.loads(path.read_text()))
            replies=[{'idle':False,'attached':1,'busy_pids':[]},{'idle':False,'attached':0,'busy_pids':[7]},
                     {'idle':False,'attached':0,'busy_pids':[7,8],'reason':'Claude Code discussion is active or needs attention: PID 8 is waiting'},
                     {'idle':True}]
            with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'), \
                    patch.object(signal,'signal'),patch.object(signal,'alarm'),patch.object(job.time,'sleep',side_effect=sleep), \
                    patch.object(job.air_maintenance,'remote',side_effect=replies), \
                    patch.object(job.cycle.runtime,'vm_update_installation_lock',return_value=contextlib.nullcontext()), \
                    patch.object(job.cycle.service,'step',return_value={'installed':{'image':'old'}}), \
                    patch.object(job.cycle,'step',return_value={'state':'complete'}):
                job.worker('boxa','b'*24)
            self.assertEqual([(r['reason'],r['checks']) for r in seen],[
                ('boxa-air tmux sessions are attached: waiting',[]),
                ('Discussions or other work on boxa-air are active: waiting',['boxa-air tmux sessions are detached: OK']),
                ('Claude Code discussion is active or needs attention: PID 8 is waiting: waiting',['boxa-air tmux sessions are detached: OK'])])
            self.assertEqual(seen[-1]['busy_processes'],0)
            self.assertEqual(json.loads(path.read_text())['checks'],['boxa-air tmux sessions are detached: OK',
                'Discussions on boxa-air are not active: OK','No other update work is ongoing: OK'])

    def test_new_job_still_cannot_start_an_implicit_migration(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            record={'state':'waiting','operation':'b'*24,'box':'boxa','created_at':1}
            with patch.object(job,'status',return_value=record),patch.object(job.cycle,'ROOT',Path(d)), \
                    patch.object(job.cycle.source,'require_controller'),patch.object(signal,'signal'),patch.object(signal,'alarm'), \
                    patch.object(job.air_maintenance,'remote',return_value={'idle':True}), \
                    patch.object(job.cycle.runtime,'vm_update_installation_lock',return_value=contextlib.nullcontext()), \
                    patch.object(job.cycle.service,'step',return_value={'installed':None}),patch.object(job.cycle,'step') as update:
                with self.assertRaisesRegex(RuntimeError,'explicit retained-data migration'):job.worker('boxa','b'*24)
            update.assert_not_called()

    def test_service_role_skips_runner_checks_and_reports_an_unchanged_image(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            path=directory/'image-job.json'
            path.write_text(json.dumps(dict(state='waiting',operation='b'*24,box='boxa',role='dmz',created_at=1)))
            with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'), \
                    patch.object(signal,'signal'),patch.object(signal,'alarm'),patch.object(job.air_maintenance,'remote') as runner, \
                    patch.object(job.cycle.runtime,'vm_update_installation_lock',return_value=contextlib.nullcontext()), \
                    patch.object(job.cycle.service,'step') as probe, \
                    patch.object(job.cycle,'step',return_value={'state':'complete','replaced':False,'image':'old'}) as update:
                job.worker('boxa','b'*24,'dmz')
                runner.assert_not_called();probe.assert_not_called()
                update.assert_called_once_with('update','boxa','b'*24,'dmz',wait_for_lock=True)
                saved=json.loads(path.read_text())
                self.assertEqual((saved['state'],saved['reason'],saved['checks']),
                    ('complete','image unchanged; health checks passed',['No other update work is ongoing: OK']))
                update.return_value={'state':'skipped','reason':'declared stopped'}
                with self.assertRaisesRegex(RuntimeError,'boxa-dmz update is skipped: declared stopped'):job.worker('boxa','b'*24,'dmz')
            self.assertEqual(json.loads(path.read_text())['state'],'failed')
