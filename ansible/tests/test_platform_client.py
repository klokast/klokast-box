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
                         r'Follow it again, or restart it after an interruption: kk update k001-air\. '
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


if __name__=='__main__':unittest.main()
