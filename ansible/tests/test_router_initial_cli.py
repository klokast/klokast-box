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
        if playbook.endswith('-stage.yml'):
            self.assertNotIn('router_initial_grant', arguments)
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
