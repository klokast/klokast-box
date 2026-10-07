"""Latest input selection and controller-side template retention orchestration."""
import contextlib
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
from platform_updates import UpdateError, digest

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
                    patch.object(cli, 'cleanup_template') as cleanup:
                first = cli.prepare('boxa', inputs_only=True)
                second = cli.prepare('boxa', inputs_only=True)
                self.assertEqual(fetch.call_count, 2)
                self.assertEqual(frozen.call_count, 2)
                self.assertEqual(first['branch'], 'v3.24')
                self.assertNotEqual(first['inputs_sha256'], second['inputs_sha256'])
                cleanup.assert_not_called()

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
                        patch.object(cli, 'cleanup_template') as cleanup:
                    with self.assertRaises(UpdateError): cli.prepare('boxa')
                    cleanup.assert_not_called()
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
