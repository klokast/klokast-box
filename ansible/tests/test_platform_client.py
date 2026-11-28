"""The MacBook client follows update jobs and reads machine status through the active controller."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'klokast-dev/lib'))
import platform_client as client


class ImageUpdate(unittest.TestCase):
    def run_update(self,argv,replies):
        calls=[]
        def remote(user,host,command,**kwargs):
            calls.append(command);return replies.pop(0)
        out=io.StringIO()
        with patch.object(client,'declared_boxes',return_value=['k001']), \
                patch.object(client,'controller',return_value='k002'),patch.object(client,'remote',side_effect=remote), \
                patch.object(client.time,'sleep'),contextlib.redirect_stdout(out):
            code=client.main(['--instance','instance','update','k001-air',*argv])
        return code,out.getvalue(),calls

    MACHINE=dict(target='k001-air',declared='running',state='reachable',updated_at=1794549563,image_built_at=1794548675,
                 booted_at=1794549500,alpine='3.24.4',packages=[dict(name='zlib',version='1.3.1-r2',built_at=1776880594),
                                                                  dict(name='local-tool',version='1-r0')])

    def run_status(self,replies):
        calls=[]
        def remote(user,host,command,**kwargs):
            calls.append(command);return replies.pop(0)
        out=io.StringIO()
        with patch.object(client,'applications',return_value=[('music','present',True)]), \
                patch.object(client,'controller',return_value='k002'),patch.object(client,'remote',side_effect=remote), \
                contextlib.redirect_stdout(out):
            code=client.main(['--instance','instance','status','k001-air'])
        return code,out.getvalue(),calls

    def test_status_of_one_machine_shows_its_times_packages_and_update_job(self):
        code,out,calls=self.run_status([self.MACHINE,{'state':'absent','box':'k001','role':'air'}])
        self.assertEqual(code,0,out)
        self.assertEqual(out.count('UTC'),1)
        self.assertEqual([c[0].rsplit('/',1)[1:]+c[1:] for c in calls],[['machine-status','show','--target','k001-air'],
            ['vm-image-update','status','--box','k001','--role','air']])
        for line in ('- Machine: k001-air','- State: reachable','- Last update: 2026-11-13 05:59','- Image built: 2026-11-13 05:44',
                     '- Alpine release: 3.24.4','- Packages: 2','PACKAGE     VERSION   BUILT','zlib        1.3.1-r2  2026-04-22 17:56',
                     'local-tool  1-r0      unknown'):
            self.assertIn(line,out.splitlines())
        self.assertIn('No update job exists for k001-air',out)
        # An unreachable machine and a failed job make the exit status 1. A dom0 has no update job.
        code,out,_calls=self.run_status([dict(target='k001-air',declared='running',state='unreachable',error='no answer in 30 seconds'),
                                         dict(operation='c'*24,state='failed',error='AIR became busy')])
        self.assertEqual(code,1);self.assertIn('Cause: no answer in 30 seconds',out)
        self.assertIn('`kk update k001-air` to restart job '+'c'*24,out)

    def test_status_of_one_machine_shows_its_role_health(self):
        machine=dict(self.MACHINE,health=[dict(name='Proxy service',state='ok',detail='started'),
                                          dict(name='Selected relay',state='warning',detail='JP (dead at the last test)'),
                                          dict(name='Proxy traffic: Google',state='failed',detail='HTTP 503 in 1.0 s')])
        code,out,_calls=self.run_status([machine,{'state':'absent'}])
        self.assertEqual(code,1)
        for line in ('# Health','- OK: Proxy service: started','- WARNING: Selected relay: JP (dead at the last test)',
                     '- FAILED: Proxy traffic: Google: HTTP 503 in 1.0 s','  - k001-air: Proxy traffic: Google failed.'):
            self.assertIn(line,out.splitlines())
        code,out,_calls=self.run_status([dict(self.MACHINE,health_error='no health result in 400 seconds'),{'state':'absent'}])
        self.assertEqual(code,1);self.assertIn('- Not checked. Cause: no health result in 400 seconds',out.splitlines())
        # An appliance has no login: only its declaration and health show.
        code,out,_calls=self.run_status([dict(target='k001-air',declared='running',state='appliance',image_built_at=1794549563,
                                              image='a'*24,operation='b'*24,package_source='build closure',
                                              packages=[dict(name='busybox',version='1.37.0-r30')],
                                              health=[dict(name='Xen guest',state='ok',detail='exit runs')]),{'state':'absent'}])
        self.assertEqual(code,0)
        for line in ('- OK: Xen guest: exit runs','- Image built: 2026-11-13 05:59','PACKAGE  VERSION','busybox  1.37.0-r30'):
            self.assertIn(line,out.splitlines())
        self.assertIn('build-only packages',out)
        _code,out,_calls=self.run_status([dict(target='k001-air',declared='running',state='appliance',image_error='no answer'),{'state':'absent'}])
        self.assertIn('- Image record: not read. Cause: no answer',out.splitlines());self.assertNotIn('# Packages',out)
        # A role without health checks has no Health section.
        _code,out,_calls=self.run_status([self.MACHINE,{'state':'absent'}]);self.assertNotIn('# Health',out)

    def test_a_vm_that_its_health_check_stopped_names_the_restart(self):
        stopped=dict(target='k001-air',declared='running',state='stopped-health',packages=[],
                     health_stop=dict(check='CUPS queue',detail='queue epson is absent',restart_requires='power on the printer',at=1794549563))
        code,out,_calls=self.run_status([stopped,{'state':'absent'}])
        self.assertEqual(code,0)
        for line in ('- State: stopped by its health check',
                     '- Stopped at 2026-11-13 05:59 because it cannot work: CUPS queue: queue epson is absent',
                     '- Power on the printer, then run: `kk restart k001-air`',
                     '- Warning: k001-air is stopped until you power on the printer. Then run: kk restart k001-air'):
            self.assertIn(line,out.splitlines())
        self.assertNotIn('No problem found',out)
        # A failed stop needs attention.
        code,out,_calls=self.run_status([dict(self.MACHINE,health=[dict(name='CUPS queue',state='warning',detail='absent')],
                                              health_stop_error='another VM update holds the installation lock'),{'state':'absent'}])
        self.assertEqual(code,1);self.assertIn('The health check could not stop it. Cause: another VM update',out)

    def test_restart_asks_the_controller_and_shows_the_result(self):
        calls=[]
        def remote(user,host,command,**kwargs):
            calls.append((host,command,kwargs.get('timeout')));return dict(self.MACHINE,restarted=True)
        out=io.StringIO()
        with patch.object(client,'declared_boxes',return_value=['k001']),patch.object(client,'controller',return_value='k002'), \
                patch.object(client,'remote',side_effect=remote),contextlib.redirect_stdout(out):
            code=client.main(['--instance','instance','restart','k001-air'])
        self.assertEqual(code,0)
        self.assertEqual(calls[0][0],'k002-ops');self.assertEqual(calls[0][1][1:],['restart','--target','k001-air'])
        self.assertIn('starting k001-air through k002-ops',out.getvalue());self.assertIn('- State: reachable',out.getvalue())

    def test_the_overview_shows_the_nightly_health_checks_of_the_active_controller(self):
        run={'health':{'state':'complete','machines':[
            {'target':'k001-air','failed':['Workspace: tmux is missing'],'warnings':[]},
            {'target':'k002-print','failed':[],'warnings':['CUPS queue: absent'],'health_stop':{'restart_requires':'power on the printer'}},
            {'target':'k001-vpn-egress','failed':[],'warnings':[],'error':'no answer'}]}}
        out=io.StringIO()
        with contextlib.redirect_stdout(out):issues,notes=client.show_nightly_health(run,'k001')
        self.assertEqual(issues,['Nightly health check of k001-air: Workspace: tmux is missing','Nightly health check of k001-vpn-egress: no answer'])
        self.assertEqual(notes,['k002-print is stopped until you power on the printer. Then run: kk restart k002-print'])
        self.assertIn('Nightly health checks of k001-ops: k001-air, k002-print, k001-vpn-egress.',out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as quiet:
            self.assertEqual(client.show_nightly_health({'health':{'state':'complete','machines':[{'target':'k002-exit'}]}},'k001'),([],[]))
        self.assertIn('k002-exit; all passed',quiet.getvalue())
        self.assertEqual(client.show_nightly_health({},'k001'),([],[]))

    def test_status_of_one_application_does_not_connect(self):
        out=io.StringIO()
        with patch.object(client,'applications',return_value=[('music','present',True)]),patch.object(client,'remote') as remote, \
                contextlib.redirect_stdout(out):
            self.assertEqual(client.main(['--instance','instance','status','music']),0)
        remote.assert_not_called();self.assertIn('Application: music',out.getvalue());self.assertIn('kk app music --help',out.getvalue())

    def test_update_follows_each_stage_until_completion(self):
        waiting=dict(operation='c'*24,box='k001',state='waiting',reason='Discussions or other work on k001-air are active: waiting',attached=1,busy_processes=2,checks=[])
        idle=['Discussions on k001-air are not active: OK']
        locked=dict(waiting,reason='checking that no other update work is ongoing',checks=idle);del locked['attached'],locked['busy_processes']
        done=dict(locked,state='complete',reason='update verified',result={'image':'d'*24},checks=idle+['No other update work is ongoing: OK'])
        code,out,calls=self.run_update([],[waiting,dict(waiting),locked,done])
        self.assertEqual(code,0)
        # The start is one timestamped line that ends with the command to follow the job again.
        start=out.splitlines()[0]
        self.assertRegex(start,r'^\d\d:\d\d:\d\d k\d+-ops starting the k001-air update, job c{24}\. Ctrl-C or a closed terminal does not stop the job\. '
                         r'Resume following it: kk update k001-air\. '
                         r'Stop the job before it changes k001-air: kk update k001-air --cancel$')
        self.assertEqual(out.count('active: waiting; busy processes: 2. Let the work on k001-air finish\n'),1)
        self.assertNotIn('attached clients',out)
        self.assertIn('Waiting: checking that no other update work is ongoing',out)
        for check in idle+['No other update work is ongoing: OK']:self.assertEqual(out.count(check),1)
        self.assertIn('Update verified; image '+'d'*24,out)
        self.assertEqual([c[1] for c in calls],['start','status','status','status'])
        self.assertTrue(all(c[-1]=='c'*24 for c in calls[1:]))

    def test_update_prints_each_short_stage_between_two_status_checks(self):
        now=client.time.time()
        stage=dict(operation='c'*24,box='k001',state='updating',reason='x',progress='a',progress_at=now,stages=[[now,'a']])
        later=dict(stage,progress='c',progress_at=now,stages=[[now,'a'],[now,'all 3 VMs are online'],[now,'c']])
        done=dict(stage,state='complete',reason='update verified',result={'image':'d'*24})
        code,out,_calls=self.run_update([],[stage,later,done])
        self.assertEqual(code,0)
        self.assertEqual([line[9:] for line in out.splitlines() if line[9:] in ('A','All 3 VMs are online','C')],
                         ['A','All 3 VMs are online','C'])

    def test_update_shows_the_published_stage(self):
        updating=dict(operation='c'*24,box='k001',state='updating',reason='preparing and verifying the replacement image',
                      progress='image build: build the candidate in a networkless Xen guest on k001')
        code,out,_calls=self.run_status([self.MACHINE,updating])
        self.assertEqual(code,0);self.assertIn('Image build: build the candidate in a networkless Xen guest on k001',out)
        self.assertNotIn('preparing and verifying',out)

    def test_status_prints_the_recorded_times_and_not_the_time_of_the_request(self):
        check='No other update work is ongoing: OK'
        updating=dict(operation='c'*24,box='k001',state='updating',reason='preparing',checks=[check],check_times={check:3600},
                      changed_at=3700,progress='booting the replacement k001-air',progress_at=86400+7384)
        gmtime=client.time.gmtime
        with patch.object(client.time,'gmtime',side_effect=lambda seconds=86400+9000:gmtime(seconds)):
            _code,out,_calls=self.run_status([self.MACHINE,updating])
        self.assertIn('1970-01-01 01:00:00: '+check,out)
        self.assertIn('\n- 1970-01-02 02:03:04: Booting the replacement k001-air',out)
        # A record without recorded times must not show the time of the request.
        _code,out,_calls=self.run_status([self.MACHINE,dict(operation='c'*24,box='k001',state='waiting',reason='x',checks=[check])])
        self.assertIn('unknown: '+check,out);self.assertIn('unknown: Waiting: x',out)

    def test_failed_job_prints_the_error_and_the_restart_command(self):
        code,out,_calls=self.run_update([],[dict(operation='c'*24,box='k001',state='updating',reason='x',request='restarted',previous='failed'),
            dict(operation='c'*24,box='k001',state='failed',error='AIR became busy')])
        self.assertEqual(code,1);self.assertIn('Failed: AIR became busy',out)
        self.assertIn(' k002-ops restarting the failed k001-air update, job '+'c'*24+'. ',out)
        self.assertEqual(out.splitlines()[-1],'Inspect: kk status k001-air. Restart the same job: kk update k001-air')
        self.assertNotIn('--resume',out)

    def test_detach_and_status_do_not_follow(self):
        record=dict(operation='c'*24,box='k001',state='waiting',reason='checking that no agent runs on k001-air')
        code,out,calls=self.run_update(['--detach'],[record])
        self.assertEqual((code,len(calls)),(0,1));self.assertIn('Check progress: kk status k001-air',out)
        code,out,calls=self.run_status([self.MACHINE,dict(record)])
        self.assertEqual((code,[c[1] for c in calls]),(0,['show','status']));self.assertIn('Waiting: checking that no agent runs on k001-air',out)


class UpdateAll(unittest.TestCase):
    MACHINES=[dict(target=t,declared='running') for t in (
        'k001-air','k002-air','k001-bak','k002-bak','k001-dmz','k002-dmz','k001-dom0','k002-dom0','k001-iot',
        'k001-ops','k002-ops','k001-router','k002-router','k001-vpn-egress')]+[dict(target='k002-iot',declared='stopped')]

    def test_plan_updates_the_standby_box_first_and_switches_once_before_the_active_controller(self):
        machines=self.MACHINES
        steps=client.update_plan(machines,['k001','k002'],'k002')
        self.assertEqual(['kk %s %s'%step for step in steps],[
            'kk update k001-bak','kk update k001-dmz','kk update k001-iot','kk update k001-vpn-egress','kk update k001-air',
            'kk update k001-router','kk update k001-ops','kk update k001-dom0',
            'kk update k002-bak','kk update k002-dmz','kk update k002-air','kk update k002-router',
            'kk promote k001-ops','kk update k002-ops','kk update k002-dom0'])
        # A box without a controller has no switch and runs between the two controller boxes.
        steps=client.update_plan(machines+[dict(target='k003-dom0',declared='running'),dict(target='k003-dmz',declared='running')],
                                 ['k001','k002'],'k001')
        self.assertEqual(steps.index(('update','k003-dom0'))-steps.index(('update','k003-dmz')),1)
        self.assertEqual([s for s in steps if s[0]=='promote'],[('promote','k002-ops')])
        self.assertLess(steps.index(('update','k003-dom0')),steps.index(('update','k001-bak')))
        with self.assertRaisesRegex(ValueError,'declared active and a declared standby'):
            client.update_plan(machines,['k001'],'k001')

    def run_all(self,results):
        steps=[]
        def update(args):steps.append(('update',args.target));return results.pop(0)
        def promote(args):steps.append(('promote',args.target));return results.pop(0)
        out=io.StringIO()
        # The run asks no question: it starts after the plan.
        with patch.object(client,'controller',return_value='k002'),patch.object(client,'declared_controllers',return_value=['k001','k002']), \
                patch.object(client,'remote',return_value={'machines':self.MACHINES}),patch.object(client,'image_update',side_effect=update), \
                patch.object(client,'promote',side_effect=promote),patch('builtins.input',side_effect=AssertionError('no prompt')), \
                contextlib.redirect_stdout(out),contextlib.redirect_stderr(io.StringIO()):
            code=client.main(['--instance','instance','update','--all'])
        return code,out.getvalue(),steps

    def test_all_runs_each_step_and_stops_at_the_first_step_that_does_not_complete(self):
        code,out,steps=self.run_all([0]*20)
        self.assertEqual((code,len(steps)),(0,15));self.assertIn('kk update --all completed all 15 steps.',out)
        self.assertNotIn(('update','k002-iot'),steps)
        self.assertIn(' 14. kk update k002-ops\n',out)
        code,out,steps=self.run_all([0,0,1])
        self.assertEqual((code,steps[-1]),(1,('update','k001-iot')))
        self.assertEqual(out.splitlines()[-1],'kk update --all stopped at step 3 of 15 (kk update k001-iot). Correct the cause, then run kk update --all again.')

    def test_all_refuses_a_target_and_the_options_of_one_job(self):
        for argv in (['update','--all','k001-air'],['update','--all','--detach'],['update','--all','--cancel'],['update'],['update','--all','--yes']):
            with self.assertRaises(SystemExit),contextlib.redirect_stderr(io.StringIO()):client.main(['--instance','instance',*argv])


if __name__=='__main__':unittest.main()
