"""Native activity and wrapper decisions must preserve active work."""
import contextlib
import itertools
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import ANY,Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import live_tmux_guard  # noqa: F401  Before the workspace code.
import air_image_workspace as image
import codex_workspace as workspace

INSPECT_CLAUDE=image.inspect_claude

class NativeActivity(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        for name,value in (('ROOT',Path(temporary.name)),):
            mock=patch.object(image.workspace,name,value);mock.start();self.addCleanup(mock.stop)
        mock=patch.object(image.workspace,'codex_binary',return_value='/test/codex');mock.start();self.addCleanup(mock.stop)
        mock=patch.object(image,'inspect_claude',return_value=[]);mock.start();self.addCleanup(mock.stop)
        # The work check reads the live process table; its own tests give it fixed rows.
        mock=patch.object(image,'inspect_work');mock.start();self.addCleanup(mock.stop)

    def test_image_selection_links_the_app_server_package_and_no_home_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'home/agent/.codex';home.mkdir(parents=True)
            (root/'klokast-codex.json').write_text('{"version": "0.163.0"}')
            real=Path
            with patch.object(image.workspace,'HOME',home),patch.object(image,'stop_daemon') as stop, \
                    patch.object(image,'Path',side_effect=lambda v:root/'klokast-codex.json' if v=='/etc/klokast-codex.json' else real(v)):
                image.select_image_package()
            stop.assert_called_once_with('/usr/local/bin/codex')
            self.assertEqual(str((home/'packages/app-server-daemon/current').readlink()),'/opt/codex')
            self.assertFalse((root/'home/agent/.local').exists())
            self.assertFalse(json.loads((home/'app-server-daemon/settings.json').read_text())['updater']['autoUpdateEnabled'])

    def test_active_approval_and_error_threads_block_shutdown(self):
        panes=subprocess.CompletedProcess([],0,'9\tmain:6\tcodex\t/home/agent/src\tReview | src\t1\n','')
        for status,activity in (({'type':'active','activeFlags':[]},'runs a turn'),
                                ({'type':'active','activeFlags':['waitingOnApproval']},'waits for an approval'),
                                ({'type':'systemError'},'stopped on a system error'),({'type':'notLoaded'},'is not loaded')):
            rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
            thread={'id':'t','name':'Review','cwd':'/home/agent/src','status':status}
            with patch.object(image.subprocess,'check_output',return_value='{"status":"running"}'), \
                    patch.object(workspace,'tmux',return_value=panes), \
                    patch.object(image,'LiveRPC',return_value=rpc),patch.object(workspace,'loaded',return_value=[thread]):
                with self.assertRaisesRegex(RuntimeError,'active or needs attention: tmux window main:6 "codex" '+activity):
                    image.inspect()

    def test_claude_refusal_names_its_tmux_window(self):
        rows={8:dict(parent=5),5:dict(parent=1)}
        panes=subprocess.CompletedProcess([],0,'5\tmain:2\tclaude\t/home/agent\ttitle\t1\n','')
        clients={8:{'session':'s','status':'waiting','cwd':'/home/agent','kind':'interactive'}}
        with patch('air_workspace.processes',return_value=rows),patch('air_workspace.claude_clients',return_value=clients), \
                patch.object(workspace,'tmux',return_value=panes):
            with self.assertRaisesRegex(RuntimeError,'PID 8 in tmux window main:2 "claude" is waiting'):
                INSPECT_CLAUDE()
    def test_claude_refusal_names_a_detached_session_without_status(self):
        rows={8:dict(parent=5),5:dict(parent=1)}
        panes=subprocess.CompletedProcess([],0,'5\t1:2\tclaude\t/home/agent\ttitle\t0\n','')
        clients={8:{'session':'s','status':'unknown','cwd':'/home/agent','kind':'interactive'}}
        with patch('air_workspace.processes',return_value=rows),patch('air_workspace.claude_clients',return_value=clients), \
                patch.object(workspace,'tmux',return_value=panes),patch.object(workspace,'claude_transcript',return_value=True):
            with self.assertRaisesRegex(RuntimeError,r'PID 8 in tmux window 1:2 "claude" of detached session "1" '
                                        r'\(tmux attach -t 1\) records no status yet and has a conversation'):
                INSPECT_CLAUDE()
    def test_background_jobs_block_shutdown(self):
        rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
        rpc.call.return_value={'data':[{'id':'job'}]}
        with patch.object(image.subprocess,'check_output',return_value='{"status":"running"}'), \
                patch.object(image,'LiveRPC',return_value=rpc),patch.object(workspace,'loaded',return_value=[{'id':'t','updatedAt':10,'status':{'type':'idle'}}]):
            with self.assertRaisesRegex(RuntimeError,'background terminals'):image.inspect()
    def test_no_daemon_needs_no_rpc(self):
        with patch.object(image.subprocess,'check_output',return_value='{"status":"stopped"}'),patch.object(image,'LiveRPC') as rpc:
            self.assertEqual(image.inspect()['threads'],[]);rpc.assert_not_called()
        # Every probe also refuses agent work outside a turn.
        image.inspect_work.assert_called_once_with({'threads':[],'latest':0})
    def test_idle_fork_reports_its_native_parent_for_process_matching(self):
        rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
        rpc.call.return_value={'data':[], 'nextCursor':None}
        thread={'id':'child','forkedFromId':'parent','updatedAt':10,'status':{'type':'idle'}}
        with patch.object(image.subprocess,'check_output',return_value='{"status":"running"}'), \
                patch.object(image,'LiveRPC',return_value=rpc),patch.object(workspace,'loaded',return_value=[thread]):
            self.assertEqual(image.inspect(),{'threads':['child'],'fork_parents':['parent'],'latest':10,'claude':[],'claude_background':[]})
    def test_activity_after_fencing_never_stops_daemon(self):
        with tempfile.TemporaryDirectory() as d,patch.object(image.workspace,'ROOT',Path(d)), \
                patch.object(image,'inspect',side_effect=[{'threads':['t'],'claude':[]},RuntimeError('new turn')]), \
                patch.object(workspace,'inventory',return_value={'sessions':[],'discussions':['t']}), \
                patch.object(workspace,'frozen_tuis',return_value=contextlib.nullcontext()),patch.object(image.subprocess,'run') as stop:
            with self.assertRaisesRegex(RuntimeError,'new turn'):image.checkpoint('a'*24)
            stop.assert_not_called()
    def test_empty_tmux_server_can_be_saved_without_codex(self):
        with patch.object(workspace,'tmux',return_value=Mock(returncode=1,stderr='no server running',stdout='')),patch.object(workspace,'LiveRPC') as rpc:
            value=workspace.inventory('a'*24)
            self.assertEqual(value['sessions'],[]);rpc.assert_not_called()
    def test_checkpoint_keeps_only_the_open_fork_and_closes_its_loaded_parent(self):
        with tempfile.TemporaryDirectory() as d,patch.object(image.workspace,'ROOT',Path(d)), \
                patch.object(image,'inspect',return_value={'threads':['child','parent'],'fork_parents':['parent'],'claude':[],'claude_background':[]}), \
                patch.object(workspace,'inventory',return_value={'sessions':[],'discussions':['child']}), \
                patch.object(workspace,'frozen_tuis',return_value=contextlib.nullcontext()), \
                patch.object(image,'stop_daemon') as stop:
            self.assertEqual(image.checkpoint('a'*24)['workspace']['discussions'],['child'])
            stop.assert_called_once()
    def test_checkpoint_does_not_close_unrepresented_discussions(self):
        with tempfile.TemporaryDirectory() as d,patch.object(image.workspace,'ROOT',Path(d)), \
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
            args=argparse.Namespace(instance='/fixture',target=target,detach=True)
            with patch.object(self.client,'declared_boxes',return_value=['boxa']),patch.object(self.client,'controller') as controller:
                with self.assertRaisesRegex(ValueError,'BOX-ROLE target on a declared box'):self.client.image_update(args)
                controller.assert_not_called()
    def test_each_common_role_selects_the_same_controller_job(self):
        import argparse
        for role in self.client.ROLES:
            args=argparse.Namespace(instance='/fixture',target='box-a-'+role,detach=True)
            with patch.object(self.client,'declared_boxes',return_value=['box-a']), \
                    patch.object(self.client,'controller',return_value='boxb'),patch.object(self.client,'remote',return_value={'state':'waiting'}) as remote:
                self.client.image_update(args)
            command=remote.call_args.args[2]
            self.assertTrue(command[0].endswith('/ansible/bin/vm-image-update'))
            self.assertEqual(command[1:],['start','--box','box-a','--role',role])
    def test_repeated_request_names_the_job_that_the_controller_selected(self):
        import argparse,contextlib,io
        args=argparse.Namespace(instance='/fixture',target='boxa-air',detach=True);at=time.time()-7
        for answer,line in (({'request':'restarted','previous':'failed'},'boxb-ops restarting the failed boxa-air update, job '+'b'*24),
                            ({'request':'restarted','previous':'interrupted'},'boxb-ops restarting the interrupted boxa-air update, job '+'b'*24),
                            ({'request':'joined'},'boxb-ops already runs the boxa-air update, job '+'b'*24),
                            ({'request':'new'},'boxb-ops starting the boxa-air update, job '+'b'*24)):
            with patch.object(self.client,'declared_boxes',return_value=['boxa']),patch.object(self.client,'controller',return_value='boxb'), \
                    patch.object(self.client,'remote',return_value=dict(answer,state='waiting',operation='b'*24,requested_at=at)) as remote, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.client.image_update(args)
            self.assertEqual(remote.call_args.args[0:2],('smith','boxb-ops'));self.assertNotIn('--operation',remote.call_args.args[2])
            # The controller clock times the request, like each stage that follows it.
            self.assertTrue(output.getvalue().startswith(time.strftime('%H:%M:%S ',time.gmtime(at))))
            first=output.getvalue().splitlines()[0][9:]
            self.assertEqual(first,line+'. Ctrl-C or a closed terminal does not stop the job. Resume following it: kk update boxa-air. '
                             'Stop the job before it changes boxa-air: kk update boxa-air --cancel')
    def test_cancel_requests_the_waiting_job_and_excludes_resume(self):
        import contextlib,io
        record={'state':'cancelled','operation':'b'*24,'reason':'cancelled on request before the update changed the VM'}
        with patch.object(self.client,'declared_boxes',return_value=['boxa']),patch.object(self.client,'controller',return_value='boxb'), \
                patch.object(self.client,'remote',return_value=record) as remote,contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.client.main(['--instance','/fixture','update','boxa-air','--cancel']),0)
        self.assertEqual(remote.call_args.args[2][1:],['cancel','--box','boxa','--role','air'])
        self.assertIn('Cancelled: cancelled on request before the update changed the VM',output.getvalue())
        error=io.StringIO()
        with contextlib.redirect_stderr(error),self.assertRaises(SystemExit):
            self.client.main(['update','boxa-air','--cancel','--detach'])
        self.assertIn('not allowed with argument',error.getvalue())
        error=io.StringIO()
        with contextlib.redirect_stderr(error),self.assertRaises(SystemExit):
            self.client.main(['update','boxa-air','--resume','b'*24])
        self.assertIn('unrecognized arguments: --resume',error.getvalue())
    def test_cancel_of_a_running_job_follows_it_until_it_stops(self):
        import contextlib,io
        running={'state':'updating','operation':'b'*24,'progress':'image build: run the boot tests','cancel_requested':True}
        stopped={'state':'cancelled','operation':'b'*24,'reason':'cancelled on request before the update changed the VM'}
        with patch.object(self.client,'declared_boxes',return_value=['boxa']),patch.object(self.client,'controller',return_value='boxb'), \
                patch.object(self.client,'remote',side_effect=[running,stopped]) as remote,patch.object(self.client.time,'sleep'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.client.main(['--instance','/fixture','update','boxa-dmz','--cancel']),0)
        self.assertEqual(remote.call_args_list[0].args[2][1:],['cancel','--box','boxa','--role','dmz'])
        self.assertIn('Cancel accepted. The current stage ends first; then the job stops before it changes boxa-dmz.',output.getvalue())
        self.assertIn('Image build: run the boot tests; cancel requested: the job stops before it changes boxa-dmz',output.getvalue())
        self.assertIn('Cancelled: cancelled on request before the update changed the VM',output.getvalue())
        self.assertIn('Request it again: kk update boxa-dmz',output.getvalue())
    def instance(self,directory):
        value={'boxes':{'boxa':{'vpn-egress':{'clients':[]}},
                        'boxb':{'substrate':{'shared-guests':{'iot':{'runtime-state':'stopped'}}}},'boxc':{}},
               'controllers':['boxa','boxb'],'airunners':['boxb-air','cloud-ops']}
        (Path(directory)/'klokast-instance.json').write_text(json.dumps(value))
        return value
    def test_missing_or_unknown_target_names_the_status_command(self):
        import contextlib,io
        for arguments,message in ((['update'],'required: BOX-ROLE'),(['promote'],'List the machines with: kk status'),
                                  (['update','boxa-air','--list'],'unrecognized arguments: --list'),
                                  (['update','boxa-air','--status'],'unrecognized arguments: --status'),
                                  (['promote','--list'],'unrecognized arguments: --list')):
            error=io.StringIO()
            with contextlib.redirect_stderr(error),self.assertRaises(SystemExit):self.client.main(arguments)
            self.assertIn(message,error.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            self.instance(directory)
            with self.assertRaisesRegex(ValueError,'kk status'):self.client.update_target(directory,'boxa-print')
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
    def test_promote_accepts_only_a_declared_controller(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(self.client,'controller',return_value='boxa'), \
                patch.object(self.client,'remote') as remote,patch.object(self.client.air,'probe') as probe:
            self.promotable(directory)
            # Each runner keeps its own workspace, so no runner is promoted.
            for target in ('boxc-ops','boxb-air','boxa-air','boxa-dmz','boxa'):
                code,output=self.promote(directory,target);self.assertEqual(code,2);self.assertIn('List the machines with: kk status',output)
            with self.assertRaises(SystemExit):self.promote(directory)
            remote.assert_not_called();probe.assert_not_called()
    def test_applications_show_vm_names_box_placement_and_only_usable_clients(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'klokast-instance.json'
            value={'boxes':{'k002':{},'k001':{}},'apps':{
                'print-server':{'desired-state':'present','placement':{'mode':'multi-box','boxes':['k001']}},
                'music':{'desired-state':'absent'},
                'exit-node':{'desired-state':'present','placement':{'mode':'multi-box','boxes':['k001']}}}}
            path.write_text(json.dumps(value))
            def show():
                output=io.StringIO()
                with contextlib.redirect_stdout(output):self.client.show_applications(directory)
                return [line.split() for line in output.getvalue().splitlines() if not line.startswith('- ')]
            self.assertEqual(show()[:5],[['NAME','k001','k002','COMMAND'],[],
                ['exit','present','absent','-'],['music','absent','absent','-'],['print','present','absent','-']])
            # An absent app cannot offer its on-disk client or a help command.
            output=io.StringIO()
            with contextlib.redirect_stdout(output):self.client.target_status(directory,'music')
            self.assertIn('MacBook command: none',output.getvalue());self.assertNotIn('kk app music',output.getvalue())
            # The short display name also selects the application details.
            with patch.object(self.client,'remote') as remote,contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.client.target_status(directory,'print'),0)
            remote.assert_not_called();self.assertIn('Application: print-server',output.getvalue())
            for placement,states in (({'mode':'single-box','box':'k002'},['absent','present']),
                                     ({'mode':'multi-box','boxes':['k001','k002']},['present','present']),
                                     ({'mode':'active-passive','active':'k002','passive':'k001'},['passive','present'])):
                value['apps']['music']={'desired-state':'present','placement':placement}
                path.write_text(json.dumps(value))
                self.assertEqual(show()[3],['music',*states,'kk','app','music'])

    def test_machine_groups_sort_by_package_count_then_role_and_target(self):
        import machine_status
        counts={'dom0':13,'router':16,'bak':22,'dmz':22,'iot':22,'vpn-egress':25,'ops':37,'air':48}
        groups=[['boxa-'+role,'boxb-'+role] for role in ('dom0','router')]
        groups.append(['boxa-bak','boxb-bak','boxa-dmz','boxb-dmz','boxa-iot','boxb-iot'])
        groups.extend([['boxa-'+role,'boxb-'+role] for role in ('vpn-egress','ops','air')])
        rows=[]
        for role,count in counts.items():
            for box in ('boxb','boxa'):
                row=machine_status.summary({'target':box+'-'+role,'state':'reachable','packages':[{}]*count})
                if box=='boxb' and role=='ops':row['packages']=36
                if box=='boxa' and role=='iot':
                    row['state']='unreachable';row['error']='no answer';del row['packages']
                rows.append(row)
        def render(rows):
            output=io.StringIO()
            with contextlib.redirect_stdout(output):issues=self.client.show_machines({'machines':rows})
            self.assertEqual(issues,['boxa-iot is unreachable (no answer).'])
            blocks=output.getvalue().split('\n\n')
            self.assertEqual(blocks[0].split()[0],'TARGET')
            self.assertEqual([[line.split()[0] for line in block.splitlines() if not line.startswith('- ')]
                              for block in blocks[1:]],groups)
            self.assertIn('- boxa-iot is unreachable. Cause: no answer',output.getvalue())
        render(rows)
        render(list(reversed(rows)))
        # Counts, rather than a fixed role list, determine group order.
        for row in rows:
            if row['role']=='air':row['packages']=1
        groups.insert(0,groups.pop())
        render(rows)
        # An entire group without counts stays visible at the end.
        for row in rows:
            if row['role']=='router':del row['packages']
        groups.append(groups.pop(2))
        render(rows)

    def test_status_summarizes_controllers_runners_and_unfinished_operations(self):
        import contextlib,io
        guard=['/usr/local/sbin/klokast-controller-guard','--status','--json']
        base='/home/smith/src/klokast/klokast-box/ansible/bin/'
        answers={('boxa-ops',tuple(guard)):{'hostname':'boxa-ops','active':True,'role':'active','active_box':'boxa'},
                 ('boxb-ops',tuple(guard)):{'hostname':'boxb-ops','active':False,'role':'standby','active_box':'boxa'},
                 ('boxa-ops',(base+'promote-status','--last-switch')):{'completed_at':1762935300},
                 ('boxb-ops',(base+'promote-status','--last-switch')):{'completed_at':None},
                 ('boxa-ops',(base+'promote-status',)):[],('boxb-ops',(base+'promote-status',)):[],
                 ('boxa-ops',(base+'vm-image-update','list')):[],
                 ('boxa-ops',(base+'ops-controller-nightly','--status')):{'state':'complete','started':'2026-11-14T03:00:01Z',
                     'finished':'2026-11-14T04:12:09Z','log':'/var/lib/klokast/ops-nightly/x.log'},
                 ('boxb-ops',(base+'ops-controller-nightly','--status')):{'state':'skipped','reason':'verified standby','box':'boxb',
                     'started':'2026-11-14T03:00:00Z','finished':'2026-11-14T03:00:04Z'},
                 ('boxa-ops',(base+'machine-status','list')):{'observed_at':1794560000,'machines':[
                     {'target':'boxa-dom0','declared':'running','state':'reachable','updated_at':1791430986,'alpine':'3.23.6','packages':95},
                     {'target':'boxa-dmz','declared':'running','state':'reachable','updated_at':1794543961,'alpine':'3.24.4','packages':143},
                     {'target':'boxb-iot','declared':'stopped','state':'stopped'}]}}
        used={}
        def remote(user,host,argv,**kw):
            if argv==['/usr/local/sbin/platform-source','controllers']:
                return {'instance_sha256':used.get('sha256') or __import__('hashlib').sha256(
                    (Path(directory)/'klokast-instance.json').read_bytes()).hexdigest()}
            value=answers[(host,tuple(argv))]
            if isinstance(value,Exception):raise value
            return value
        probes={'boxa':dict(available=True,workspace=False,maintenance=False,attached=0,busy_pids=[]),
                'boxb':dict(available=True,workspace=True,maintenance=False,attached=2,busy_pids=[7])}
        def run(directory):
            output=io.StringIO()
            with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):code=self.client.main(['--instance',directory,'status'])
            return code,output.getvalue()
        credentials={'boxa-ops':True,'boxb-ops':True}
        def report(host):
            return {'hostname':host,'role':'fixture','ready':credentials[host],
                    'items':[{'id':'tailscale-policy','required':True,'state':'ok' if credentials[host] else 'absent','detail':'fixture'}]}
        with tempfile.TemporaryDirectory() as directory,patch.object(self.client,'remote',side_effect=remote), \
                patch.object(self.client.air,'probe',side_effect=lambda box:probes[box]), \
                patch.object(self.client.controller_setup,'report',side_effect=report):
            self.promotable(directory);value=json.loads((Path(directory)/'klokast-instance.json').read_text())
            value['apps']={'music':{'desired-state':'present','placement':{'mode':'multi-box','boxes':['boxa']}},
                           'nextcloud':{'desired-state':'absent'}}
            (Path(directory)/'klokast-instance.json').write_text(json.dumps(value));code,output=run(directory)
            self.assertEqual(code,0,output);lines=output.splitlines()
            self.assertEqual(lines[0],'---------------------- kk status ----------------------')
            self.assertEqual([line for line in lines if line.startswith('# ')],
                ['# Machines','# Credentials','# Applications','# Controllers','# Runners','# Promotions','# Updates','# Help','# Summary'])
            self.assertEqual(output.count('UTC'),1)
            self.assertIn('- Last controller switch: 2025-11-12 08:15',lines)
            self.assertEqual(lines[-3],'- No problem found.')
            for line in ('boxa-ops  active','boxb-ops  standby','TARGET     STATE      LAST UPDATE       ALPINE  PACKAGES',
                         'boxa-dom0  reachable  2026-10-08 03:43  3.23.6  95','boxa-dmz   reachable  2026-11-13 04:26  3.24.4  143',
                         'boxb-iot   stopped    -                 -       -','NAME       boxa     boxb    boxc    COMMAND',
                         'present  Tailscale policy and enrollment client  fixture',
                         'nextcloud  absent   absent  absent  -',
                         '- `kk status TARGET` for details',
                         'boxa-air  available  none       0        0','boxb-air  available  present    2        1',
                         'boxa-ops  2026-11-14 03:00  2026-11-14 04:12  complete',
                         'boxb-ops  2026-11-14 03:00  2026-11-14 03:00  skipped - verified standby',
                         '## boxb-ops',
                         '- No unfinished promote operation.','- No problem found.',
                         '- Instance: boxa-ops uses the same desired state as your Instance checkout.'):
                self.assertIn(line,lines)
            self.assertRegex(lines[-1],r'^------------- [A-Z][a-z]{2} \d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC -------------$')
            # Each problem is shown and makes the exit status 1.
            credentials['boxa-ops']=False;used['sha256']='0'*64
            answers[('boxb-ops',tuple(guard))]=RuntimeError('boxb-ops: connection timed out')
            answers[('boxb-ops',(base+'promote-status',))]=RuntimeError('boxb-ops: connection timed out')
            answers[('boxa-ops',(base+'promote-status',))]=[{'role':'ops','operation':'a'*24,'updated_at':1762935300,
                'target':'boxb-ops','phase':'synchronized','created_at':1762935240,'error':'standby demotion failed','running':False}]
            answers[('boxa-ops',(base+'vm-image-update','list'))]=[
                {'target':'boxa-dmz','operation':'c'*24,'state':'failed','created_at':1762935240,'detail':'service step failed'},
                {'target':'boxa-dom0','operation':'e'*24,'state':'interrupted','created_at':1762935240,'reboot':True},
                {'target':'boxb-iot','operation':'d'*24,'state':'updating','created_at':1762935240,'detail':'preparing the image'}]
            answers[('boxa-ops',(base+'ops-controller-nightly','--status'))]={'state':'interrupted','started':'2026-11-14T03:00:01Z',
                'log':'/var/lib/klokast/ops-nightly/x.log','error':'the run stopped before it recorded a result; inspect the run log',
                'alternation':{'state':'abandoned','error':'boxb-ops Instance repository must match its live upstream main branch'}}
            answers[('boxa-ops',(base+'machine-status','list'))]['machines'][1]={'target':'boxa-dmz','declared':'running',
                'state':'unreachable','error':'no answer in 30 seconds'}
            probes['boxa']={'available':False,'workspace':False,'maintenance':True,'attached':0,'busy_pids':[],'error':'SSH connection failed'}
            code,output=run(directory);self.assertEqual(code,1,output);lines=output.splitlines()
            self.assertIn('boxb-ops  unreachable',lines)
            self.assertIn('- boxb-ops is unreachable. Cause: boxb-ops: connection timed out',lines)
            self.assertIn('boxa-air  unreachable  unknown    -        -     SSH connection failed',lines)
            self.assertIn('- `kk promote boxb-ops --resume '+'a'*24+'` to resume',lines)
            self.assertIn('boxa-dmz   2025-11-12 08:14  -         failed - '+'c'*24+' - service step failed',lines)
            self.assertIn('- `kk update boxa-dmz` to restart job '+'c'*24,lines);self.assertNotIn('`kk update boxb-iot`',output)
            self.assertIn('- `kk reboot boxa-dom0` to restart job '+'e'*24,lines)
            self.assertIn('boxa-dmz   unreachable  -                 -       -',lines)
            self.assertIn('- boxa-dmz is unreachable. Cause: no answer in 30 seconds',lines)
            self.assertIn('  - `boxa-ops:/var/lib/klokast/ops-nightly/x.log`',lines)
            self.assertIn('- Not shown: the controller is unreachable.',lines)
            self.assertIn('- `kk setup --controller boxa-ops` for missing credentials',lines)
            self.assertIn('- Controller switch before this run: abandoned; boxa-ops stayed active. Cause: boxb-ops Instance '
                          'repository must match its live upstream main branch',lines)
            self.assertIn('- Instance: boxa-ops uses another desired state than your Instance checkout, so the reports '
                          'can be out of date. Run git pull in your Instance checkout. If they still differ, update the '
                          'controller copy: git -C /home/smith/private/klokast/instance pull --ff-only',lines)
            # The summary names each problem instead of a bare count.
            summary=lines[lines.index('# Summary')+2:-2]
            self.assertEqual(summary,['- 11 items need attention:',
                '  - boxb-ops is unreachable.',
                '  - boxa-dmz is unreachable (no answer in 30 seconds).',
                '  - boxa-ops misses a required credential, so it cannot become the active controller.',
                '  - boxa-ops uses another Instance than your checkout.',
                '  - boxa-air is unreachable.',
                '  - The promote records of boxb-ops are not shown.',
                '  - The promotion of boxb-ops (operation '+'a'*24+') is failed.',
                '  - The boxa-dmz update is failed: service step failed',
                '  - The boxa-dom0 reboot is interrupted.',
                '  - The scheduled update of boxa-ops interrupted: the run stopped before it recorded a result; inspect the run log',
                '  - The controller switch before the scheduled run was abandoned.'])
            # Without one active controller, the job records are not read.
            answers[('boxa-ops',tuple(guard))]={'hostname':'boxa-ops','active':False,'role':'standby','active_box':'boxb'}
            code,output=run(directory);self.assertEqual(code,1)
            self.assertIn('Exactly one active controller is required; 0 found. Reconcile controller authority.',output)
            self.assertIn('Jobs not shown: no single active controller.',output)
            self.assertIn('interrupted - the run stopped',output)
            self.assertNotIn('Controller switch before this run',output)
    def test_old_controller_placement_names_the_checkout_to_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'klokast-instance.json'
            path.write_text(json.dumps({'controllers':{'active':'boxa','standby':'boxb'}}))
            with self.assertRaisesRegex(ValueError,'old controllers form.*git -C '+directory+' pull --ff-only'):
                self.client.declared_controllers(directory)
            path.write_text(json.dumps({'controllers':['boxa','boxa']}))
            with self.assertRaisesRegex(ValueError,'klokast-instance.json: controllers must list one or two distinct boxes'):
                self.client.declared_controllers(directory)
            path.write_text(json.dumps({'controllers':['boxb','boxa']}))
            self.assertEqual(self.client.declared_controllers(directory),['boxa','boxb'])

    def test_nightly_alternation_lines_name_the_switch_and_the_standby_result(self):
        cases=(({'after_switch':'c'*24,'standby':'boxa','standby_update':'replaced'},[],
                ['- Controller switch before this run: complete (operation '+'c'*24+'); it made boxb-ops active.',
                 '- Standby update of boxa-ops: replaced. The next scheduled run can make it active first.']),
               ({'alternation':{'state':'not due','reason':'the previous scheduled run installed no new standby image '
                                 '(standby update: unchanged)'},'standby':'boxa','standby_update':'unchanged'},[],
                ['- Controller switch before this run: not due (the previous scheduled run installed no new standby image '
                 '(standby update: unchanged)).','- Standby update of boxa-ops: unchanged.']),
               ({'state':'skipped','reason':'verified standby'},[],[]))
        for run,issues,expected in cases:
            output=io.StringIO()
            with self.subTest(run=run),contextlib.redirect_stdout(output):
                self.assertEqual(self.client.show_alternation(run,'boxb'),issues)
            self.assertEqual(output.getvalue().splitlines(),expected)

    def test_promote_status_lists_the_unfinished_operations_of_each_controller(self):
        failed={'role':'ops','operation':'a'*24,'updated_at':1762935300,'target':'boxb-ops','phase':'publishing',
                'created_at':1762935240,'error':'Instance upstream advanced','running':False}
        stopped={'role':'air','operation':'b'*24,'updated_at':1762935400,'target':'boxb-air','phase':'closing',
                 'created_at':None,'error':None,'running':False}
        active=dict(stopped,operation='c'*24,target='boxa-air',running=True)
        def remote(user,host,argv,**kw):
            self.assertEqual((user,argv),('smith',['/home/smith/src/klokast/klokast-box/ansible/bin/promote-status']))
            if host=='boxb-ops':raise RuntimeError('boxb-ops: connection timed out')
            return [stopped,failed,active]
        with tempfile.TemporaryDirectory() as directory,patch.object(self.client,'remote',side_effect=remote):
            self.promotable(directory);code,output=self.promote(directory,'--status')
            self.assertEqual(code,1,output);lines=output.splitlines()
            self.assertEqual(lines[1].split()[:2],['TARGET','OPERATION'])
            self.assertRegex(lines[2],'^boxb-ops  a{24}  2025-11-12 08:14  2025-11-12 08:15  failed       phase publishing, recorded on boxa-ops: Instance upstream advanced$')
            self.assertRegex(lines[3],'^boxb-air  b{24}  unknown           2025-11-12 08:16  interrupted  phase closing, recorded on boxa-ops; no error is recorded$')
            self.assertRegex(lines[4],'^boxa-air  c{24}  .*  running      phase closing, recorded on boxa-ops$')
            self.assertIn('  kk promote boxb-ops --resume '+'a'*24,lines);self.assertIn('  kk promote boxb-air --resume '+'b'*24,lines)
            self.assertNotIn('  kk promote boxa-air --resume '+'c'*24,lines)
            self.assertIn('kk promote: boxb-ops: its records are not shown',output)
            code,output=self.promote(directory,'boxb-air','--status')
            self.assertNotIn('a'*24,output);self.assertIn('b'*24,output)
        with tempfile.TemporaryDirectory() as directory,patch.object(self.client,'remote',return_value=[]):
            self.promotable(directory);code,output=self.promote(directory,'--status')
            self.assertEqual(code,0);self.assertIn('No unfinished promote operation.',output)

class ControllerJobs(unittest.TestCase):
    def setUp(self):
        import air_image_update,vm_image_update
        self.job=vm_image_update;self.fresh=air_image_update
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        for obj,name,kwargs in ((self.job.cycle,'ROOT',{'new':Path(temporary.name)}),
                (self.job.inputs,'update_checkout',{'return_value':'e'*40}),
                (self.job.cycle.source,'require_controller',{'return_value':None}),
                (self.job.cycle.source,'snapshot',{'return_value':{'controllers':{'active':'boxa','standby':'boxb'},'instance':{'airunners':['boxa-air','boxb-air'],'controllers':['boxa','boxb'],
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

    def test_status_keeps_the_recent_stages_so_a_short_stage_is_not_lost(self):
        import platform_maintenance
        directory=self.job.cycle.ROOT/('a'*24);directory.mkdir()
        (directory/'image-job.json').write_text(json.dumps(dict(operation='a'*24,box='boxa',state='updating',reason='x',created_at=1)))
        with patch.dict(os.environ,KLOKAST_PROGRESS=str(directory/'progress.json')), \
                patch.object(platform_maintenance,'PROGRESS_STAGES',2):
            for text in ('first','all 3 VMs are online','checking the health'):platform_maintenance.progress(text)
        record=self.job.status('boxa')
        self.assertEqual(record['progress'],'checking the health')
        self.assertEqual([text for _at,text in record['stages']],['all 3 VMs are online','checking the health'])

    def test_list_shows_the_latest_unfinished_job_of_each_target(self):
        def job(operation,**fields):
            directory=self.job.cycle.ROOT/operation;directory.mkdir()
            (directory/'image-job.json').write_text(json.dumps(dict(operation=operation,**fields)))
        job('a'*24,box='boxa',state='failed',error='old failure',created_at=1.5)            # superseded by a later job
        job('b'*24,box='boxa',state='complete',reason='update verified',created_at=2.5)
        job('c'*24,box='boxa',role='dmz',state='failed',error='x'*600,reason='stage',created_at=3.5)
        job('d'*24,box='boxb',role='iot',state='updating',reason='preparing the image',created_at=4.5)
        job('e'*24,box='boxb',role='bak',state='waiting',reason='checking the lock',created_at=5.5)
        (self.job.cycle.ROOT/('f'*24)).mkdir()                                                  # maintenance record without a job
        with patch.object(self.job,'running',side_effect=lambda operation:operation=='d'*24):
            rows=self.job.unfinished()
        self.assertEqual(rows,[
            {'target':'boxa-dmz','operation':'c'*24,'state':'failed','created_at':3,'detail':'x'*500},
            {'target':'boxb-bak','operation':'e'*24,'state':'interrupted','created_at':5,'detail':'checking the lock'},
            {'target':'boxb-iot','operation':'d'*24,'state':'updating','created_at':4,'detail':'preparing the image'}])
        self.job.cycle.source.require_controller.assert_called()
    def test_list_command_accepts_no_selector(self):
        tool=str(Path(__file__).resolve().parents[1]/'bin/vm-image-update')
        for arguments,message in ((['list','--box','boxa'],'list accepts no'),(['status'],'status needs --box and --role')):
            result=subprocess.run([sys.executable,tool,*arguments],capture_output=True,text=True)
            self.assertEqual(result.returncode,2);self.assertIn(message,result.stderr)

    def test_target_mismatch_cannot_overwrite_an_operation(self):
        with patch.object(self.job.subprocess,'Popen'):
            self.job.launch_job('boxa','a'*24)
            with self.assertRaisesRegex(RuntimeError,'another update target'):self.job.launch_job('boxb','a'*24)
            with self.assertRaisesRegex(RuntimeError,'another update target'):self.job.launch_job('boxa','a'*24,'dmz')
    def test_start_selects_the_operation_and_refuses_one_from_the_request(self):
        tool=str(Path(__file__).resolve().parents[1]/'bin/vm-image-update')
        result=subprocess.run([sys.executable,tool,'start','--box','boxa','--role','air','--operation','a'*24],capture_output=True,text=True)
        self.assertEqual(result.returncode,1);self.assertIn('start selects the job of the target',result.stderr)
    def test_request_uses_the_common_target_authority(self):
        with patch.object(self.job.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'hand off authority first'):self.job.start('boxa','ops')
            with self.assertRaisesRegex(RuntimeError,'declared stopped'):self.job.start('boxb','iot')
            with self.assertRaisesRegex(RuntimeError,'not declared'):self.job.start('boxc','dmz')
            launch.assert_not_called()
            record=self.job.start('boxb','ops')
        self.assertEqual((record['role'],record['reason'],record['request']),('ops','checking that no other update work is ongoing','new'))
        self.assertEqual(launch.call_args.args[0][2:],['worker','--box','boxb','--role','ops','--operation',record['operation']])
    def test_roles_on_one_box_have_separate_jobs(self):
        with patch.object(self.job.subprocess,'Popen'):
            record=self.job.start('boxa','dmz')
        self.assertEqual(self.job.status('boxa')['state'],'absent')
        self.assertEqual(self.job.status('boxa',role='dmz')['operation'],record['operation'])
    def test_completed_job_is_not_relaunched_and_a_new_request_starts_a_new_job(self):
        directory=self.job.cycle.ROOT/('a'*24);directory.mkdir()
        (directory/'image-job.json').write_text(json.dumps({'state':'complete','box':'boxa','operation':'a'*24,'created_at':1}))
        with patch.object(self.job.subprocess,'Popen') as launch:
            record,launched=self.job.launch_job('boxa','a'*24)
            self.assertEqual((record['state'],launched),('complete',False));launch.assert_not_called()
            record=self.job.start('boxa')
        self.assertEqual(record['request'],'new');self.assertNotEqual(record['operation'],'a'*24)
    def test_repeated_request_joins_the_existing_job(self):
        import fcntl
        directory=self.job.cycle.ROOT/('a'*24);directory.mkdir()
        record=dict(state='updating',box='boxa',operation='a'*24,created_at=1)
        (directory/'image-job.json').write_text(json.dumps(record))
        with (directory/'worker.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with patch.object(self.job.subprocess,'Popen') as launch:
                joined=self.job.start('boxa')
                self.assertEqual(dict(joined,requested_at=None),dict(record,request='joined',requested_at=None))
                launch.assert_not_called()
                self.job.inputs.update_checkout.assert_not_called()
        self.assertEqual([p.name for p in self.job.cycle.ROOT.iterdir() if p.is_dir()],['a'*24])
    def test_request_restarts_the_failed_or_interrupted_job_with_its_operation(self):
        # Only the operation of the failed job holds its recorded VM transaction.
        for state,previous,role,operation in (('failed','failed','dmz','a'*24),('updating','interrupted','air','b'*24)):
            with self.subTest(state=state):
                directory=self.job.cycle.ROOT/operation;directory.mkdir()
                (directory/'image-job.json').write_text(json.dumps(
                    dict(state=state,box='boxa',role=role,operation=operation,created_at=1,error='old failure')))
                with patch.object(self.job.subprocess,'Popen') as launch:
                    record=self.job.start('boxa',role)
                self.assertEqual((record['operation'],record['request'],record['previous'],record['state']),
                                 (operation,'restarted',previous,'waiting'))
                self.assertEqual(launch.call_args.args[0][-1],operation)

    def test_new_worker_starts_after_controller_source_update(self):
        with patch.object(self.job.subprocess,'Popen',side_effect=lambda *a,**k:
                self.job.inputs.update_checkout.assert_called_once_with(self.job.cycle.source.REPO)):
            self.job.start('boxa')

    def test_source_failure_does_not_launch_or_replace_a_failed_job_record(self):
        directory=self.job.cycle.ROOT/('a'*24);directory.mkdir()
        path=directory/'image-job.json'
        record=dict(state='failed',box='boxa',operation='a'*24,created_at=1,error='old failure')
        path.write_text(json.dumps(record))
        self.job.inputs.update_checkout.side_effect=RuntimeError('preserve local changes')
        with patch.object(self.job.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'preserve local changes'):
                self.job.start('boxa')
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
        mock=patch.object(image,'inspect_work');mock.start();self.addCleanup(mock.stop)

    def test_connection_refused_with_writers_is_busy(self):
        import subprocess
        with patch.object(image.workspace,'codex_binary',return_value='/fixture/codex'), \
                patch.object(image.subprocess,'check_output',side_effect=subprocess.CalledProcessError(1,['codex'])), \
                patch.object(workspace,'live_writers',return_value={'thread'}):
            with self.assertRaisesRegex(RuntimeError,'writers exist'):image.inspect()
    def test_connection_refused_without_writers_allows_offline_checkpoint(self):
        import subprocess
        with patch.object(image.workspace,'codex_binary',return_value='/fixture/codex'), \
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
with patch.object(image.workspace,'ROOT',Path({str(root)!r})), patch.object(image.workspace,'codex_binary',return_value={str(cli)!r}), patch.object(image,'inspect',return_value={{'threads':[],'claude':[]}}), patch.object(image.workspace,'inventory',return_value={{'sessions':[],'discussions':[]}}), patch.object(image.workspace,'frozen_tuis',return_value=contextlib.nullcontext()):
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
    def tearDown(self):
        # The worker arms a real twelve-hour job alarm. A test that does not
        # patch signal.alarm leaves it armed, and it fires in a later test.
        import signal
        self.assertEqual(signal.alarm(0), 0, 'a worker test left its job alarm armed; patch signal.alarm')

    def setUp(self):
        # The worker names its progress file in its own environment.
        context=patch.dict(os.environ);context.start();self.addCleanup(context.stop)
        import vm_image_update as job
        # The controller checkout is already at upstream main unless a test moves it.
        for name in ('revision','update_checkout'):
            context=patch.object(job.inputs,name,return_value='c'*40);context.start();self.addCleanup(context.stop)

    def test_worker_restarts_on_source_that_moved_while_it_waited(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            path=directory/'image-job.json'
            path.write_text(json.dumps(dict(state='waiting',operation='b'*24,box='boxa',created_at=time.time()-3600)))
            with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'), \
                    patch.object(signal,'signal'),patch.object(signal,'alarm') as alarm, \
                    patch.object(job.air_maintenance,'remote',return_value={'idle':True}), \
                    patch.object(job.cycle.runtime,'vm_update_installation_lock',return_value=contextlib.nullcontext()), \
                    patch.object(job.inputs,'update_checkout',return_value='d'*40), \
                    patch.object(job.os,'execv') as execv,patch.object(job.cycle,'step') as update, \
                    contextlib.redirect_stderr(io.StringIO()):
                job.worker('boxa','b'*24)
            update.assert_not_called()
            # The restarted worker keeps the twelve-hour bound of the job, not a new one.
            self.assertLessEqual(alarm.call_args.args[0],43200-3600)
            self.assertEqual(execv.call_args.args[1][-7:],['worker','--box','boxa','--role','air','--operation','b'*24])
            saved=json.loads(path.read_text())
            self.assertEqual(saved['state'],'waiting')
            self.assertIn('controller source moved to '+'d'*12,saved['reason'])

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
            resume.assert_called_once_with('update','boxa','b'*24,'air',wait_for_lock=True,before_change=ANY)
            self.assertEqual(json.loads((directory/'image-job.json').read_text())['state'],'complete')

    def test_ended_vm_attempt_restarts_the_worker_for_a_new_attempt(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            (directory/'update-air.json').write_text(json.dumps({'vm_operation':'a'*24}))
            path=directory/'image-job.json'
            path.write_text(json.dumps({'state':'failed','operation':'b'*24,'box':'boxa','created_at':time.time()}))
            ended={'state':'abandoned','operation_id':'a'*24,'reason':'VM operation '+'a'*24+' ended before the new boxa-air started'}
            with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'), \
                    patch.object(signal,'signal'),patch.object(signal,'alarm'), \
                    patch.object(job.cycle.runtime,'vm_update_installation_lock',return_value=contextlib.nullcontext()), \
                    patch.object(job.cycle,'step',return_value=ended),patch.object(job.os,'execv') as execv, \
                    contextlib.redirect_stderr(io.StringIO()):
                job.worker('boxa','b'*24)
            # The new worker waits for an idle runner and fresh source again.
            self.assertEqual(execv.call_args.args[1][-7:],['worker','--box','boxa','--role','air','--operation','b'*24])
            saved=json.loads(path.read_text())
            self.assertEqual(saved['state'],'waiting');self.assertIn('ended before the new boxa-air started; starting a new attempt',saved['reason'])

    def test_failed_job_error_names_no_controller_resume_command_for_its_own_vm_operation(self):
        import vm_image_update as job
        with tempfile.TemporaryDirectory() as d:
            saved=Path(d)/'update-air.json';saved.write_text(json.dumps({'vm_operation':'a'*24}))
            hint='; resume with: ansible/bin/platform-update update --box boxa --role air --resume '
            self.assertEqual(job.job_error(RuntimeError('frozen service execution failed'+hint+'a'*24),saved,'boxa','air'),
                             'frozen service execution failed')
            # Another operation keeps its command: this job does not resume it.
            self.assertIn(hint+'c'*24,job.job_error(RuntimeError('pending'+hint+'c'*24),saved,'boxa','air'))

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
                self.assertEqual(saved['checks'],['Discussions on boxa-air are not active: OK'])
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
            update.assert_called_once_with('update','boxa','b'*24,'air',wait_for_lock=True,before_change=ANY)
            self.assertEqual(json.loads(path.read_text())['state'],'complete')
            self.assertEqual(json.loads(path.read_text())['reason'],'update of boxa-air is complete; tmux workspace not restored: checkpoint missing')
            self.assertEqual(json.loads(path.read_text())['checks'][-1],'No other update work is ongoing: OK')

    def test_worker_names_the_blocking_activity(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            path=directory/'image-job.json'
            path.write_text(json.dumps(dict(state='waiting',operation='b'*24,box='boxa',created_at=1)))
            seen=[]
            def sleep(_seconds):
                record=json.loads(path.read_text())
                # The wait checks for a cancel request each second; keep one sample per poll.
                if not seen or seen[-1]['reason']!=record['reason']:seen.append(record)
            # Only a runner image older than the client detach reports attached clients as the cause.
            replies=[{'idle':False,'attached':1,'busy_pids':[]},{'idle':False,'attached':1,'busy_pids':[7]},
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
                ('Discussions or other work on boxa-air are active: waiting',[]),
                ('Claude Code discussion is active or needs attention: PID 8 is waiting: waiting',[])])
            self.assertEqual(seen[-1]['busy_processes'],0)
            # A repeated wait reason keeps its time; each check keeps the time at which it passed.
            self.assertEqual(seen[1]['check_times'],seen[2]['check_times'])
            self.assertLess(seen[0]['changed_at'],seen[1]['changed_at'])
            saved=json.loads(path.read_text())
            self.assertEqual(sorted(saved['check_times']),sorted(saved['checks']))
            self.assertGreaterEqual(saved['changed_at'],saved['check_times']['No other update work is ongoing: OK'])
            self.assertEqual(json.loads(path.read_text())['checks'],[
                'Discussions on boxa-air are not active: OK','No other update work is ongoing: OK'])

    def test_cancel_request_stops_a_waiting_worker_before_the_update(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            path=directory/'image-job.json'
            path.write_text(json.dumps(dict(state='waiting',operation='b'*24,box='boxa',created_at=1)))
            with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'), \
                    patch.object(signal,'signal'),patch.object(signal,'alarm'), \
                    patch.object(job.time,'sleep',side_effect=lambda _:(directory/job.CANCEL).touch()), \
                    patch.object(job.air_maintenance,'remote',return_value={'idle':False,'attached':0,'busy_pids':[7]}), \
                    patch.object(job.cycle,'step') as update:
                job.worker('boxa','b'*24)
            update.assert_not_called()
            saved=json.loads(path.read_text())
            self.assertEqual((saved['state'],saved['reason']),('cancelled','cancelled on request before the update changed the VM'))
            self.assertFalse((directory/job.CANCEL).exists())

    def test_busy_runner_stops_the_job_after_the_idle_bound(self):
        import vm_image_update as job
        import signal
        busy={'idle':False,'attached':0,'busy_pids':[7],'busy_work':['7 claude (parent 6)']}
        for waited,abandoned in ((job.IDLE_WAIT,['a'*24]),(job.IDLE_WAIT-5,None)):
            with tempfile.TemporaryDirectory() as d:
                directory=Path(d)/('b'*24);directory.mkdir()
                path=directory/'image-job.json'
                # A restarted worker keeps the time that the attempt already waited.
                path.write_text(json.dumps(dict(state='waiting',operation='b'*24,box='boxa',created_at=1,idle_waited=waited)))
                if abandoned:(directory/'update-air.json').write_text(json.dumps({'abandoned':abandoned}))
                clock=iter(range(0,10**6,10))
                with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'), \
                        patch.object(signal,'signal'),patch.object(signal,'alarm'), \
                        patch.object(job.time,'sleep'),patch.object(job.time,'monotonic',side_effect=lambda:next(clock)), \
                        patch.object(job.air_maintenance,'remote',return_value=busy), \
                        patch.object(job.cycle.runtime,'vm_update_installation_lock',return_value=contextlib.nullcontext()), \
                        patch.object(job.cycle,'step') as update:
                    job.worker('boxa','b'*24)
                update.assert_not_called()
                saved=json.loads(path.read_text())
                self.assertEqual(saved['state'],'cancelled')
                self.assertEqual(saved['reason'],'stopped after 15 minutes: Discussions or other work on boxa-air are active '
                    '[7 claude (parent 6)]. The update did not change the VM. When the runner is idle, start a new job: kk update boxa-air')

    def test_runner_busy_again_before_the_image_step_waits_again_within_the_bound(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            path=directory/'image-job.json'
            path.write_text(json.dumps(dict(state='waiting',operation='b'*24,box='boxa',created_at=time.time(),idle_waited=120)))
            with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'), \
                    patch.object(signal,'signal'),patch.object(signal,'alarm'), \
                    patch.object(job.air_maintenance,'remote',return_value={'idle':True}), \
                    patch.object(job.cycle.runtime,'vm_update_installation_lock',return_value=contextlib.nullcontext()), \
                    patch.object(job.inputs,'update_checkout',return_value=None),patch.object(job.inputs,'revision',return_value=None), \
                    patch.object(job.cycle.service,'step',return_value={'installed':{'image':'old'}}), \
                    patch.object(job.cycle,'step',return_value={'state':'skipped','reason':job.RUNNER_BUSY}), \
                    patch.object(job.os,'execv') as execv,contextlib.redirect_stderr(io.StringIO()):
                job.worker('boxa','b'*24)
            self.assertEqual(execv.call_args.args[1][-7:],['worker','--box','boxa','--role','air','--operation','b'*24])
            saved=json.loads(path.read_text())
            self.assertEqual((saved['state'],saved['reason']),('waiting','boxa-air became busy before the image step; waiting again'))
            self.assertGreaterEqual(saved['idle_waited'],120)

    def test_cancel_stops_a_job_only_before_it_changes_the_vm(self):
        import vm_image_update as job
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            path=directory/'image-job.json'
            def run(state,saved=None,live=False,role='air'):
                path.write_text(json.dumps(dict(state=state,operation='b'*24,box='boxa',role=role,created_at=1)))
                if saved:(directory/('update-'+role+'.json')).write_text(json.dumps(saved))
                with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'), \
                        patch.object(job,'running',return_value=live),patch.object(job.time,'sleep'), \
                        patch.object(job.time,'monotonic',side_effect=itertools.count(0,10)):
                    return job.cancel('boxa',role)
            with self.assertRaisesRegex(RuntimeError,'nothing to cancel'):run('complete')
            self.assertEqual(run('waiting')['state'],'cancelled')
            with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'):
                self.assertEqual(job.unfinished(),[])
            # A job that has not stopped yet keeps the request; it stops at its next check.
            self.assertTrue(run('waiting',live=True)['cancel_requested'])
            # A running image build ends first; the request stops the job before the VM changes.
            self.assertTrue(run('updating',live=True)['cancel_requested'])
            self.assertTrue((directory/job.CANCEL).exists())
            with self.assertRaises(job.Cancelled):job.before_change(directory)()
            self.assertFalse((directory/job.CHANGING).exists())
            # After the last check, the change starts and each cancel refuses.
            (directory/job.CANCEL).unlink()
            job.before_change(directory)()
            with self.assertRaisesRegex(RuntimeError,'already changes the VM'):run('updating',live=True)
            self.assertFalse((directory/job.CANCEL).exists())
            (directory/job.CHANGING).unlink()
            with self.assertRaisesRegex(RuntimeError,'recorded VM operation'):run('waiting',{'vm_operation':'a'*24})
            (directory/'update-air.json').unlink()
            # A dom0 job of older code has no marker; its host step arguments show the change.
            import dom0_updates
            with patch.object(dom0_updates,'STATE',Path(d)/'dom0'):
                (Path(d)/'dom0'/('b'*24)).mkdir(parents=True)
                (Path(d)/'dom0'/('b'*24)/'prepare-args.json').write_text('{}')
                with self.assertRaisesRegex(RuntimeError,'already changes the host'):run('updating',live=True,role='dom0')

    def test_cancel_request_stops_the_job_at_the_last_check_before_the_vm_change(self):
        import vm_image_update as job
        import signal
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/('b'*24);directory.mkdir()
            path=directory/'image-job.json'
            path.write_text(json.dumps(dict(state='waiting',operation='b'*24,box='boxa',role='dmz',created_at=time.time())))
            def step(*_args,before_change,**_kwargs):
                # The request arrives during the image build; the build ends, then the check stops the job.
                (directory/job.CANCEL).touch();before_change();raise AssertionError('the VM change must not start')
            with patch.object(job.cycle,'ROOT',Path(d)),patch.object(job.cycle.source,'require_controller'), \
                    patch.object(signal,'signal'),patch.object(signal,'alarm'), \
                    patch.object(job.cycle.runtime,'vm_update_installation_lock',return_value=contextlib.nullcontext()), \
                    patch.object(job.inputs,'update_checkout',return_value=None),patch.object(job.inputs,'revision',return_value=None), \
                    patch.object(job.cycle.service,'step',return_value={'installed':{'image':'old'}}), \
                    patch.object(job.cycle,'step',side_effect=step):
                job.worker('boxa','b'*24,'dmz')
            saved=json.loads(path.read_text())
            self.assertEqual((saved['state'],saved['reason']),('cancelled','cancelled on request before the update changed the VM'))
            self.assertFalse((directory/job.CANCEL).exists())
            self.assertFalse((directory/job.CHANGING).exists())

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
                update.assert_called_once_with('update','boxa','b'*24,'dmz',wait_for_lock=True,before_change=ANY)
                saved=json.loads(path.read_text())
                self.assertEqual((saved['state'],saved['reason'],saved['checks']),
                    ('complete','image and configuration unchanged; health checks passed',['No other update work is ongoing: OK']))
                update.return_value={'state':'skipped','reason':'declared stopped'}
                with self.assertRaisesRegex(RuntimeError,'boxa-dmz update is skipped: declared stopped'):job.worker('boxa','b'*24,'dmz')
            self.assertEqual(json.loads(path.read_text())['state'],'failed')
