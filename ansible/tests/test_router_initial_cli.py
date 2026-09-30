"""The controller issues first-install authority only after bounded staging."""
from contextlib import nullcontext
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_router_candidate as fixture_module
from test_router_template_cli import load_cli
from test_router_updates import ENGINE, release
import router_initial_contact as contact
import router_generations as generations


class InitialCliTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        fixture = fixture_module.CandidateTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.first = {'key':'ssh-ed25519 YQ==', 'backend_address':'192.0.2.2',
                      'backend_source_address':'192.0.2.1', 'backend_prefix':24}
        fixture.job.update(mode='initial-install', first_contact=self.first)
        fixture.request['files']['etc/nftables.nft'] = (
            'table inet filter {\n    chain input {\n'
            '        type filter hook input priority 0; policy drop;\n    }\n}\n')
        self.prepared = fixture.prepare()
        self.operation, self.template = fixture.job['operation_id'], 'c'*24
        self.release = release()
        self.selection = self.cli.router_updates.seal({
            'kind':'klokast.router-bootstrap-input-selection.v1', 'engine_commit':ENGINE,
            'inputs_sha256':self.release['inputs']['inputs_sha256'], 'replacement_authorized':False})
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache, self.state = self.root / 'cache', self.root / 'state'
        self.cache.mkdir(); self.state.mkdir()
        self.source = self.cache / ('e'*24)
        self.source.mkdir()
        self.write(self.source / 'inputs.json', self.release['inputs'])
        self.write(self.source / 'selection.json', self.selection)
        template = self.state / self.template
        template.mkdir()
        self.write(template / 'release.json', self.release)
        self.write(template / 'candidate.json', {'test':'qualified-template'})
        rendered = self.root / 'rendered'
        rendered.mkdir()
        self.write(rendered / 'personalization.json', fixture.request)
        self.rendered = {'evidence_directory':str(rendered), 'registry_sha256':'f'*64}
        self.events = []
        self.attempt = 0
        self.boot = Mock(side_effect=self.stage_boot)
        for target, name, value in (
                (self.cli, 'CACHE', self.cache), (self.cli, 'STATE', self.state),
                (self.cli.transport, 'require_controller', Mock()),
                (self.cli.transport, 'approved_engine', Mock(return_value=ENGINE)),
                (self.cli.transport, 'installation_lock', lambda:nullcontext()),
                (self.cli.transport, 'command', self.command),
                (self.cli, 'initial_template_selection', Mock(return_value=self.selection['receipt_sha256'])),
                (self.cli.router_template_inputs, 'release', Mock(return_value=self.release)),
                (self.cli, 'render_candidate', Mock(return_value=self.rendered)),
                (self.cli, 'first_contact_details', Mock(return_value=self.first)),
                (self.cli, 'candidate_boot', self.boot),
                (self.cli.secrets, 'token_hex', self.token)):
            context = patch.object(target, name, value)
            context.start(); self.addCleanup(context.stop)

    def write(self, path, value):
        path.write_text(json.dumps(value))

    def token(self, size):
        if size == 12:
            return self.operation
        self.attempt += 1
        return f'{self.attempt:012x}'

    def stage_boot(self, source, work):
        boot = work / 'boot'
        boot.mkdir()
        result = {}
        for name in ('kernel', 'initramfs'):
            data = (name + ' synthetic').encode()
            (boot / name).write_bytes(data)
            (boot / name).chmod(0o600)
            result[name] = {'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()}
        return result

    def command(self, argv, **kwargs):
        args = [str(value) for value in argv]
        if args[0] == 'git':
            return ENGINE if 'rev-parse' in args else ''
        if Path(args[0]).name == 'platform-resources':
            return json.dumps({'registry_sha256':self.rendered['registry_sha256']})
        self.assertEqual(args[0], 'ansible-playbook')
        playbook = next(Path(arg).name for arg in args if arg.endswith('.yml'))
        arguments = json.loads(Path(args[-1][1:]).read_text())
        self.events.append(playbook)
        if playbook == '74-router-initial-preparation-stage.yml':
            self.assertNotIn('router_initial_grant', arguments)
            return ''
        if playbook == '74-router-initial-boot-stage.yml':
            value = {**arguments['router_initial_boot_base'], 'xen':{
                'uuid':arguments['router_initial_xen_uuid'], 'memory':512, 'vcpus':1,
                'vif':['bridge=br-wan,mac=00:16:3e:50:00:01']}}
            self.write(self.state / self.operation / 'initial-boot-request.json', value)
            return ''
        if playbook == '74-router-initial-boot-start.yml':
            value = json.loads((self.state / self.operation / 'initial-boot-request.json').read_text())
            grant = arguments['router_initial_boot_grant']
            self.assertEqual(grant['request_sha256'], generations.digest(value))
            first = json.loads((self.state / self.operation / 'initial-preparation-result.json').read_text())
            self.write(self.state / self.operation / 'initial-boot-result.json', {
                'kind':'klokast.router-initial-boot-result.v1','box':'boxa',
                'operation_id':self.operation,'status':'running-first-contact',
                'request_sha256':grant['request_sha256'],
                'intent_sha256':'d'*64,'xen_uuid':value['xen']['uuid'],
                'disk':first['installation']['disk']})
            return ''
        if playbook == '74-router-initial-enrollment-probe.yml':
            self.assertEqual(arguments['router_initial_host_key_alias'],'router-initial-' + self.operation)
            return ''
        if playbook == '74-router-initial-enrollment-begin.yml':
            self.write(self.state / self.operation / 'initial-enrollment-begin.json',{
                'kind':'klokast.router-initial-enrollment-begin.v1','box':'boxa',
                'operation_id':self.operation,'attempt':'b'*24,'intent_sha256':'c'*64,
                'xen_uuid':json.loads((self.state / self.operation / 'initial-xen.json').read_text())['uuid'],
                'disk':json.loads((self.state / self.operation / 'initial-preparation-result.json').read_text())['installation']['disk'],
                'mint_permitted':self.events.count('74-router-initial-enrollment-begin.yml') == 1})
            return ''
        if playbook == '74-router-initial-enroll.yml':
            self.write(self.state / self.operation / 'initial-enrollment-guest.json',{
                'kind':'klokast.router-initial-enrollment-guest.v1','box':'boxa',
                'operation_id':self.operation,'attempt':arguments['router_initial_attempt'],
                'machine_id':'nExactMachine','hostname':'boxa-router','tags':['tag:vm'],
                'ssh':True,'state_sha256':'a'*64})
            return ''
        if playbook == '74-router-initial-enrollment-finish.yml':
            guest = arguments['router_initial_guest_receipt']
            prepared_installation = json.loads((self.state / self.operation / 'initial-preparation-result.json').read_text())['installation']
            enrolled_installation = generations.seal({
                **{key:value for key,value in prepared_installation.items() if key != 'record_sha256'},
                'stage':'enrolled','enrollment_sha256':generations.digest(guest),
                'machine_id':guest['machine_id']})
            self.write(self.state / self.operation / 'initial-enrollment-result.json',{
                'kind':'klokast.router-initial-enrollment-result.v1','box':'boxa',
                'operation_id':self.operation,'status':'enrolled-first-contact',
                'guest_sha256':generations.digest(guest),'machine_id':guest['machine_id'],
                'state_sha256':guest['state_sha256'],'installation':enrolled_installation})
            return ''
        if playbook == '74-router-initial-finalization-stage.yml':
            self.assertNotIn('router_final_stop_grant',arguments)
            self.assertNotIn('router_final_run_grant',arguments)
            return ''
        if playbook == '74-router-initial-finalization-stop.yml':
            self.assertIn('74-router-initial-finalization-stage.yml',self.events)
            enrolled = json.loads((self.state / self.operation / 'initial-enrollment-result.json').read_text())
            self.assertEqual(arguments['router_final_stop_grant']['installation_sha256'],
                             enrolled['installation']['record_sha256'])
            self.write(self.state / self.operation / 'initial-stop-result.json',{
                'kind':'klokast.router-initial-stop-result.v1','box':'boxa',
                'operation_id':self.operation,'status':'stopped-for-offline-finalization',
                'disk':enrolled['installation']['disk']})
            return ''
        if playbook == '74-router-initial-finalization-run.yml':
            self.assertEqual(self.events[-2],'74-router-initial-finalization-stop.yml')
            enrolled = json.loads((self.state / self.operation / 'initial-enrollment-result.json').read_text())
            receipt = {'kind':'klokast.router-initial-finalization-result.v1',
                'operation_id':self.operation,'success':True,'machine_id':'nExactMachine',
                'finalized':{'packages':self.release['runtime_packages'],
                             'tests':self.release['runtime_tests']},
                'state':{name:{'sha256':letter*64} for name,letter in (
                    ('var/lib/dhcpcd/duid','a'),('var/lib/dhcpcd/secret','b'),
                    ('etc/ssh/ssh_host_rsa_key','c'),
                    ('etc/ssh/ssh_host_ecdsa_key','d'),
                    ('etc/ssh/ssh_host_ed25519_key','e'))}}
            self.write(self.state / self.operation / 'initial-finalization-result.json',receipt)
            self.write(self.state / self.operation / 'initial-finalization-complete.json',{
                'kind':'klokast.router-initial-finalization-complete.v1','box':'boxa',
                'operation_id':self.operation,'status':'offline-finalized',
                'disk':enrolled['installation']['disk'],
                'result_sha256':generations.digest(receipt)})
            return ''
        if playbook == '74-router-initial-final-boot.yml':
            complete = json.loads((self.state / self.operation / 'initial-finalization-complete.json').read_text())
            self.assertEqual(arguments['router_final_boot_grant']['request_sha256'],
                             generations.digest(complete))
            self.write(self.state / self.operation / 'initial-final-boot-result.json',{
                'kind':'klokast.router-initial-final-boot-result.v1','box':'boxa',
                'operation_id':self.operation,'status':'running-unverified',
                'complete_sha256':generations.digest(complete),'disk':complete['disk']})
            return ''
        if playbook == '74-router-initial-final-verify.yml':
            expected = arguments['router_initial_expected']
            self.assertEqual(expected['kind'],'klokast.router-initial-runtime-expected.v1')
            self.assertEqual(expected['machine_id'],'nExactMachine')
            self.assertEqual(len(expected['identity_files']),5)
            self.write(self.state / self.operation / 'initial-runtime-verification.json',{
                'kind':'klokast.router-initial-runtime-verification.v1',
                'box':'boxa','operation_id':self.operation,
                'expected_sha256':expected['record_sha256'],
                'release_sha256':self.release['receipt_sha256'],
                'machine_id':'nExactMachine','status':'verified',
                'services':True,'packages':True,'identity':True,'management':True,
                'dom0':True})
            return ''
        if playbook == '74-router-initial-accept-stage.yml':
            expected = arguments['router_initial_expected']
            verified = arguments['router_initial_verification']
            self.assertEqual(verified['expected_sha256'],expected['record_sha256'])
            self.assertNotIn('router_initial_accept_grant',arguments)
            return ''
        if playbook == '74-router-initial-accept-run.yml':
            verified = json.loads((self.state / self.operation / 'initial-runtime-verification.json').read_text())
            prepared = json.loads((self.state / self.operation / 'preparation-result.json').read_text())['prepared']
            enrolled = json.loads((self.state / self.operation / 'initial-enrollment-result.json').read_text())['installation']
            xen = json.loads((self.state / self.operation / 'initial-boot-request.json').read_text())['xen']
            boot = {name:{'path':'/mnt/dom0_data/klokast-router-updates/generations/' +
                self.operation + '/' + name,'sha256':self.release['artifacts'][name],
                'bytes':100} for name in ('kernel','initramfs')}
            generation = generations.seal({'kind':'klokast.router-generation.v1',
                'box':'boxa','role':'router','generation_id':self.operation,
                'origin':'template','engine_commit':ENGINE,
                'alpine_branch':self.release['inputs']['branch'],
                'disk':enrolled['disk'],'boot':boot,'xen':xen,
                'packages':self.release['runtime_packages'],
                'kernel_release':self.release['kernel_release'],
                'accounts':prepared['accounts'],
                'configuration_files':prepared['configuration_files'],
                'tailscale':prepared['tailscale'],
                'evidence_sha256':generations.digest(verified)})
            generations.generation(generation,'boxa')
            installation = generations.seal({
                **{key:value for key,value in enrolled.items() if key != 'record_sha256'},
                'stage':'verified','generation_sha256':generation['record_sha256']})
            accepted = generations.seal({'kind':'klokast.router-assignment.v1',
                'box':'boxa','role':'router','current_sha256':generation['record_sha256'],
                'previous_sha256':None,'operation_id':self.operation,
                'engine_commit':ENGINE,
                'policy_sha256':self.cli.router_records.INITIAL_AUTHORITY_SHA256,
                'evidence_sha256':installation['record_sha256']})
            result = self.state / self.operation
            self.write(result / 'initial-generation.json',generation)
            self.write(result / 'initial-verified-installation.json',installation)
            self.write(result / 'initial-accepted.json',accepted)
            self.write(result / 'initial-acceptance-result.json',{
                'kind':'klokast.router-initial-acceptance-result.v1','box':'boxa',
                'operation_id':self.operation,'status':'accepted',
                'generation_sha256':generation['record_sha256'],
                'assignment_sha256':accepted['record_sha256']})
            return ''
        self.assertEqual(playbook, '74-router-initial-prepare.yml')
        self.assertEqual(self.events[-2], '74-router-initial-preparation-stage.yml')
        request = json.loads((self.cache / ('initial-' + self.operation) / 'request.json').read_text())
        self.assertEqual(arguments['router_initial_grant']['request_sha256'], generations.digest(request))
        native_result = {'kind':'klokast.router-candidate-preparation-result.v1',
            'operation_id':self.operation, 'inputs_sha256':request['inputs_sha256'],
            'job_sha256':request['job_sha256'], 'success':True, 'prepared':self.prepared,
            'first_contact':{
                'kind':'klokast.router-first-contact.v2',
                'authorized_key_sha256':contact.digest_bytes((self.first['key']+'\n').encode()),
                'interfaces_sha256':contact.digest_bytes(contact.first_contact_interfaces('192.0.2.2', 24).encode()),
                'firewall_sha256':contact.digest_bytes(contact.first_contact_firewall(
                    json.loads((self.root / 'rendered/personalization.json').read_text())['files']['etc/nftables.nft'],
                    '192.0.2.1', '192.0.2.2', 24).encode()),
                'sshd_config_sha256':contact.digest_bytes(contact.first_contact_sshd_config('192.0.2.2').encode()),
                'host_key_public_sha256':{'ed25519':contact.digest_bytes(b'ssh-ed25519 YQ== fixture\n')},
                'host_key_public':{'ed25519':'ssh-ed25519 YQ== fixture\n'}}}
        installation = generations.seal({
            'kind':'klokast.router-initial-installation.v1', 'box':'boxa', 'role':'router',
            'operation_id':self.operation, 'engine_commit':ENGINE,
            'selection_sha256':request['selection_sha256'], 'release_sha256':request['release_sha256'],
            'disk':{'path':'/dev/vg0/routergen_'+self.operation, 'uuid':'exact-uuid', 'bytes':2147483648},
            'stage':'prepared', 'preparation_sha256':generations.digest(native_result),
            'enrollment_sha256':None, 'generation_sha256':None, 'machine_id':None})
        result = self.state / self.operation
        self.write(result / 'initial-preparation-result.json', {
            'kind':'klokast.router-initial-preparation-result.v1', 'box':'boxa',
            'operation_id':self.operation, 'status':'initial-prepared',
            'router_started':False, 'installation':installation})
        self.write(result / 'preparation-result.json', native_result)
        return ''

    def prepare(self, operation=None):
        return self.cli.prepare_initial('boxa', self.source, self.template, operation)

    def test_staging_precedes_fresh_grant_and_resume_reuses_the_same_boot_job(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'initial-prepared')
        self.assertFalse(result['router_started'])
        pinned = Path(result['known_hosts'])
        self.assertEqual(pinned.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result['host_key_alias'], 'router-initial-' + self.operation)
        self.assertEqual(pinned.read_text(), result['host_key_alias'] + ' ssh-ed25519 YQ==\n')
        inode = pinned.stat().st_ino
        self.assertEqual(self.prepare(self.operation), result)
        self.assertEqual(pinned.stat().st_ino, inode)
        self.assertEqual(self.boot.call_count, 1)
        self.assertEqual(self.events, ['74-router-initial-preparation-stage.yml',
            '74-router-initial-prepare.yml'] * 2)

    def test_reserved_operation_can_start_once_and_reject_incomplete_state(self):
        result = self.prepare(self.operation)
        self.assertEqual(result['operation'], self.operation)
        self.assertEqual(self.boot.call_count, 1)
        self.assertEqual(self.prepare(self.operation), result)
        (self.cache / ('initial-' + self.operation) / 'request.json').unlink()
        with self.assertRaisesRegex(self.cli.UpdateError,'incomplete controller state'):
            self.prepare(self.operation)

    def test_reserved_operation_retries_after_local_boot_staging_interrupts(self):
        attempts=[]
        def interrupted(source,work):
            attempts.append(work)
            if len(attempts)==1:
                raise RuntimeError('interrupted local boot assembly')
            return self.stage_boot(source,work)
        self.boot.side_effect=interrupted
        with self.assertRaisesRegex(RuntimeError,'interrupted local boot assembly'):
            self.prepare(self.operation)
        self.assertFalse((self.cache / ('initial-' + self.operation)).exists())
        prepared=self.prepare(self.operation)
        self.assertEqual(prepared['operation'],self.operation)
        self.assertEqual(self.boot.call_count,2)

    def test_resume_never_overwrites_a_changed_or_unsafe_host_pin(self):
        result = self.prepare()
        pinned = Path(result['known_hosts'])
        original = pinned.read_text()
        for change in ('content', 'permissions', 'symlink', 'hardlink'):
            with self.subTest(change=change):
                pinned.unlink()
                pinned.write_text(original)
                pinned.chmod(0o600)
                if change == 'content':
                    pinned.write_text(original + '# changed\n')
                elif change == 'permissions':
                    pinned.chmod(0o644)
                elif change == 'symlink':
                    pinned.unlink()
                    pinned.symlink_to(self.root / 'absent')
                else:
                    os.link(pinned, self.root / 'extra-link')
                with self.assertRaisesRegex(self.cli.UpdateError, 'host-key pin.*unsafe'):
                    self.prepare(self.operation)
                self.assertEqual(pinned.is_symlink(), change == 'symlink')
        self.assertFalse((self.root / 'absent').exists())
        self.assertFalse(list(pinned.parent.glob('.router-host-*')))

    def test_first_boot_stages_approved_xen_identity_before_fresh_grant(self):
        self.prepare()
        started = self.cli.start_initial('boxa', self.operation)
        self.assertEqual(started['status'],'running-first-contact')
        self.assertEqual(started['host_key_alias'],'router-initial-' + self.operation)
        self.assertEqual(self.events[-2:],['74-router-initial-boot-stage.yml',
                                           '74-router-initial-boot-start.yml'])
        identity = json.loads((self.state / self.operation / 'initial-xen.json').read_text())
        self.assertEqual(self.cli.start_initial('boxa', self.operation), started)
        self.assertEqual(json.loads((self.state / self.operation / 'initial-xen.json').read_text()), identity)

    def test_first_boot_refuses_changed_compiler_before_staging(self):
        self.prepare()
        self.events.clear()
        self.rendered['registry_sha256'] = '0'*64
        with self.assertRaisesRegex(self.cli.UpdateError,'compiler inputs changed'):
            self.cli.start_initial('boxa', self.operation)
        self.assertEqual(self.events,[])

    def test_enrollment_records_one_attempt_and_retries_without_mint_authority(self):
        self.prepare()
        self.cli.start_initial('boxa',self.operation)
        key_dir = self.root / '.ssh'
        key_dir.mkdir(mode=0o700)
        for name in ('github-klokast-codex','known_hosts'):
            path = key_dir / name
            path.write_text('synthetic controller key\n')
            path.chmod(0o600)
        with patch.object(Path,'home',return_value=self.root):
            enrolled = self.cli.enroll_initial('boxa',self.operation)
            self.assertEqual(enrolled['status'],'enrolled-first-contact')
            self.assertEqual(enrolled['machine_id'],'nExactMachine')
            self.assertEqual(self.cli.enroll_initial('boxa',self.operation),enrolled)
        self.assertEqual(self.events[-4:],[
            '74-router-initial-enrollment-probe.yml',
            '74-router-initial-enrollment-begin.yml',
            '74-router-initial-enroll.yml',
            '74-router-initial-enrollment-finish.yml'])

    def test_finalization_stages_before_stop_and_reuses_exact_job(self):
        self.prepare()
        self.cli.start_initial('boxa',self.operation)
        key_dir = self.root / '.ssh'
        key_dir.mkdir(mode=0o700)
        for name in ('github-klokast-codex','known_hosts'):
            path = key_dir / name
            path.write_text('synthetic controller key\n')
            path.chmod(0o600)
        with patch.object(Path,'home',return_value=self.root):
            self.cli.enroll_initial('boxa',self.operation)
        with patch.object(self.cli.vm_template_inputs,'bootstrap',
                          side_effect=lambda source,output,guest,**kwargs:
                              self.stage_boot(source,output.parent)) as bootstrap:
            result = self.cli.finalize_initial('boxa',self.operation)
            self.assertEqual(result['status'],'offline-finalized')
            self.assertFalse(result['router_started'])
            self.assertEqual(self.events[-3:],[
                '74-router-initial-finalization-stage.yml',
                '74-router-initial-finalization-stop.yml',
                '74-router-initial-finalization-run.yml'])
            self.assertEqual(self.cli.finalize_initial('boxa',self.operation),result)
            self.assertEqual(bootstrap.call_count,1)
            booted = self.cli.boot_final_initial('boxa',self.operation)
            self.assertEqual(booted['status'],'running-unverified')
            self.assertEqual(self.events[-1],'74-router-initial-final-boot.yml')
            verified = self.cli.verify_initial('boxa',self.operation)
            self.assertEqual(verified['status'],'verified')
            self.assertEqual(self.events[-1],'74-router-initial-final-verify.yml')
            accepted = self.cli.accept_initial('boxa',self.operation)
            self.assertEqual(accepted['status'],'accepted')
            self.assertEqual(self.events[-2:],[
                '74-router-initial-accept-stage.yml',
                '74-router-initial-accept-run.yml'])
            guest_path = self.state / self.operation / 'initial-enrollment-guest.json'
            guest = json.loads(guest_path.read_text())
            self.write(guest_path,{**guest,'machine_id':'nChangedMachine'})
            previous = len(self.events)
            with self.assertRaisesRegex(self.cli.UpdateError,'exact final boot evidence'):
                self.cli.verify_initial('boxa',self.operation)
            self.assertEqual(len(self.events),previous)

    def test_finalization_refuses_changed_compiler_before_stop(self):
        self.prepare()
        self.cli.start_initial('boxa',self.operation)
        key_dir = self.root / '.ssh'
        key_dir.mkdir(mode=0o700)
        for name in ('github-klokast-codex','known_hosts'):
            path = key_dir / name
            path.write_text('synthetic controller key\n')
            path.chmod(0o600)
        with patch.object(Path,'home',return_value=self.root):
            self.cli.enroll_initial('boxa',self.operation)
        self.events.clear()
        self.rendered['registry_sha256'] = '0'*64
        with self.assertRaisesRegex(self.cli.UpdateError,'compiler inputs changed'):
            self.cli.finalize_initial('boxa',self.operation)
        self.assertEqual(self.events,[])

    def test_changed_selection_after_staging_cannot_issue_an_allocation_grant(self):
        with patch.object(self.cli, 'initial_template_selection',
                          side_effect=[self.selection['receipt_sha256'], '1'*64]):
            with self.assertRaisesRegex(self.cli.UpdateError, 'authority changed during staging'):
                self.prepare()
        self.assertEqual(self.events, ['74-router-initial-preparation-stage.yml'])
        self.assertFalse(list((self.state / self.operation).glob('initial-prepare-*')))

    def test_changed_compiler_inputs_cannot_reuse_a_prepared_operation(self):
        self.prepare()
        self.events.clear()
        self.rendered['registry_sha256'] = '0'*64
        with self.assertRaisesRegex(self.cli.UpdateError, 'retry differs'):
            self.prepare(self.operation)
        self.assertEqual(self.events, [])

    def test_unpromoted_engine_stops_before_creating_preparation_resources(self):
        with patch.object(self.cli.transport, 'approved_engine', return_value='b'*40):
            with self.assertRaisesRegex(self.cli.UpdateError, 'exact activated engine'):
                self.prepare()
        self.assertFalse((self.cache / ('initial-' + self.operation)).exists())
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
