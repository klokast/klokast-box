"""First-router selection and rendering use validated fixed inputs."""
import copy
import datetime as dt
import unittest

from router_release_fixtures import r, PROFILE, ENGINE, NOW, branch, release, reseal
from platform_updates import UpdateError, digest, timestamp


class RouterReleaseTests(unittest.TestCase):
    def test_historical_release_cannot_be_used_for_a_new_installation(self):
        profile = {**PROFILE, 'state_contract': 'klokast.router-state.v1'}
        value = release()
        value['inputs']['profile_sha256'] = digest(profile)
        reseal(value['inputs'], 'inputs_sha256')
        reseal(value)
        with self.assertRaises(UpdateError):
            r.validate_release(value, profile, ENGINE)
        r.validate_release(value, profile, ENGINE, historical=True)

    def test_first_install_choice_binds_policy_inputs_and_fresh_metadata(self):
        schedule = {'kind': 'klokast.vm-update-schedule.v1', 'enabled': False,
                    'replacement_ready': False,
                    'policy': {'branch-policy': 'tested-stable', 'branch-delay-days': 21,
                               'report-max-age-hours': 72, 'enabled': False, 'targets': {}}}
        resolved = {'branch': 'v3.24', 'inputs_sha256': 'a' * 64}
        releases = {'release_branches': [branch('v3.23'), branch('v3.24')]}
        choice = r.seal({'kind': 'klokast.router-bootstrap-input-selection.v1',
            'engine_commit': ENGINE, 'observed_at': timestamp(NOW), 'branch': 'v3.24',
            'branch_delay_days': 21, 'schedule_sha256': digest(schedule),
            'metadata_sha256': 'b' * 64, 'inputs_sha256': 'a' * 64,
            'replacement_authorized': False})
        self.assertEqual(r.validate_initial_selection(choice, resolved, schedule, ENGINE, releases, 'b' * 64, NOW), choice)
        for field, value in (('branch', 'v3.23'), ('inputs_sha256', 'c' * 64),
                             ('replacement_authorized', True), ('branch_delay_days', 20)):
            changed = {key: item for key, item in choice.items() if key != 'receipt_sha256'}
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(UpdateError):
                r.validate_initial_selection(r.seal(changed), resolved, schedule, ENGINE, releases, 'b' * 64, NOW)
        with self.assertRaises(UpdateError):
            r.validate_initial_selection(choice, resolved, schedule, ENGINE, releases, 'c' * 64, NOW)
        with self.assertRaises(UpdateError):
            r.validate_initial_selection(choice, resolved,
                {**schedule, 'policy': {**schedule['policy'], 'branch-delay-days': 30}},
                ENGINE, releases, 'b' * 64, NOW)
        with self.assertRaisesRegex(UpdateError, 'stale'):
            r.validate_initial_selection(choice, resolved, schedule, ENGINE, releases, 'b' * 64,
                                         NOW + dt.timedelta(hours=73))
        with self.assertRaisesRegex(UpdateError, 'no longer eligible'):
            r.validate_initial_selection(choice, resolved, schedule, ENGINE,
                {'release_branches': releases['release_branches'] + [branch('v3.25')]},
                'b' * 64, NOW)

    def test_resolver_selects_only_first_install_inputs(self):
        policy = {'branch-policy': 'tested-stable', 'branch-delay-days': 21}
        releases = {'release_branches': [branch('v3.23'), branch('v3.24'),
                                         branch('v3.25'), branch('v3.26', '2026-09-20'),
                                         {'rel_branch': 'edge'}]}
        self.assertEqual(r.select_branch('initial-install', releases, NOW, policy), 'v3.25')
        with self.assertRaisesRegex(UpdateError, 'first installation only'):
            r.select_branch('replacement', releases, NOW, policy, current='v3.23')
        releases['release_branches'][2]['repos'][1]['eol_date'] = '2026-09-01'
        self.assertEqual(r.select_branch('initial-install', releases, NOW, policy), 'v3.24')
        policy['branch-delay-days'] = 0
        self.assertEqual(r.select_branch('initial-install', releases, NOW, policy), 'v3.26')

    def test_fresh_selection_needs_no_enabled_replacement_policy(self):
        policy = {'branch-policy': 'tested-stable', 'branch-delay-days': 21,
                  'enabled': False, 'targets': {}}
        releases = {'release_branches': [branch('v3.24', '2026-09-04')]}
        boundary = dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc)
        with self.assertRaises(UpdateError):
            r.select_branch('initial-install', releases, boundary - dt.timedelta(seconds=1), policy)
        self.assertEqual(r.select_branch('initial-install', releases, boundary, policy), 'v3.24')
        releases['release_branches'][0]['releases'].append({'version':'3.24.1', 'date':'2026-09-24'})
        self.assertEqual(r.select_branch('initial-install', releases, boundary, policy), 'v3.24')

    def test_fresh_selection_refuses_missing_or_ambiguous_policy_and_metadata(self):
        policy = {'branch-policy': 'tested-stable', 'branch-delay-days': 21}
        for releases in ({}, {'release_branches': []},
                         {'release_branches': [branch('v3.24'), branch('v3.24')]},
                         {'release_branches': [branch('v3.24', '2027-01-01')]}):
            with self.subTest(releases=releases), self.assertRaises(UpdateError):
                r.select_branch('initial-install', releases, NOW, policy)
        for invalid in ({}, {**policy, 'branch-delay-days': True}, {**policy, 'branch-policy': 'edge'}):
            with self.subTest(policy=invalid), self.assertRaises(UpdateError):
                r.select_branch('initial-install', {'release_branches':[branch('v3.24')]}, NOW, invalid)
        with self.assertRaises(UpdateError):
            r.select_branch('initial-install', {}, NOW, policy, current='v3.23')
        with self.assertRaises(UpdateError):
            r.select_branch('replacement', {}, NOW, policy)

    def test_include_inputs_come_from_complete_compiler_output(self):
        row = {'node': 'boxa', 'host_role': 'router', 'kind': 'router-forward',
               'filename': 'aabbcc.nft', 'content': '# rendered rule\n'}
        compiled = {'compiler': 'platform-resources', 'registry_sha256': 'a' * 64,
                    'box_configs': {'boxa': {}}, 'app_resource_effective_files': [row]}
        value = r.rendered_includes(compiled, 'boxa')
        self.assertEqual(value['/etc/klokast/app-resources/router-forward.d/aabbcc.nft'],
                         row['content'])
        self.assertEqual(len(value), 3)
        for mutation in (lambda c: c.pop('app_resource_effective_files'),
                         lambda c: c['box_configs'].clear(),
                         lambda c: c['app_resource_effective_files'].append(copy.deepcopy(row)),
                         lambda c: c['app_resource_effective_files'][0].update(filename='../escape.nft'),
                         lambda c: c['app_resource_effective_files'][0].update(kind='vm-input')):
            changed = copy.deepcopy(compiled)
            mutation(changed)
            with self.assertRaises(UpdateError):
                r.rendered_includes(changed, 'boxa')
