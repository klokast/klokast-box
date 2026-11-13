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


class Relay(unittest.TestCase):
    def test_transfer_is_relayed_by_device_and_uncertain_start_is_retried_without_import(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(client,'authorize'),patch.object(client,'controller',return_value='k002'), \
                patch.dict(client.os.environ,XDG_CONFIG_HOME=temporary):
            path=Path(temporary)/'operation.json'
            record=dict(operation='a'*24,phase='prepared',session='s',**{'from':'k002','to':'k001'})
            events=[];fail=True
            def remote(user,host,argv,**kwargs):
                events.append((user,host,argv[1]))
                if user=='smith':return {'released':False}
                self.assertEqual(user,'agent')
                if argv[1]=='inspect':return {'cwd':'/home/agent/src/klokast/klokast-box'}
                if argv[1]=='ready':return {'account_fingerprint':'fixture account'}
                if argv[1]=='export':return {'only':'selected discussion'}
                if argv[1]=='import':self.assertEqual(json.loads(kwargs['data']),{'only':'selected discussion'})
                if argv[1]=='start' and fail:raise RuntimeError('uncertain destination start')
                return {}
            with patch.object(client,'remote',side_effect=remote):
                with self.assertRaisesRegex(RuntimeError,'uncertain'):client.handoff(record,path,'instance','fixture.ts.net',attach=False)
                self.assertEqual(record['phase'],'imported');fail=False
                client.handoff(record,path,'instance','fixture.ts.net',attach=False)
            self.assertEqual(sum(action=='export' for _,_,action in events),1)
            self.assertEqual(sum(action=='import' for _,_,action in events),1)
            self.assertEqual(record['phase'],'complete')
            with client.air.settings('fixture.ts.net') as settings:self.assertEqual(settings['last'],'k001')

    def test_destination_failure_never_stops_source(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(client,'authorize'):
            record=dict(operation='a'*24,phase='prepared',session='s',**{'from':'k002','to':'k001'})
            calls=[]
            def remote(user,host,argv,**kwargs):
                calls.append(argv[1])
                if argv[1]=='inspect':return {'cwd':'/home/agent/src/work'}
                raise RuntimeError('destination needs sign-in')
            with patch.object(client,'remote',side_effect=remote),self.assertRaisesRegex(RuntimeError,'sign-in'):
                client.handoff(record,Path(temporary)/'record.json','instance','fixture.ts.net',attach=False)
            self.assertNotIn('export',calls)
            self.assertEqual(record['phase'],'prepared')

    def test_account_mismatch_never_exports_source(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(client,'authorize'):
            record=dict(operation='a'*24,phase='prepared',session='s',**{'from':'k002','to':'k001'})
            calls=[]
            def remote(user,host,argv,**kwargs):
                calls.append(argv[1])
                if argv[1]=='inspect':return {'cwd':'/home/agent/src/work'}
                if argv[1]=='ready':return {'account_fingerprint':host}
                self.fail('source must remain unchanged on account mismatch')
            with patch.object(client,'remote',side_effect=remote),self.assertRaisesRegex(RuntimeError,'same ChatGPT account'):
                client.handoff(record,Path(temporary)/'record.json','instance','fixture.ts.net',attach=False)
            self.assertNotIn('export',calls)
            self.assertEqual(record['phase'],'prepared')

    def test_maintenance_stops_for_other_work_then_switches_and_returns(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(client,'controller',return_value='k002'), \
                patch.object(client.air,'read_json',return_value={'controllers':{'active':'k002','standby':'k001'}}), \
                patch.object(client.air,'choose',return_value=('k001',False)), \
                patch.dict(client.os.environ,XDG_CONFIG_HOME=temporary):
            path=Path(temporary)/'maintenance.json'
            record=dict(action='maintain',phase='prepared',operation='c'*24,box='k002',session='s',roles=['air','ops'],**{'return':True})
            events=[];busy=True
            def step(instance,record,action,role=None):
                events.append((action,role))
                return {'state':'skipped' if role=='air' and busy else 'complete'}
            def move(record,*args,**kwargs):events.append(('handoff',record['from'],record['to']))
            def switch(record,path,origin,destination):events.append(('switch',origin,destination))
            with patch.object(client,'maintenance_step',side_effect=step),patch.object(client,'handoff',side_effect=move), \
                    patch.object(client,'switch',side_effect=switch):
                with self.assertRaisesRegex(RuntimeError,'still has work'):
                    client.maintain(record,path,'instance','fixture.ts.net',['k001','k002'])
                self.assertEqual(record['phase'],'update-air')
                self.assertFalse(any(event[0]=='switch' for event in events))
                busy=False
                self.assertEqual(client.maintain(record,path,'instance','fixture.ts.net',['k001','k002']),'k002')
            self.assertEqual([event for event in events if event[0]=='handoff'],[('handoff','k002','k001'),('handoff','k001','k002')])
            self.assertEqual([event for event in events if event[0]=='switch'],[('switch','k002','k001'),('switch','k001','k002')])
            self.assertEqual(record['phase'],'complete')


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

    def test_update_follows_each_stage_until_completion(self):
        waiting=dict(operation='c'*24,box='k001',state='waiting',reason='k001-air tmux sessions are attached: waiting',attached=1,busy_processes=0,checks=[])
        idle=['k001-air tmux sessions are detached: OK','Discussions on k001-air are not active: OK']
        locked=dict(waiting,reason='checking that no other update work is ongoing',checks=idle);del locked['attached'],locked['busy_processes']
        done=dict(locked,state='complete',reason='update verified',result={'image':'d'*24},checks=idle+['No other update work is ongoing: OK'])
        code,out,calls=self.run_update([],[waiting,dict(waiting),locked,done])
        self.assertEqual(code,0)
        self.assertIn('Joined the existing update job for k001-air',out)
        self.assertEqual(out.count('attached clients: 1'),1)
        self.assertIn('waiting: checking that no other update work is ongoing',out)
        for check in idle+['No other update work is ongoing: OK']:self.assertEqual(out.count(check),1)
        self.assertIn('complete: update verified; image '+'d'*24,out)
        self.assertEqual([c[1] for c in calls],['start','status','status','status'])
        self.assertTrue(all(c[-1]=='c'*24 for c in calls[1:]))

    def test_update_shows_the_published_stage(self):
        updating=dict(operation='c'*24,box='k001',state='updating',reason='preparing and verifying the replacement image',
                      progress='image build: build the candidate in a networkless Xen guest on k001')
        code,out,_calls=self.run_update(['--status'],[updating])
        self.assertEqual(code,0);self.assertIn('updating: image build: build the candidate in a networkless Xen guest on k001',out)
        self.assertNotIn('preparing and verifying',out)

    def test_status_prints_the_recorded_times_and_not_the_time_of_the_request(self):
        check='No other update work is ongoing: OK'
        updating=dict(operation='c'*24,box='k001',state='updating',reason='preparing',checks=[check],check_times={check:3600},
                      changed_at=3700,progress='booting the replacement k001-air',progress_at=86400+7384)
        gmtime=client.time.gmtime
        with patch.object(client.time,'gmtime',side_effect=lambda seconds=86400+9000:gmtime(seconds)):
            _code,out,_calls=self.run_update(['--status'],[updating])
        self.assertIn('1970-01-01 01:00:00 UTC '+check,out)
        self.assertIn('\n02:03:04 UTC updating: booting the replacement k001-air',out)
        # A record without recorded times must not show the time of the request.
        _code,out,_calls=self.run_update(['--status'],[dict(operation='c'*24,box='k001',state='waiting',reason='x',checks=[check])])
        self.assertIn('--:--:-- UTC '+check,out);self.assertIn('--:--:-- UTC waiting: x',out)

    def test_failed_job_prints_the_error_and_the_resume_command(self):
        code,out,_calls=self.run_update(['--resume','c'*24],[dict(operation='c'*24,box='k001',state='updating',reason='x'),
            dict(operation='c'*24,box='k001',state='failed',error='AIR became busy')])
        self.assertEqual(code,1);self.assertIn('failed: AIR became busy',out);self.assertIn('--resume '+'c'*24,out)
        self.assertNotIn('Joined',out)

    def test_detach_and_status_do_not_follow(self):
        record=dict(operation='c'*24,box='k001',state='waiting',reason='checking that k001-air tmux sessions are detached')
        code,out,calls=self.run_update(['--detach'],[record])
        self.assertEqual((code,len(calls)),(0,1));self.assertIn('kk update k001-air --status',out)
        code,out,calls=self.run_update(['--status'],[dict(record)])
        self.assertEqual((code,[c[1] for c in calls]),(0,['status']));self.assertIn('waiting: checking that k001-air tmux sessions are detached',out)


if __name__=='__main__':unittest.main()
