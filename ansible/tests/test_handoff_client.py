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


if __name__=='__main__':unittest.main()
