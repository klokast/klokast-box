"""Daily update eligibility, manifest verification and Instance schedules."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import platform_update_metadata as metadata
import platform_updates as updates
import vm_template_inputs
from test_platform_updates import load_cli


class DailyUpdates(unittest.TestCase):
    def setUp(self):
        self.release = dt.datetime(2026, 6, 9, tzinfo=dt.timezone.utc)
        self.releases = {'release_branches': [{
            'rel_branch': 'v3.24', 'git_branch': '3.24-stable',
            'branch_date': '2026-06-01', 'eol_date': '2028-06-01',
            'arches': ['x86_64'], 'repos': [{'name': 'main'}, {'name': 'community', 'eol_date': '2026-11-01'}],
            'releases': [{'version': '3.24.0', 'date': '2026-06-09'},
                         {'version': '3.24.1', 'date': '2026-06-29'}]}]}

    def test_day_21_uses_first_stable_date_not_branch_or_latest_patch(self):
        at = self.release + dt.timedelta(days=21)
        self.assertIsNone(metadata.adjacent_stable_branch('v3.23', self.releases, at - dt.timedelta(seconds=1)))
        self.assertEqual(metadata.adjacent_stable_branch('v3.23', self.releases, at), 'v3.24')
        self.assertIsNone(metadata.adjacent_stable_branch('v3.22', self.releases, at))
        self.assertEqual(metadata.adjacent_stable_branch('v3.23', self.releases, self.release, 0), 'v3.24')

    def test_missing_invalid_or_duplicate_first_release_defers(self):
        for first in ([], [{'version': '3.24.0'}], [{'version': '3.24.0', 'date': 'invalid'}],
                      [{'version': '3.24.0', 'date': '2026-06-09'}] * 2):
            data = copy.deepcopy(self.releases)
            data['release_branches'][0]['releases'] = first + [{'version': '3.24.1', 'date': '2026-06-29'}]
            with self.subTest(first=first):
                self.assertIsNone(metadata.adjacent_stable_branch('v3.23', data, self.release + dt.timedelta(days=40)))

    def test_security_unavailable_or_malformed_cannot_block_packages(self):
        installed = {'alpine-baselayout': {'version': '1', 'origin': 'alpine-baselayout'}}
        indexes = {'main': {'alpine-baselayout': {'version': '2'}}, 'community': {}}
        compare = lambda a, b: '=' if a == b else '<' if a < b else '>'
        for security in ({}, {'main': None}, {'main': {'packages': [{'pkg': {'secfixes': []}}]}}):
            changes, missing, _ = updates.compare_packages(installed, indexes, security, compare)
            self.assertEqual(changes[0]['available'], '2')
            self.assertFalse(missing)

    def test_identity_ignores_index_but_binds_package_bytes_and_build_inputs(self):
        inputs = {'indexes': {'main': 'a'}, 'inputs_sha256': 'b', 'engine_commit': 'c',
                  'packages': [{'name': 'podman', 'version': '5', 'sha256': 'd'}], 'profile_sha256': 'e'}
        changed = copy.deepcopy(inputs)
        changed.update(indexes={'main': 'different'}, inputs_sha256='different')
        self.assertEqual(vm_template_inputs.build_identity(inputs), vm_template_inputs.build_identity(changed))
        for key, value in [('packages', [{'name': 'podman', 'version': '5', 'sha256': 'new'}]),
                           ('engine_commit', 'new'), ('profile_sha256', 'new')]:
            modified = dict(changed, **{key: value})
            self.assertNotEqual(vm_template_inputs.build_identity(inputs), vm_template_inputs.build_identity(modified))

    def test_daily_freshness_is_distinct_from_execution_freshness(self):
        stamp = updates.timestamp(self.release)
        report = {'kind': updates.REPORT_KIND, 'generated_at': stamp, 'complete': True}
        verify = {'generated_at': stamp}
        later = self.release + dt.timedelta(hours=30)
        self.assertFalse(updates.fresh(stamp, later, updates.VERIFY_AGE))
        self.assertEqual(updates.health(report, verify, later, report_max_age_hours=30), [])
        self.assertEqual(len(updates.health(report, verify, later + dt.timedelta(seconds=1))), 2)
        self.assertEqual(updates.health(report, verify, later, report_max_age_hours=40), [])

    def test_same_branch_partial_rollout_freezes_release(self):
        cli = load_cli()
        selection = {'targets': ['k001-dmz', 'k002-dmz', 'k002-iot']}
        old = {'managed': True, 'stage': 'complete', 'release_sha256': 'a' * 64}
        new = dict(old, release_sha256='b' * 64)
        with patch.object(cli, 'optional', return_value={'release_sha256': 'b' * 64}):
            for assignments, expected in (([old, old, old], False), ([new, old, old], True),
                                          ([new, new, new], False)):
                with patch.object(cli, 'dom0_assignment_status', side_effect=assignments):
                    self.assertEqual(cli.rollout_in_progress(selection), expected)
            with patch.object(cli, 'dom0_assignment_status', return_value=dict(new, stage='recovered')):
                with self.assertRaisesRegex(updates.UpdateError, 'reconcile'):
                    cli.rollout_in_progress(selection)

    def test_verifier_checks_packages_kernel_and_new_workload(self):
        cli = load_cli()
        assignment = {'stage': 'complete', 'runtime': 'running', 'vm_uuid': 'uuid'}
        release = {'packages': {'podman': '1'}, 'kernel_release': 'kernel'}
        probe = {'packages_sha256': updates.digest(release['packages']), 'kernel': 'kernel',
                 'modules_present': True, 'xen_uuid': 'uuid', 'hostname': 'k001-dmz',
                 'tailscale_online': True, 'rootless_podman': True, 'firewall_valid': True,
                 'retained_mounted': True, 'retained_state_private': True,
                 'containers': [], 'images': [], 'volumes': []}
        with patch.object(cli, 'require_controller'), patch.object(cli, 'write'), \
                patch.object(cli, 'accepted_release', return_value=release), \
                patch.object(cli, 'dom0_assignment_status', return_value=assignment):
            for field, value, code in [('packages_sha256', 'changed', 'packages'),
                                       ('kernel', 'old', 'kernel'), ('containers', ['new-app'], 'no_application')]:
                entries = [{'host': 'k001-dmz', 'assignment': assignment}]
                with patch.object(cli, 'assignment_summary', return_value=(entries, [])), \
                        patch.object(cli.vm_supervised_probe, 'probe', return_value=dict(probe, **{field: value})):
                    result = cli.verify_accepted()
                    self.assertFalse(result['verified'])
                    self.assertIn('release.' + code, [row['code'] for row in result['findings']])

    def test_daily_does_not_prepare_after_failed_verification(self):
        cli = load_cli()
        with patch.object(cli, 'schedule_source', return_value={'policy': {'enabled': True}, 'activated': True}), \
                patch.object(cli, 'scan'), patch.object(cli, 'verify_accepted', return_value={'verified': False}), \
                patch.object(cli, 'prepare_auto') as prepare:
            self.assertEqual(cli.daily()['status'], 'failed')
            prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
