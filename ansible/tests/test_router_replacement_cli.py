"""The controller grants replacement allocation only after staged, fresh evidence."""
from contextlib import nullcontext
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_router_template_cli import load_cli
from test_router_updates import ENGINE, release
import test_router_candidate as candidate_fixture


class ReplacementCliTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        fixture = candidate_fixture.CandidateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.job = copy.deepcopy(fixture.job)
        self.prepared = fixture.prepare()
        self.release = release()
        self.operation = self.job['operation_id']
        self.check = 'd'*24
        self.template = 'c'*24
        self.source_operation = 'e'*24
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache,self.state = self.root/'cache',self.root/'state'
        self.cache.mkdir(); self.state.mkdir()
        checked = self.state/self.check
        checked.mkdir(mode=0o700)
        self.binding = {'kind':'klokast.router-check-source.v1','box':'boxa',
            'report_sha256':'d'*64,'source_operation':self.source_operation,
            'inputs_sha256':self.release['inputs']['inputs_sha256'],'engine_commit':ENGINE}
        (checked/'candidate-source.json').write_text(json.dumps(self.binding))
        self.source = self.cache/self.source_operation
        self.source.mkdir(mode=0o700)
        (self.source/'inputs.json').write_text(json.dumps(self.release['inputs']))
        template = self.state/self.template
        template.mkdir(mode=0o700)
        self.candidate = {'inputs_sha256':self.release['inputs']['inputs_sha256']}
        (template/'candidate.json').write_text(json.dumps(self.candidate))
        (template/'release.json').write_text(json.dumps(self.release))
        rendered = self.root/'rendered'
        rendered.mkdir(mode=0o700)
        (rendered/'personalization.json').write_text(json.dumps(self.job['personalization']))
        self.rendered = {'evidence_directory':str(rendered),'registry_sha256':'f'*64}
        self.context = {'report':{'report_sha256':'d'*64},'binding':self.binding,
            'inputs':self.release['inputs'],'frozen':self.source,
            'assignment':{'record_sha256':'a'*64},
            'generation':{'record_sha256':'b'*64},
            'accepted':{'box':'boxa','role':'router','generation':'b'*64},
            'accepted_profile':{},'policy':{'enabled':True},
            'policy_sha256':'f'*64,'live':{'configuration_verified':True}}
        self.events = []
        self.authority = Mock()
        self.boot = Mock(side_effect=self.stage_boot)
        self.context_check = Mock(return_value=self.context)
        self.proposed = {'record_sha256':'c'*64,'boot':{}}
        self.preflight = {'kind':'klokast.router-candidate-preflight.v1',
                          'candidate_sha256':'c'*64,'candidate_booted':False}
        self.next_token = 0
        for target,name,value in (
                (self.cli,'CACHE',self.cache),(self.cli,'STATE',self.state),
                (self.cli.transport,'require_controller',Mock()),
                (self.cli.transport,'approved_engine',Mock(return_value=ENGINE)),
                (self.cli.transport,'installation_lock',lambda:nullcontext()),
                (self.cli.transport,'command',self.command),
                (self.cli.router_template_inputs,'release',Mock(return_value=self.release)),
                (self.cli,'render_candidate',Mock(return_value=self.rendered)),
                (self.cli,'candidate_boot',self.boot),
                (self.cli,'replacement_context',self.context_check),
                (self.cli.secrets,'token_hex',self.token)):
            context = patch.object(target,name,value)
            context.start(); self.addCleanup(context.stop)

    def token(self,size):
        self.next_token += 1
        return f'{self.next_token:0{size*2}x}'

    def stage_boot(self,source,work):
        boot = work/'boot'
        boot.mkdir()
        result = {}
        for name in ('kernel','initramfs'):
            data = (name+' synthetic').encode()
            (boot/name).write_bytes(data)
            (boot/name).chmod(0o600)
            result[name] = {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        return result

    def command(self,argv,**kwargs):
        args = [str(value) for value in argv]
        if args[0] == 'git':
            return ENGINE if 'rev-parse' in args else ''
        if Path(args[0]).name == 'platform-resources':
            return json.dumps({'registry_sha256':self.rendered['registry_sha256']})
        self.assertEqual(args[0],'ansible-playbook')
        playbook = next(Path(arg).name for arg in args if arg.endswith('.yml'))
        arguments = json.loads(Path(args[-1][1:]).read_text())
        self.events.append(playbook)
        if playbook == '74-router-replacement-preparation-stage.yml':
            self.assertNotIn('router_replacement_grant',arguments)
            return ''
        if playbook == '74-router-replacement-generation-stage.yml':
            self.assertNotIn('router_replacement_generation_grant',arguments)
            self.generation_selection = arguments['router_replacement_generation']
            return ''
        if playbook == '74-router-replacement-generation-run.yml':
            self.assertEqual(arguments['router_replacement_generation_grant']['selection_sha256'],
                             self.cli.router_generations.digest(self.generation_selection))
            self.assertEqual(self.context_check.call_count % 3,2)
            result = self.state/self.operation
            (result/'proposed-generation.json').write_text(json.dumps(self.proposed))
            (result/'candidate-preflight.json').write_text(json.dumps(self.preflight))
            (result/'generation-stage-result.json').write_text(json.dumps({
                'kind':'klokast.router-replacement-generation-stage-result.v1',
                'box':'boxa','operation_id':self.operation,'status':'proposed-generation-staged',
                'old_sha256':'b'*64,'candidate_sha256':'c'*64,
                'preflight_sha256':self.cli.router_generations.digest(self.preflight),
                'router_started':False,'cutover_authorized':False}))
            return ''
        self.assertEqual(playbook,'74-router-replacement-prepare.yml')
        self.assertEqual(self.events[-2],'74-router-replacement-preparation-stage.yml')
        request = json.loads((self.cache/('replacement-'+self.operation)/'request.json').read_text())
        grant = arguments['router_replacement_grant']
        self.assertEqual(grant['request_sha256'],self.cli.router_generations.digest(request))
        self.assertEqual(self.context_check.call_count % 3,2)
        self.assertEqual(self.authority.call_count,
                         self.events.count('74-router-replacement-prepare.yml'))
        disk = {'path':'/dev/vg0/routergen_'+self.operation,'uuid':'exact-uuid','bytes':2147483648}
        native = {'kind':'klokast.router-candidate-preparation-result.v1',
            'operation_id':self.operation,'inputs_sha256':request['inputs_sha256'],
            'job_sha256':request['job_sha256'],'success':True,'prepared':self.prepared}
        result = self.state/self.operation
        for name,value in (
                ('preparation-result',native),
                ('candidate-disk',{'kind':'klokast.router-candidate-disk.v1',
                    'operation_id':self.operation,'stage':'cloned',
                    'path':disk['path'],'uuid':disk['uuid']}),
                ('replacement-preparation-result',{
                    'kind':'klokast.router-replacement-preparation-result.v1','box':'boxa',
                    'operation_id':self.operation,'status':'replacement-prepared',
                    'router_started':False,'old_sha256':'b'*64,'candidate_disk':disk,
                    'preparation_sha256':self.cli.router_generations.digest(native),
                    'release_sha256':self.release['receipt_sha256']})):
            (result/(name+'.json')).write_text(json.dumps(value))
        return ''

    def prepare(self):
        import router_replacement_preparation as replacement
        with patch.object(replacement,'authority',self.authority):
            return self.cli.prepare_replacement('boxa',self.check,self.template,self.operation)

    def test_stage_precedes_grant_and_live_source_is_rechecked(self):
        result = self.prepare()
        self.assertEqual(result['status'],'replacement-prepared')
        self.assertFalse(result['router_started'])
        self.assertEqual(self.context_check.call_count,3)
        self.assertEqual(self.events,['74-router-replacement-preparation-stage.yml',
                                      '74-router-replacement-prepare.yml'])
        self.assertEqual(self.prepare(),result)
        self.assertEqual(self.boot.call_count,1)

    def test_changed_source_after_staging_never_grants_allocation(self):
        changed = {**self.context,'assignment':{'record_sha256':'0'*64}}
        self.context_check.side_effect = [self.context,changed]
        with self.assertRaisesRegex(self.cli.UpdateError,'authority changed during staging'):
            self.prepare()
        self.assertEqual(self.events,['74-router-replacement-preparation-stage.yml'])
        self.authority.assert_not_called()

    def test_changed_old_router_after_preparation_retains_disk_without_success(self):
        changed = {**self.context,'generation':{'record_sha256':'0'*64}}
        self.context_check.side_effect = [self.context,self.context,changed]
        with self.assertRaisesRegex(self.cli.UpdateError,'running router changed after preparation'):
            self.prepare()
        self.assertEqual(self.events,['74-router-replacement-preparation-stage.yml',
                                      '74-router-replacement-prepare.yml'])
        self.assertTrue((self.state/self.operation/'candidate-disk.json').is_file())

    def test_proposed_generation_stages_only_after_fresh_source_and_grant(self):
        self.prepare()
        import router_candidate_generation
        with patch.object(router_candidate_generation,'assemble',return_value=self.proposed), \
             patch.object(router_candidate_generation,'offline_preflight',return_value=self.preflight):
            result = self.cli.stage_replacement_generation('boxa',self.operation)
        self.assertEqual(result['status'],'proposed-generation-staged')
        self.assertFalse(result['router_started'])
        self.assertFalse(result['cutover_authorized'])
        self.assertEqual(result['preflight_sha256'],self.cli.router_generations.digest(self.preflight))
        self.assertEqual(self.events[-2:],['74-router-replacement-generation-stage.yml',
                                          '74-router-replacement-generation-run.yml'])

    def test_changed_dom0_preflight_cannot_report_staging_success(self):
        self.prepare()
        import router_candidate_generation
        original = self.command
        def change_preflight(argv,**kwargs):
            value = original(argv,**kwargs)
            if any(str(item).endswith('74-router-replacement-generation-run.yml') for item in argv):
                (self.state/self.operation/'candidate-preflight.json').write_text('{"changed":true}')
            return value
        with patch.object(router_candidate_generation,'assemble',return_value=self.proposed), \
             patch.object(router_candidate_generation,'offline_preflight',return_value=self.preflight), \
             patch.object(self.cli.transport,'command',side_effect=change_preflight), \
             self.assertRaisesRegex(self.cli.UpdateError,'different proposed evidence'):
            self.cli.stage_replacement_generation('boxa',self.operation)

    def test_changed_source_after_generation_stage_refuses_separate_grant(self):
        self.prepare()
        changed = {**self.context,'assignment':{'record_sha256':'0'*64}}
        self.context_check.side_effect = [self.context,changed]
        with self.assertRaisesRegex(self.cli.UpdateError,'no grant was issued'):
            self.cli.stage_replacement_generation('boxa',self.operation)
        self.assertEqual(self.events[-1],'74-router-replacement-generation-stage.yml')

    def test_changed_template_receipt_after_generation_stage_refuses_grant(self):
        self.prepare()
        original = self.command
        def change_template(argv,**kwargs):
            value = original(argv,**kwargs)
            if any(str(item).endswith('74-router-replacement-generation-stage.yml')
                   for item in argv):
                (self.state/self.template/'candidate.json').write_text('{"changed":true}')
            return value
        with patch.object(self.cli.transport,'command',side_effect=change_template):
            with self.assertRaisesRegex(self.cli.UpdateError,'no grant was issued'):
                self.cli.stage_replacement_generation('boxa',self.operation)
        self.assertEqual(self.events[-1],'74-router-replacement-generation-stage.yml')


if __name__ == '__main__':
    unittest.main()
