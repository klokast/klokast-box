"""A final B signal needs a waiting transaction and complete service proof."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_router_template_cli import load_cli
from test_router_updates import ENGINE


class ReplacementAcceptanceCliTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.operation = 'a'*24
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.cache = Path(root.name)/'cache'
        self.state = Path(root.name)/'state'
        self.work = self.cache/('replacement-'+self.operation)
        self.result = self.state/self.operation
        self.work.mkdir(parents=True,mode=0o700)
        self.result.mkdir(parents=True,mode=0o700)
        self.request = {'kind':'klokast.router-transaction-request.v1',
            'role':'router','box':'boxa','operation_id':self.operation,
            'engine_commit':ENGINE,'policy_sha256':'1'*64,
            'accepted_sha256':'2'*64,'old_sha256':'3'*64,
            'candidate_sha256':'4'*64,'cutover_seconds':900,'recovery_seconds':900}
        self.candidate = {'record_sha256':'4'*64}
        for path,value in ((self.result/'transaction-request.json',self.request),
                (self.result/'proposed-generation.json',self.candidate),
                (self.result/'preparation-result.json',{'prepared':True}),
                (self.work/'request.json',{'source':True}),
                (self.work/'candidate-job.json',{'job':True}),
                (self.work/'release.json',{'release':True})):
            path.write_text(json.dumps(value))
        self.pending = {'kind':'klokast.router-pending.v1',
            'request':self.request,'phase':'awaiting-acceptance',
            'candidate_started':True,'old_started':False,'reason':'cutover'}
        import router_generations as generations
        self.expected = generations.seal({'kind':'klokast.router-replacement-service-expected.v1',
            'box':'boxa','operation_id':self.operation,
            'request_sha256':generations.digest(self.request),
            'candidate_sha256':'4'*64,'enrollment_sha256':'5'*64,
            'finalization_sha256':'6'*64,'machine_id':'nNewRouter',
            'hostname':'boxa-router-'+self.operation,
            'addresses':['100.64.0.8']})
        import router_replacement_service as service
        self.proof = {'kind':'klokast.router-replacement-service-proof.v1',
            'box':'boxa','operation_id':self.operation,
            'expected_sha256':self.expected['record_sha256'],
            'candidate_sha256':'4'*64,'machine_id':'nNewRouter',
            'hostname':'boxa-router-'+self.operation,
            'tests':dict.fromkeys(service.TESTS,True)}
        self.events = []
        for target,name,value in ((self.cli,'CACHE',self.cache),
                (self.cli,'STATE',self.state),
                (self.cli.transport,'require_controller',mock.Mock()),
                (self.cli.transport,'approved_engine',mock.Mock(return_value=ENGINE)),
                (self.cli.transport,'installation_lock',lambda:nullcontext()),
                (self.cli.transport,'command',self.command),
                (self.cli.router_generations,'generation',mock.Mock()),
                (service,'expected',mock.Mock(return_value=self.expected))):
            patch = mock.patch.object(target,name,value)
            patch.start(); self.addCleanup(patch.stop)
        import router_replacement_finalization as finalization
        patch = mock.patch.object(finalization,'job_for',return_value={'job':'exact'})
        patch.start();self.addCleanup(patch.stop)

    def command(self,argv,**kwargs):
        args = [str(item) for item in argv]
        if args[0] == 'git':
            return ENGINE if 'rev-parse' in args else ''
        self.assertEqual(args[0],'ansible-playbook')
        playbook = next(Path(item).name for item in args if item.endswith('.yml'))
        self.events.append(playbook)
        if playbook == '74-router-replacement-final-source.yml':
            for name,value in (('dom0-final-pending',self.pending),
                    ('dom0-final-attempt',{'attempt':True}),
                    ('dom0-final-enrollment',{'enrolled':True,'addresses':['100.64.0.8']}),
                    ('dom0-finalization-result',{'final':True})):
                (self.result/(name+'.json')).write_text(json.dumps(value))
        elif playbook == '74-router-replacement-final-verify.yml':
            arguments = json.loads(Path(args[-1][1:]).read_text())
            self.assertEqual(arguments['router_replacement_expected'],self.expected)
            (self.result/'replacement-service-proof.json').write_text(json.dumps(self.proof))
        elif playbook == '74-router-replacement-acceptance-signal.yml':
            arguments = json.loads(Path(args[-1][1:]).read_text())
            self.assertEqual(arguments['router_replacement_proof'],self.proof)
            (self.result/'dom0-published-acceptance.json').write_text(
                json.dumps(arguments['router_replacement_acceptance']))
        else:
            self.fail('unexpected acceptance playbook: '+playbook)
        return ''

    def test_verified_final_b_is_published_once(self):
        value = self.cli.signal_replacement_acceptance('boxa',self.operation)
        self.assertEqual(value['status'],'published')
        self.assertEqual(value['machine_id'],'nNewRouter')
        self.assertEqual(self.events,[
            '74-router-replacement-final-source.yml',
            '74-router-replacement-final-verify.yml',
            '74-router-replacement-acceptance-signal.yml'])

    def test_wrong_phase_or_failed_service_never_signals(self):
        self.pending['phase'] = 'checking-final-candidate'
        with self.assertRaisesRegex(self.cli.UpdateError,'not waiting'):
            self.cli.signal_replacement_acceptance('boxa',self.operation)
        self.assertEqual(self.events,['74-router-replacement-final-source.yml'])
        self.events.clear()
        self.pending['phase'] = 'awaiting-acceptance'
        self.proof['tests']['management'] = False
        with self.assertRaisesRegex(RuntimeError,'full-service proof'):
            self.cli.signal_replacement_acceptance('boxa',self.operation)
        self.assertNotIn('74-router-replacement-acceptance-signal.yml',self.events)


if __name__ == '__main__':
    unittest.main()
