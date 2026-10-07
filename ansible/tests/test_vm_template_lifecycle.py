"""Latest input selection and controller-side template retention orchestration."""
import contextlib
import copy
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_platform_updates import load_cli
from router_release_fixtures import branch
import platform_update_metadata as metadata
from platform_updates import (UpdateError, digest, no_application_release, BASE_BUILD_TESTS,
                              BASE_BOOT_TESTS, OPENRC_TESTS, PERSONALIZED_TESTS)

NOW = dt.datetime(2026, 10, 7, 12, tzinfo=dt.timezone.utc)


class LatestTemplateTests(unittest.TestCase):
    def test_stable_released_today_and_numeric_order_without_delay(self):
        releases = {'release_branches': [branch('v3.9'), branch('v3.24', '2026-10-07'),
                                        branch('v3.25', '2026-10-08'), {'rel_branch': 'edge'}]}
        self.assertEqual(metadata.newest_stable_branch(releases, NOW, 0), 'v3.24')
        releases['release_branches'][1]['repos'][1]['eol_date'] = '2026-10-07'
        self.assertEqual(metadata.newest_stable_branch(releases, NOW, 0), 'v3.9')

    def test_bad_metadata_cannot_choose_older_branch_silently(self):
        for value in ({}, {'release_branches': []},
                      {'release_branches': [branch('v3.23'), {'rel_branch': 'v3.24'}]},
                      {'release_branches': [branch('v3.24'), branch('v3.24')]}):
            with self.subTest(value=value), self.assertRaises(UpdateError):
                metadata.newest_stable_branch(value, NOW, 0)

    def test_inputs_only_resolves_fresh_each_time_without_cleanup(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            releases = {'release_branches': [branch('v3.24', '2026-10-07')]}
            def freeze(_directory, profile, selected, _commit):
                version = str(frozen.call_count)
                return {'inputs_sha256': version * 64, 'profile': profile['profile'],
                        'architecture': profile['architecture'], 'branch': selected,
                        'packages': [{'name': 'podman', 'version': version}]}
            with patch.object(cli, 'STATE', root), patch.object(cli, 'CACHE', root), \
                    patch.object(cli, 'require_controller'), patch.object(cli, 'command', return_value='a' * 40), \
                    patch.object(cli, 'now', return_value=NOW), \
                    patch.object(cli, 'fetch_json', return_value=(releases, 'b' * 64)) as fetch, \
                    patch.object(cli.vm_template_inputs, 'freeze', side_effect=freeze) as frozen, \
                    patch.object(cli.vm_template_inputs, 'capsule', return_value={}), \
                    patch.object(cli.vm_template_inputs, 'bootstrap', return_value={}), \
                    patch.object(cli.vm_template_inputs, 'personalization_fixture'), \
                    patch.object(cli, 'reusable_template') as reuse, \
                    patch.object(cli, 'cleanup_template') as cleanup:
                first = cli.prepare('boxa', inputs_only=True)
                second = cli.prepare('boxa', inputs_only=True)
                self.assertEqual(fetch.call_count, 2)
                self.assertEqual(frozen.call_count, 2)
                self.assertEqual(first['branch'], 'v3.24')
                self.assertNotEqual(first['inputs_sha256'], second['inputs_sha256'])
                cleanup.assert_not_called()
                reuse.assert_not_called()

    def test_download_or_signature_failure_does_not_run_build_or_cleanup(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for stage in ('metadata', 'packages'):
                with self.subTest(stage=stage), \
                        patch.object(cli, 'STATE', root), patch.object(cli, 'CACHE', root), \
                        patch.object(cli, 'require_controller'), patch.object(cli, 'command', return_value='a' * 40) as command, \
                        patch.object(cli, 'fetch_json', return_value=({}, 'b' * 64),
                                     side_effect=UpdateError('download failed') if stage == 'metadata' else None), \
                        patch.object(cli, 'newest_stable_branch', return_value='v3.24'), \
                        patch.object(cli.vm_template_inputs, 'freeze', side_effect=UpdateError('signature failed')), \
                        patch.object(cli, 'reusable_template') as reuse, \
                        patch.object(cli, 'cleanup_template') as cleanup:
                    with self.assertRaises(UpdateError): cli.prepare('boxa')
                    cleanup.assert_not_called()
                    reuse.assert_not_called()
                    self.assertFalse(any(c.args[0][0] == 'ansible-playbook' for c in command.call_args_list))

    def test_cli_rejects_branch_and_reports_cleanup_failure(self):
        cli = load_cli()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit), \
                patch.object(cli, 'prepare') as prepare:
            cli.main(['prepare', '--box', 'boxa', '--branch', 'v3.23'])
        prepare.assert_not_called()
        with patch.object(cli, 'prepare', return_value={'state': 'candidate-built', 'cleanup': {'status': 'deferred'}}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['prepare', '--box', 'boxa']), 1)
        self.assertEqual(json.loads(output.getvalue())['state'], 'candidate-built')


class TemplateReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cli = load_cli()
        self.old = 'a' * 24
        self.new = 'b' * 24
        self.directory = self.root / 'builds' / self.old; self.directory.mkdir(parents=True)
        self.result = self.root / 'builds' / self.new; self.result.mkdir()
        self.inputs = {'kind': 'klokast.vm-template-inputs.v1', 'engine_commit': 'a' * 40,
            'profile': 'shared-alpine-v1', 'architecture': 'x86_64', 'branch': 'v3.24',
            'indexes': {'main': 'c' * 64, 'community': 'd' * 64},
            'packages': [{'name': name, 'version': '1-r0', 'architecture': 'x86_64', 'origin': name,
                          'file': 'packages/' + name + '-1-r0.apk', 'bytes': 100, 'sha256': 'e' * 64}
                         for name in ('alpine-base', 'linux-virt', 'openssl', 'podman', 'tailscale')]}
        self.seal(self.inputs)
        component = {'operation_id': self.old, 'inputs_sha256': self.inputs['inputs_sha256'],
                     'success': True, 'kernel_release': 'test-kernel'}
        self.candidate = {'kind': 'klokast.vm-template-candidate.v1', 'box': 'boxa',
            'operation_id': self.old, 'inputs_sha256': self.inputs['inputs_sha256'],
            'success': True, 'accepted': False, 'validation': 'base-boot-tested',
            'kernel_release': 'test-kernel', 'modules_release': 'test-kernel',
            'tests': dict.fromkeys(BASE_BUILD_TESTS, True),
            'artifacts': {name: {'sha256': 'c' * 64, 'bytes': 100} for name in ('root', 'kernel', 'initramfs')},
            'boot_test': {**component, 'tests': dict.fromkeys(BASE_BOOT_TESTS, True),
                'openrc_test': {**component, 'tests': dict.fromkeys(OPENRC_TESTS, True)},
                'personalized_test': {**component, 'tests': dict.fromkeys(PERSONALIZED_TESTS, True)},
                'maintenance_restore': {**component}}}
        boot = self.candidate['boot_test']
        release = no_application_release(self.inputs, self.candidate, boot['openrc_test'],
                                          boot['personalized_test'], boot['maintenance_restore'])
        self.outcome = {'state': 'candidate-built', 'operation_id': self.old, 'accepted': False,
            'inputs_sha256': self.inputs['inputs_sha256'], 'artifacts': self.candidate['artifacts'],
            'release_evidence_sha256': release['release_sha256'],
            'cleanup': {'status': 'deferred', 'complete': False, 'error': 'protected records'}}
        records = {'selection.json': {'observed_at': '2026-10-07T10:00:00Z'},
            'inputs.json': self.inputs, 'candidate.json': self.candidate,
            'release-evidence.json': release, 'build-result.json': self.outcome,
            'request.json': {'kind': 'klokast.vm-template-build-request.v1', 'box': 'boxa',
                            'operation_id': self.old, 'inputs_sha256': self.inputs['inputs_sha256']},
            'lifecycle.json': {'stage': 'cleaned', 'domain': 'vm-build-' + self.old},
            'test-lifecycle.json': {'stage': 'cleaned', 'domain': 'vm-test-' + self.old,
                'openrc_domain': 'vm-openrc-' + self.old, 'personalize_domain': 'vm-personalize-' + self.old,
                'profile_domain': 'vm-profile-' + self.old, 'restore_domain': 'vm-restore-' + self.old}}
        for name, value in records.items(): self.cli.write(self.directory / name, value)
        self.verification = {'box': 'boxa', 'current': self.old, 'verified': True,
            'candidate_sha256': digest(self.candidate), 'artifacts': self.candidate['artifacts']}

    def seal(self, inputs):
        inputs.pop('inputs_sha256', None)
        inputs['inputs_sha256'] = digest(inputs)

    def verify_command(self, argv, **kwargs):
        if argv[0] == 'git': return 'b' * 40
        if argv[0] != 'ansible-playbook': return '{}'
        args = json.loads(Path(argv[-1][1:]).read_text())
        self.assertEqual(args['cleanup_action'], 'verify')
        self.assertEqual(args['cleanup_current'], self.old)
        self.cli.write(Path(args['cleanup_result_dir']) / 'cleanup-verify.json', self.verification)
        return '{}'

    def reuse(self, inputs=None):
        with patch.object(self.cli, 'STATE', self.root), \
                patch.object(self.cli, 'command', side_effect=self.verify_command) as command:
            return self.cli.reusable_template('boxa', inputs or self.inputs, self.result), command

    def test_code_indexes_profile_and_same_version_package_bytes_do_not_rebuild(self):
        fresh = copy.deepcopy(self.inputs)
        fresh.update(engine_commit='b' * 40, indexes={'new-main': 'f' * 64, 'new-community': 'e' * 64},
                     profile_sha256='f' * 64)
        fresh['packages'][0]['sha256'] = 'f' * 64
        fresh['packages'].reverse()
        self.seal(fresh)
        result, command = self.reuse(fresh)
        self.assertEqual(result['state'], 'candidate-reused')
        self.assertEqual(result['operation_id'], self.old)
        self.assertEqual(result['inputs_sha256'], self.inputs['inputs_sha256'])
        self.assertEqual(result['result_directory'], str(self.result))
        self.assertEqual(result['image_result_directory'], str(self.directory))
        self.assertEqual(result['cleanup']['status'], 'not-run')
        self.assertEqual(result['previous_cleanup']['status'], 'deferred')
        command.assert_called_once()

    def test_os_branch_os_package_dependency_and_package_set_changes_require_build(self):
        for change in ('branch', 'os-package', 'dependency', 'added', 'removed'):
            fresh = copy.deepcopy(self.inputs)
            if change == 'branch': fresh['branch'] = 'v3.25'
            elif change == 'os-package': fresh['packages'][0]['version'] = '2-r0'
            elif change == 'dependency': fresh['packages'][2]['version'] = '2-r0'
            elif change == 'added': fresh['packages'].append({**fresh['packages'][0], 'name': 'new-dependency'})
            else: fresh['packages'].pop(2)
            self.seal(fresh)
            with self.subTest(change=change):
                result, command = self.reuse(fresh)
                self.assertIsNone(result); command.assert_not_called()

    def test_wrong_scope_diagnostic_failed_release_and_incomplete_cleanup_cannot_reuse(self):
        for name, change in (
                ('request.json', {'box': 'boxb'}), ('request.json', {'app_test': {}}),
                ('inputs.json', {'profile': 'router-alpine-v2'}), ('inputs.json', {'architecture': 'aarch64'}),
                ('release-evidence.json', {'release_sha256': '0' * 64}),
                ('test-lifecycle.json', {'stage': 'allocated'}), ('candidate.json', {'success': False})):
            path = self.directory / name; original = self.cli.load(path)
            self.cli.write(path, {**original, **change})
            with self.subTest(name=name, change=change):
                result, command = self.reuse()
                self.assertIsNone(result); command.assert_not_called()
            self.cli.write(path, original)

    def test_missing_or_damaged_image_rebuilds_but_uncertain_verification_fails(self):
        for reason in ('missing', 'damaged'):
            self.verification = {'box': 'boxa', 'current': self.old, 'verified': False, 'reason': reason}
            with self.subTest(reason=reason): self.assertIsNone(self.reuse()[0])
        for change in ({'reason': 'unknown'}, {'verified': 'true'}, {'current': self.new}):
            self.verification.update(change)
            with self.subTest(change=change), self.assertRaises(UpdateError): self.reuse()
        with patch.object(self.cli, 'STATE', self.root), \
                patch.object(self.cli, 'command', side_effect=UpdateError('target unavailable')), \
                self.assertRaises(UpdateError):
            self.cli.reusable_template('boxa', self.inputs, self.result)

    def test_changed_dom0_metadata_cannot_claim_matching_image(self):
        self.verification['candidate_sha256'] = '0' * 64
        with self.assertRaises(UpdateError): self.reuse()

    def test_latest_qualified_build_uses_recorded_time_and_not_directory_name(self):
        older = self.root / 'builds' / ('f' * 24); older.mkdir()
        for path in self.directory.iterdir():
            if not path.is_file(): continue
            if path.name == 'inputs.json':
                self.cli.write(older / path.name, self.inputs)
                continue
            text = path.read_text().replace(self.old, older.name)
            (older / path.name).write_text(text)
        candidate = self.cli.load(older / 'candidate.json'); boot = candidate['boot_test']
        release = no_application_release(self.inputs, candidate, boot['openrc_test'],
                                          boot['personalized_test'], boot['maintenance_restore'])
        self.cli.write(older / 'release-evidence.json', release)
        self.cli.write(older / 'build-result.json', {**self.outcome, 'operation_id': older.name,
                                                   'release_evidence_sha256': release['release_sha256']})
        self.cli.write(older / 'selection.json', {'observed_at': '2026-10-06T10:00:00Z'})
        result, _command = self.reuse()
        self.assertEqual(result['operation_id'], self.old)
        self.cli.write(older / 'selection.json', {'observed_at': '2026-10-07T11:00:00Z'})
        self.old = older.name
        self.verification.update(current=self.old, candidate_sha256=digest(candidate))
        self.assertEqual(self.reuse()[0]['operation_id'], older.name)

    def test_prepare_reuses_before_capsule_bootstrap_or_guest_and_removes_only_new_cache(self):
        self.result.rmdir()  # prepare owns the new request directory.
        cache = self.root / 'cache'; cache.mkdir()
        unrelated = cache / 'unrelated'; unrelated.mkdir()
        with patch.object(self.cli, 'STATE', self.root), patch.object(self.cli, 'CACHE', cache), \
                patch.object(self.cli, 'require_controller'), \
                patch.object(self.cli.secrets, 'token_hex', return_value=self.new), \
                patch.object(self.cli, 'command', side_effect=self.verify_command) as command, \
                patch.object(self.cli, 'fetch_json', return_value=({}, 'e' * 64)) as fetch, \
                patch.object(self.cli, 'newest_stable_branch', return_value='v3.24'), \
                patch.object(self.cli.vm_template_inputs, 'freeze', return_value=self.inputs) as freeze, \
                patch.object(self.cli.vm_template_inputs, 'capsule') as capsule, \
                patch.object(self.cli.vm_template_inputs, 'bootstrap') as bootstrap, \
                patch.object(self.cli.vm_template_inputs, 'personalization_fixture') as fixture, \
                patch.object(self.cli, 'cleanup_template') as cleanup:
            result = self.cli.prepare('boxa')
        self.assertEqual(result['state'], 'candidate-reused')
        fetch.assert_called_once(); freeze.assert_called_once()
        capsule.assert_not_called(); bootstrap.assert_not_called(); fixture.assert_not_called(); cleanup.assert_not_called()
        self.assertEqual(sum(call.args[0][0] == 'ansible-playbook' for call in command.call_args_list), 1)
        self.assertFalse((cache / ('build-' + self.new)).exists())
        self.assertTrue(unrelated.exists()); self.assertTrue(self.directory.exists())
        self.assertEqual(self.cli.load(self.directory / 'build-result.json'), self.outcome)
        self.assertEqual(self.cli.load(self.result / 'build-result.json'), result)


class ControllerCleanupTests(unittest.TestCase):
    def test_plan_and_apply_bind_exact_build_and_checksum(self):
        cli = load_cli()
        operation = 'a' * 24
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = []
            def command(argv, **kwargs):
                args = json.loads(Path(argv[-1][1:]).read_text())
                calls.append(args)
                action = args['cleanup_action']
                if action == 'plan':
                    value = {'box': 'boxa', 'current': operation, 'removals': []}
                    value['plan_sha256'] = digest(value)
                else:
                    value = {'complete': True, 'current': operation,
                             'plan_sha256': args['cleanup_plan_sha256'], 'retired_candidates': []}
                (root / ('cleanup-' + action + '.json')).write_text(json.dumps(value))
            with patch.object(cli, 'command', side_effect=command):
                result = cli.cleanup_template('boxa', operation, root)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual([v['cleanup_action'] for v in calls], ['plan', 'apply'])
            self.assertTrue(all(v['cleanup_current'] == operation for v in calls))

    def test_changed_plan_prevents_apply(self):
        cli = load_cli()
        operation = 'a' * 24
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = {'box': 'boxa', 'current': 'b' * 24, 'removals': []}
            value['plan_sha256'] = digest(value)
            (root / 'cleanup-plan.json').write_text(json.dumps(value))
            with patch.object(cli, 'command') as command:
                result = cli.cleanup_template('boxa', operation, root)
            self.assertEqual(command.call_count, 1)
            self.assertEqual(result['status'], 'deferred')

    def test_refused_cleanup_preserves_build_and_returns_diagnostics(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(cli, 'command', side_effect=UpdateError('protected operation records')):
            result = cli.cleanup_template('boxa', 'a' * 24, Path(temporary))
            self.assertEqual(result['status'], 'deferred')
            self.assertIn('protected operation', result['error'])

    def test_cache_cleanup_retains_evidence_and_rejects_aliases(self):
        cli = load_cli()
        operation = 'a' * 24
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / 'builds' / operation; evidence.mkdir(parents=True)
            work = root / ('build-' + operation); work.mkdir()
            request = {'box': 'boxa', 'operation_id': operation}
            for directory in (work, evidence):
                (directory / 'request.json').write_text(json.dumps(request))
            with patch.object(cli, 'CACHE', root), patch.object(cli, 'STATE', root):
                cli.clean_template_cache('boxa', [operation])
                self.assertFalse(work.exists()); self.assertTrue(evidence.exists())
                work.symlink_to(evidence)
                with self.assertRaisesRegex(UpdateError, 'unsafe'):
                    cli.clean_template_cache('boxa', [operation])
                self.assertTrue((evidence / 'request.json').exists())


if __name__ == '__main__': unittest.main()
