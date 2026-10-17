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
        with tempfile.TemporaryDirectory() as temporary,patch.object(client,'authorize'), \
                patch.dict(client.os.environ,XDG_CONFIG_HOME=temporary):
            path=Path(temporary)/'operation.json'
            record=dict(operation='a'*24,phase='prepared',session='s',**{'from':'k002','to':'k001'})
            events=[];fail=True
            def remote(user,host,argv,**kwargs):
                events.append((user,host,argv[1]))
                self.assertEqual(user,'agent')
                if argv[1]=='inspect':return {'cwd':'/home/agent/src/klokast/klokast-box'}
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


if __name__=='__main__':unittest.main()
