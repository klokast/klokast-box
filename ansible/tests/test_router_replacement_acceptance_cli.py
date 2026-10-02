"""A final B signal needs a waiting transaction and complete service proof."""
from contextlib import nullcontext
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_router_template_cli import load_cli
from test_router_updates import ENGINE
from platform_updates import UpdateError


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
            'tests':{**dict.fromkeys(service.TESTS,True),
                     'overlay_direct_ipv6':'not_required'}}
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
            overlay = arguments['router_replacement_overlay']
            self.assertEqual(args[args.index('--limit')+1], 'boxa-router' +
                (',' + overlay['peer_box'] + '-router' if overlay is not None else ''))
            (self.result/'replacement-service-proof.json').write_text(json.dumps(self.proof))
        elif playbook == '74-router-replacement-rollback-probe.yml':
            arguments = json.loads(Path(args[-1][1:]).read_text())
            self.assertEqual(arguments['router_replacement_expected'], self.expected)
            (self.result/'rollback-probe.json').write_text(json.dumps({
                'kind':'klokast.router-rollback-probe.v1', 'operation': self.operation,
                'machine_id': self.expected['machine_id'], 'expired_fixture': True,
                'dnsmasq_stopped': True, 'before_sha256':'a'*64, 'after_sha256':'b'*64,
                'fixture_sha256':'c'*64}))
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

    def test_signed_overlay_includes_only_the_recorded_peer_in_verification(self):
        self.candidate['overlay_source_sha256'] = 'f'*64
        (self.result/'proposed-generation.json').write_text(json.dumps(self.candidate))
        with mock.patch.object(self.cli, 'signed_overlay_source', return_value={
                'source_sha256': 'f'*64, 'peer_box': 'boxb'}):
            self.assertEqual(self.cli.signal_replacement_acceptance(
                'boxa', self.operation)['status'], 'published')

    def test_supervised_fault_verifies_full_b_but_never_publishes_acceptance(self):
        value = self.cli.signal_replacement_acceptance('boxa', self.operation, _test_rollback=True)
        self.assertEqual(value['status'], 'acceptance-withheld-awaiting-native-rollback')
        self.assertEqual(self.events, ['74-router-replacement-final-source.yml',
            '74-router-replacement-final-verify.yml', '74-router-replacement-rollback-probe.yml'])
        self.assertFalse((self.result/'dom0-published-acceptance.json').exists())

    def test_service_failure_prevents_the_lease_fault(self):
        self.proof['tests']['management'] = False
        with self.assertRaisesRegex(RuntimeError, 'full-service proof'):
            self.cli.signal_replacement_acceptance('boxa', self.operation, _test_rollback=True)
        self.assertNotIn('74-router-replacement-rollback-probe.yml', self.events)

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


class ReplacementCutoverDriverTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.operation = 'a'*24
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.cache = Path(root.name)/'cache'
        self.state = Path(root.name)/'state'
        work = self.cache/('replacement-'+self.operation)
        self.result = self.state/self.operation
        work.mkdir(parents=True,mode=0o700)
        self.result.mkdir(parents=True,mode=0o700)
        import router_generations as generations
        self.request = {'kind':'klokast.router-transaction-request.v1',
            'role':'router','box':'boxa','operation_id':self.operation,
            'engine_commit':ENGINE,'policy_sha256':'1'*64,
            'accepted_sha256':'2'*64,'old_sha256':'3'*64,
            'candidate_sha256':'4'*64,'cutover_seconds':900,'recovery_seconds':900}
        self.readiness = {'kind':'klokast.router-readiness.v4',
            'request_sha256':generations.digest(self.request),
            'release_sha256':'5'*64,'candidate_preflight_sha256':'6'*64,
            'compatibility_sha256':'7'*64,'copy_qualification_sha256':'8'*64,
            'gateway':'10.1.1.1'}
        records = {
            self.result/'transaction-request.json':self.request,
            self.result/'capsule.json':{'test_capsule':True, 'inputs_sha256':'f'*64},
            self.result/'proposed-generation.json':{'record_sha256':'4'*64},
            self.result/'readiness.json':self.readiness,
            self.result/'enrollment-source.json':{'old_machine_id':'nOldRouter',
                'request_sha256':generations.digest(self.request)},
            self.result/'cutover-stage-result.json':{
                'kind':'klokast.router-command-result.v1','action':'stage-cutover',
                'box':'boxa','engine_commit':ENGINE,'result':{
                    'kind':'klokast.router-cutover-staged.v2',
                    'capsule_sha256':generations.digest({'test_capsule':True, 'inputs_sha256':'f'*64}),
                    'readiness_sha256':generations.digest(self.readiness),
                    'candidate_sha256':'4'*64,'status':'records-qualified-no-cutover'}},
            work/'request.json':{'check_operation':'b'*24, 'inputs_sha256':'f'*64}}
        for path,value in records.items():
            path.write_text(json.dumps(value))
        self.context = {'assignment':{'record_sha256':'2'*64},
            'generation':{'record_sha256':'3'*64},
            'policy_sha256':'1'*64,'old_machine_id':'nOldRouter', 'frozen':work}
        self.events = []
        self.progress = [
            (None,'3'*64,False),
            ('awaiting-enrollment','3'*64,False),
            ('awaiting-acceptance','3'*64,False),
            (None,'4'*64,True)]
        for target,name,value in ((self.cli,'CACHE',self.cache),
                (self.cli,'STATE',self.state),
                (self.cli.transport,'require_controller',mock.Mock()),
                (self.cli.transport,'approved_engine',mock.Mock(return_value=ENGINE)),
                (self.cli.transport,'installation_lock',lambda:nullcontext()),
                (self.cli.transport,'command',self.command),
                (self.cli,'replacement_context',mock.Mock(return_value=self.context)),
                (self.cli.router_copy_inputs,'retain',mock.Mock(return_value={
                    'test_capsule':True, 'inputs_sha256':'f'*64})),
                (self.cli,'require_rollout_qualified',mock.Mock(return_value={'ready':True})),
                (self.cli,'check_policy_at',mock.Mock(side_effect=lambda box,engine:
                    ({'activated':True},{'signed':True},self.context.get('policy'),self.context['policy_sha256']))),
                (self.cli,'signal_replacement_enrollment',mock.Mock(return_value={})),
                (self.cli,'signal_replacement_acceptance',mock.Mock(return_value={})),
                (self.cli.time,'sleep',mock.Mock())):
            patch = mock.patch.object(target,name,value)
            patch.start(); self.addCleanup(patch.stop)

    def command(self,argv,**kwargs):
        args = [str(item) for item in argv]
        if args[0] == 'git':
            return ENGINE if 'rev-parse' in args else ''
        self.assertEqual(args[0],'ansible-playbook')
        playbook = next(Path(item).name for item in args if item.endswith('.yml'))
        self.events.append(playbook)
        arguments = json.loads(Path(args[-1][1:]).read_text())
        if playbook == '74-router-replacement-cutover-start.yml':
            self.last_grant = arguments['router_replacement_grant']
            (self.result/'worker.json').write_text(json.dumps({
                'kind':'klokast.router-replacement-worker.v1',
                'box':'boxa','operation_id':self.operation,'engine_commit':ENGINE,
                'request_sha256':arguments['router_replacement_request_sha256'],
                'ansible_job_id':'job.123'}))
        elif playbook == '74-router-replacement-progress-read.yml':
            phase,current,finished = self.progress.pop(0)
            pending = None if phase is None else {
                'kind':'klokast.router-pending.v1','request':self.request,
                'phase':phase,'candidate_started':True,'old_started':False,
                'reason':'cutover'}
            pointer = {'kind':'klokast.router-command-result.v1',
                'box':'boxa','engine_commit':ENGINE,'action':'assignment-status',
                'result':{'adopted':True,'operation':pending,
                    'assignment':{'current_sha256':current}}}
            value = {'kind':'klokast.router-replacement-progress.v1',
                'box':'boxa','operation_id':self.operation,
                'worker_job_id':'job.123','pointer':pointer,
                'worker_finished':finished,'worker_rc':0 if finished else None}
            (self.result/('progress-'+arguments['router_replacement_progress_token']+'.json')).write_text(
                json.dumps(value))
        else:
            self.fail('unexpected cutover playbook: '+playbook)
        return ''

    def test_initial_old_pointer_does_not_end_a_started_supervisor(self):
        result = self.cli.run_replacement_cutover('boxa',self.operation)
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(self.events.count('74-router-replacement-progress-read.yml'),4)
        self.cli.signal_replacement_enrollment.assert_called_once_with(
            'boxa',self.operation,_already_locked=True)
        self.cli.signal_replacement_acceptance.assert_called_once_with(
            'boxa',self.operation,_already_locked=True)

    def test_supervised_rollback_enrolls_then_withholds_acceptance_until_native_return(self):
        self.progress[-1] = (None, '3'*64, True)
        result = self.cli.run_replacement_cutover('boxa', self.operation,
                                                test_changed_state_rollback=True)
        self.assertEqual(result['status'], 'rolled-back')
        self.cli.signal_replacement_enrollment.assert_called_once_with(
            'boxa', self.operation, _already_locked=True)
        self.cli.signal_replacement_acceptance.assert_called_once_with(
            'boxa', self.operation, _already_locked=True, _test_rollback=True)

    def test_scheduled_dispatch_cannot_inject_a_rollback_fault(self):
        with self.assertRaisesRegex(UpdateError, 'forbidden in scheduled'):
            self.cli.run_replacement_cutover('boxa', self.operation,
                require_maintenance_window=True, test_changed_state_rollback=True)
        self.assertEqual(self.events, [])

    def test_scheduled_window_refusal_cannot_launch_supervisor(self):
        self.context['policy'] = {'enabled': False}
        with self.assertRaisesRegex(UpdateError, 'enabled target'):
            self.cli.run_replacement_cutover('boxa', self.operation,
                                            require_maintenance_window=True)
        self.assertEqual(self.events, [])
        self.assertFalse((self.result / 'worker.json').exists())

    def test_scheduled_window_check_runs_after_live_source_before_launch(self):
        self.context['policy'] = {'enabled': True}
        cutoff = int(dt.datetime.now(dt.timezone.utc).timestamp()) + 30
        with mock.patch.object(self.cli.router_updates, 'require_cutover_window',
                               return_value=cutoff) as window:
            result = self.cli.run_replacement_cutover('boxa', self.operation,
                                                    require_maintenance_window=True)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(window.call_count, 2)
        self.cli.require_rollout_qualified.assert_called_once()
        policy, box, observed, request = window.call_args.args
        self.assertEqual((policy, box, request), (self.context['policy'], 'boxa', self.request))
        self.assertEqual(observed.utcoffset(), dt.timedelta(0))
        self.assertEqual(self.last_grant['expires_at'], cutoff)

    def test_missing_native_readiness_cannot_write_grant_or_launch(self):
        self.context['policy'] = {'enabled': True}
        self.cli.require_rollout_qualified.side_effect = UpdateError('native proof missing')
        with mock.patch.object(self.cli.router_updates, 'require_cutover_window', return_value=9999999999):
            with self.assertRaisesRegex(UpdateError, 'native proof missing'):
                self.cli.run_replacement_cutover('boxa', self.operation, require_maintenance_window=True)
        self.assertEqual(self.events, [])
        self.assertFalse((self.result / 'cutover-start-grant.json').exists())

    def test_expired_window_after_readiness_inspection_cannot_write_grant(self):
        self.context['policy'] = {'enabled': True}
        with mock.patch.object(self.cli.router_updates, 'require_cutover_window',
                side_effect=[9999999999, UpdateError('outside start window')]) as window:
            with self.assertRaisesRegex(UpdateError, 'outside start window'):
                self.cli.run_replacement_cutover('boxa', self.operation, require_maintenance_window=True)
        self.assertEqual(window.call_count, 2)
        self.cli.require_rollout_qualified.assert_called_once()
        self.assertEqual(self.events, [])
        self.assertFalse((self.result / 'cutover-start-grant.json').exists())



if __name__ == '__main__':
    unittest.main()
