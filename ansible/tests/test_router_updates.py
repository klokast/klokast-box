"""Router checks must not confuse availability, drift, or missing authority."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import router_updates as r
import test_router_generations as generation_fixture
from platform_updates import UpdateError, digest, timestamp

NOW = dt.datetime(2026, 9, 25, 12, tzinfo=dt.timezone.utc)
ENGINE = 'a' * 40
PROFILE = json.loads((REPO / 'ansible/update-profiles/router-alpine-v1.json').read_text())


def package(name, version='1-r0'):
    return {'name': name, 'version': version, 'origin': name, 'architecture': 'x86_64',
            'file': 'packages/' + name + '-' + version + '.apk', 'bytes': 100,
            'sha256': hashlib.sha256((name + version).encode()).hexdigest()}


def inputs(branch='v3.23'):
    return r.seal({'kind': 'klokast.vm-template-inputs.v1', 'profile': r.PROFILE,
                   'profile_sha256': digest(PROFILE), 'engine_commit': ENGINE,
                   'architecture': 'x86_64', 'branch': branch, 'world': sorted(PROFILE['packages']),
                   'repositories': [PROFILE['repository_origin'] + '/' + branch + '/' + name
                                    for name in PROFILE['repositories']],
                   'keys': {'alpine.pub': 'b' * 64},
                   'indexes': {'APKINDEX.111.tar.gz': 'c' * 64, 'APKINDEX.222.tar.gz': 'd' * 64},
                   'packages': sorted([package(name) for name in PROFILE['packages']], key=lambda p: p['name'])},
                  'inputs_sha256')


def reseal(value, field='receipt_sha256'):
    value.pop(field)
    value.update(r.seal(value, field))


def release():
    return r.seal({'kind': r.RELEASE, 'profile': r.PROFILE, 'engine_commit': ENGINE,
                   'inputs': inputs(), 'kernel_release': '6.12.1-virt',
                   'artifacts': {name: name[0] * 64 if name[0] in 'abcdef' else 'e' * 64
                                 for name in ('os', 'kernel', 'initramfs')},
                   'generic_tests': {name: True for name in ('identity_absent', 'exact_packages', 'kernel_modules', 'openrc')},
                   'runtime_packages': {p['name']: p['version'] for p in inputs()['packages'] if p['name'] != 'openssh'},
                   'runtime_tests': dict.fromkeys(('frozen_packages', 'no_openssh_server', 'locked_root', 'pinned_world'), True)})


def branch(name, date='2026-01-01'):
    return {'rel_branch': name, 'git_branch': name[1:] + '-stable', 'branch_date': date,
            'eol_date': '2028-01-01', 'arches': ['x86_64'],
            'repos': [{'name': 'main', 'eol_date': '2028-01-01'},
                      {'name': 'community', 'eol_date': '2027-01-01'}],
            'releases': [{'version': name[1:] + '.0', 'date': date}]}


class RouterCheckTests(unittest.TestCase):
    def test_common_resolver_fresh_install_and_adjacent_replacement(self):
        policy = {'branch-policy': 'tested-stable', 'branch-delay-days': 21}
        releases = {'release_branches': [branch('v3.23'), branch('v3.24'),
                                         branch('v3.25'), branch('v3.26', '2026-09-20'),
                                         {'rel_branch': 'edge'}]}
        self.assertEqual(r.select_branch('initial-install', releases, NOW, policy), 'v3.25')
        self.assertEqual(r.select_branch('replacement', releases, NOW, policy, current='v3.23'), 'v3.24')
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

    def fixture(self):
        accepted = {'box': 'boxa', 'role': 'router', 'generation': 'f' * 64, 'release': release()}
        return dict(box='boxa', role='router', accepted=accepted,
                    live={'observed_at': timestamp(NOW), 'box': 'boxa', 'role': 'router',
                          'generation': accepted['generation'], 'packages': dict(accepted['release']['runtime_packages']),
                          'kernel_release': '6.12.1-virt', 'configuration_verified': True,
                          'overlay_ipv6_enabled': False,
                          'boot_artifacts': {k: accepted['release']['artifacts'][k] for k in ('kernel', 'initramfs')}},
                    metadata={'observed_at': timestamp(NOW), 'sha256': 'e' * 64,
                              'releases': {'release_branches': [branch('v3.23')]}},
                    candidates={'v3.23': {'status': 'verified', 'observed_at': timestamp(NOW), 'inputs': inputs()}},
                    policy={'enabled': True, 'targets': {'boxa': ['router']}, 'exclusions': [],
                            'branch-policy': 'tested-stable', 'branch-delay-days': 21, 'report-max-age-hours': 30},
                    policy_sha256='d' * 64, profile=PROFILE, engine=ENGINE, now=NOW,
                    compare=lambda a, b: '=' if a == b else '<' if a < b else '>')

    def test_unactivated_instance_schedule_can_only_defer_router_check(self):
        schedule = {'kind':'klokast.vm-update-schedule.v1', 'activated':False,
                    'replacement_ready':False, 'policy':{
                        'enabled':True, 'targets':{'boxa':['dmz']}, 'exclusions':[],
                        'branch-policy':'tested-stable', 'branch-delay-days':21,
                        'report-max-age-hours':30}}
        policy, checksum = r.unactivated_diagnostic_policy(schedule, 'boxa')
        self.assertFalse(policy['enabled'])
        self.assertEqual(policy['targets']['boxa'], ['dmz', 'router'])
        f = self.fixture()
        f['policy'], f['policy_sha256'] = policy, checksum
        self.assertEqual(r.check(**f)['status'], 'deferred')
        self.assertTrue(schedule['policy']['enabled'])
        self.assertEqual(schedule['policy']['targets']['boxa'], ['dmz'])
        with self.assertRaises(UpdateError):
            r.unactivated_diagnostic_policy({**schedule, 'activated':True}, 'boxa')
        with self.assertRaises(UpdateError):
            r.unactivated_diagnostic_policy({**schedule, 'replacement_ready':True}, 'boxa')
        source = generation_fixture.generation('legacy')
        f['accepted'] = {'box':'boxa', 'role':'router', 'generation':source['record_sha256'], 'legacy':source}
        f['live'].update(generation=source['record_sha256'], packages=source['packages'],
            kernel_release=source['kernel_release'], alpine_branch=source['alpine_branch'],
            boot_artifacts={name:item['sha256'] for name,item in source['boot'].items()})
        self.assertEqual(r.check(**f)['status'], 'deferred')
        malformed = copy.deepcopy(schedule)
        malformed['policy']['targets']['boxa'] = {'dmz': True}
        with self.assertRaises(UpdateError):
            r.unactivated_diagnostic_policy(malformed, 'boxa')

    def test_unactivated_schedule_with_router_target_still_cannot_authorize_replacement(self):
        schedule = {'kind':'klokast.vm-update-schedule.v1', 'activated':False,
                    'replacement_ready':False, 'policy':{
                        'enabled':True, 'targets':{'boxa':['router']}, 'exclusions':[],
                        'branch-policy':'tested-stable', 'branch-delay-days':21,
                        'report-max-age-hours':30}}
        policy, checksum = r.unactivated_diagnostic_policy(schedule, 'boxa')
        self.assertEqual(policy['targets']['boxa'], ['router'])
        self.assertFalse(policy['enabled'])
        f = self.fixture()
        f['policy'], f['policy_sha256'] = policy, checksum
        self.assertEqual(r.check(**f)['status'], 'deferred')

    def test_unchanged_and_unrelated_index_and_patch(self):
        for change in ('none', 'index', 'patch'):
            with self.subTest(change=change):
                f = self.fixture()
                if change == 'index':
                    candidate = f['candidates']['v3.23']['inputs']
                    candidate['indexes']['APKINDEX.111.tar.gz'] = 'f' * 64
                    reseal(candidate, 'inputs_sha256')
                if change == 'patch':
                    f['metadata']['releases']['release_branches'][0]['releases'].append(
                        {'version': '3.23.9', 'date': '2026-09-20'})
                self.assertEqual(r.check(**f)['status'], 'unchanged')

    def test_adopted_legacy_source_requires_first_template_even_when_versions_match(self):
        f = self.fixture()
        source = generation_fixture.generation('legacy')
        f['accepted'] = {'box':'boxa', 'role':'router', 'generation':source['record_sha256'], 'legacy':source}
        f['live'].update(generation=source['record_sha256'], packages=source['packages'],
            kernel_release=source['kernel_release'], alpine_branch=source['alpine_branch'],
            boot_artifacts={name:item['sha256'] for name,item in source['boot'].items()})
        result = r.check(**f)
        self.assertEqual(result['status'], 'update-required', result)
        self.assertIn('first approved template', result['reason'])
        self.assertEqual(result['package_difference_scope'], 'legacy-runtime-to-build-inputs')
        self.assertIsNone(result['explicit_request_difference'])
        self.assertEqual(result['release_transition'], {'from': 'v3.23', 'to': 'v3.23'})
        self.assertEqual(result['accepted_sha256'], digest(f['accepted']))
        f['live']['alpine_branch'] = 'v3.22'
        self.assertEqual(r.check(**f)['status'], 'failed')
        f['live']['alpine_branch'] = source['alpine_branch']
        f['policy']['enabled'] = False
        self.assertEqual(r.check(**f)['status'], 'deferred')

    def test_service_dependency_and_kernel_changes(self):
        for name in ('tailscale', 'linux-virt', 'new-dependency'):
            with self.subTest(name=name):
                f = self.fixture()
                selected = f['candidates']['v3.23']['inputs']
                selected['packages'] = sorted([p for p in selected['packages'] if p['name'] != name] +
                                               [package(name, '2-r1')], key=lambda p: p['name'])
                reseal(selected, 'inputs_sha256')
                result = r.check(**f)
                self.assertEqual(result['status'], 'update-required', result)
                self.assertEqual(result['package_difference'][0]['name'], name)
                self.assertEqual(result['package_difference_scope'], 'build-inputs-to-build-inputs')
                self.assertEqual(result['explicit_request_difference'], {'added': [], 'removed': []})

    def test_dependency_removal(self):
        f = self.fixture()
        accepted = f['accepted']['release']
        accepted['inputs']['packages'].append(package('zz-old-dependency'))
        accepted['runtime_packages']['zz-old-dependency'] = '1-r0'
        reseal(accepted['inputs'], 'inputs_sha256')
        reseal(accepted)
        f['live']['packages']['zz-old-dependency'] = '1-r0'
        result = r.check(**f)
        self.assertEqual(result['status'], 'update-required', result)
        self.assertEqual(result['package_difference'][0]['change'], 'removed')

    def test_held_branch_does_not_hold_current_package_update(self):
        f = self.fixture()
        f['metadata']['releases']['release_branches'].append(branch('v3.24', '2026-09-20'))
        self.assertEqual(r.check(**f)['status'], 'deferred')
        selected = f['candidates']['v3.23']['inputs']
        selected['packages'].append(package('zz-dependency'))
        reseal(selected, 'inputs_sha256')
        result = r.check(**f)
        self.assertEqual(result['status'], 'update-required', result)
        self.assertFalse(result['availability'][1]['eligible'])
        self.assertEqual(result['selected_branch'], 'v3.23')

    def test_adjacent_branch_only_and_separate_support(self):
        f = self.fixture()
        f['metadata']['releases']['release_branches'] += [branch('v3.24'), branch('v3.25')]
        f['candidates']['v3.24'] = {'status': 'verified', 'observed_at': timestamp(NOW), 'inputs': inputs('v3.24')}
        result = r.check(**f)
        self.assertEqual(result['status'], 'update-required', result)
        self.assertEqual(result['selected_branch'], 'v3.24')
        self.assertFalse(result['availability'][2]['eligible'])
        f['metadata']['releases']['release_branches'][1]['repos'][1]['eol_date'] = '2026-09-01'
        result = r.check(**f)
        self.assertEqual(result['selected_branch'], 'v3.23')
        self.assertTrue(result['availability'][1]['support']['main']['supported'])
        self.assertFalse(result['availability'][1]['support']['community']['supported'])

    def test_missing_stale_invalid_and_drift_evidence(self):
        cases = [
            ('metadata', lambda f: f.update(metadata=None), 'deferred'),
            ('closure', lambda f: f.update(candidates={}), 'deferred'),
            ('signature', lambda f: f['candidates']['v3.23'].update(status='invalid-signature'), 'failed'),
            ('solver', lambda f: f['candidates']['v3.23'].update(status='unsatisfied'), 'failed'),
            ('stale', lambda f: f['live'].update(observed_at='2026-09-24T00:00:00Z'), 'failed'),
            ('role', lambda f: f.update(role='dmz'), 'failed'),
            ('box', lambda f: f['accepted'].update(box='boxb'), 'failed'),
            ('drift', lambda f: f['live']['packages'].update(tailscale='2-r0'), 'failed'),
            ('boot', lambda f: f['live']['boot_artifacts'].update(kernel='f' * 64), 'failed'),
            ('overlay', lambda f: f['live'].update(overlay_ipv6_enabled=True), 'failed'),
            ('disabled', lambda f: f['policy'].update(enabled=False), 'deferred'),
            ('baseline', lambda f: f.update(accepted=None), 'deferred')]
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                f = self.fixture()
                mutate(f)
                self.assertEqual(r.check(**f)['status'], expected)

    def test_downgrade_and_same_version_different_bytes(self):
        for version in ('0-r0', '1-r0'):
            f = self.fixture()
            p = f['candidates']['v3.23']['inputs']['packages'][0]
            p.update(package(p['name'], version))
            p['sha256'] = 'a' * 64
            reseal(f['candidates']['v3.23']['inputs'], 'inputs_sha256')
            self.assertEqual(r.check(**f)['status'], 'failed')

    def test_changed_policy_and_stale_decision_cannot_allocate(self):
        f = self.fixture()
        selected = f['candidates']['v3.23']['inputs']
        selected['packages'].append(package('zz-dependency'))
        reseal(selected, 'inputs_sha256')
        report = r.check(**f)
        args = dict(box='boxa', policy_sha256=f['policy_sha256'], accepted_sha256=digest(f['accepted']),
                    inputs=selected, now=NOW, max_age_hours=30)
        r.require_preparation(report, **args)
        for key, value in [('box', 'boxb'), ('policy_sha256', 'f' * 64),
                           ('accepted_sha256', 'f' * 64), ('now', NOW + dt.timedelta(days=2))]:
            with self.subTest(key=key), self.assertRaises(UpdateError):
                r.require_preparation(report, **{**args, key: value})


class LifecycleTests(unittest.TestCase):
    def fixture(self):
        return dict(box='boxa', role='router', existing_disk=None, installation=None, accepted=None,
                    bootstrap_authorized=True, replacement_authorized=False)

    def test_unknown_existing_disk_never_formats(self):
        f = self.fixture()
        self.assertEqual(r.lifecycle('initial-install', **f), 'allocate')
        f['existing_disk'] = {'path': '/dev/vg0/lv_router', 'uuid': 'example'}
        with self.assertRaises(UpdateError):
            r.lifecycle('initial-install', **f)
        f['installation'] = {'box': 'boxa', 'role': 'router', 'disk': f['existing_disk'],
                             'stage': 'enrolled', 'machine_id': 'n123'}
        self.assertEqual(r.lifecycle('initial-install', **f), 'resume')
        f['installation']['disk'] = {'path': '/dev/vg0/lv_router', 'uuid': 'other'}
        with self.assertRaises(UpdateError):
            r.lifecycle('initial-install', **f)

    def test_accepted_assignment_cannot_be_initial_install(self):
        f = self.fixture()
        f['accepted'] = {'box': 'boxa', 'role': 'router', 'disk': 'disk-1'}
        with self.assertRaises(UpdateError):
            r.lifecycle('initial-install', **f)
        f.update(existing_disk='disk-1', bootstrap_authorized=False, replacement_authorized=True)
        self.assertEqual(r.lifecycle('replacement', **f), 'prepare')
        for role in ('bak', 'dmz', 'iot', 'ops', 'dom0'):
            with self.subTest(role=role), self.assertRaises(UpdateError):
                r.lifecycle('replacement', **{**f, 'role': role})

    def test_profiles_have_distinct_dispatch(self):
        self.assertEqual(r.dispatch('router'), 'router-alpine-v1')
        self.assertEqual(r.dispatch('dmz'), 'shared-alpine-v1')
        with self.assertRaises(UpdateError):
            r.dispatch('ops')


class LegacyBaselineTests(unittest.TestCase):
    def test_include_inputs_come_from_complete_compiler_output(self):
        row = {'node': 'boxa', 'host_role': 'router', 'kind': 'router-forward',
               'filename': 'aabbcc.nft', 'content': '# rendered rule\n'}
        compiled = {'compiler': 'platform-resources', 'registry_sha256': 'a' * 64,
                    'box_configs': {'boxa': {}}, 'app_resource_effective_files': [row]}
        value = r.expected_includes(compiled, 'boxa')
        self.assertEqual(value['files']['/etc/klokast/app-resources/router-forward.d/aabbcc.nft'],
                         hashlib.sha256(row['content'].encode()).hexdigest())
        self.assertEqual(len(value['files']), 3)
        for mutation in (lambda c: c.pop('app_resource_effective_files'),
                         lambda c: c['box_configs'].clear(),
                         lambda c: c['app_resource_effective_files'].append(copy.deepcopy(row)),
                         lambda c: c['app_resource_effective_files'][0].update(filename='../escape.nft'),
                         lambda c: c['app_resource_effective_files'][0].update(kind='vm-input')):
            changed = copy.deepcopy(compiled)
            mutation(changed)
            with self.assertRaises(UpdateError):
                r.expected_includes(changed, 'boxa')

    def fixture(self):
        def state(size, mode, uid=0, gid=0):
            return {'present': True, 'regular': True, 'links': 1, 'bytes': size,
                    'mode': oct(mode), 'uid': uid, 'gid': gid}

        guest = {'kind': 'klokast.router-inspection.v1', 'box': 'boxa', 'target': 'router',
                 'tailscale_running': True, 'tailscale_ssh': True, 'machine_id': 'node-1',
                 'tags': ['tag:vm'],
                 'service_accounts': {'dnsmasq_uid': 103, 'dnsmasq_gid': 104, 'tailscale_gid': 103},
                 'overlay_ipv6_enabled': False,
                 'unsupported_state': {'/var/lib/tailscale/tka': False,
                                       '/var/lib/tailscale/tpm-sealed': False},
                 'dnsmasq_lease_paths': ['/var/lib/misc/dnsmasq.leases'],
                 'ssh_keys': {kind: {'path': '/etc/ssh/ssh_host_' + kind + '_key',
                                    'fingerprint': 'SHA256:' + 'A' * 43,
                                    'metadata': state(411, 0o600)}
                              for kind in ('rsa', 'ecdsa', 'ed25519')},
                 'first_contact_key': {'present': False},
                 'root_password_locked': True, 'sshd_running': False,
                 'openssh_paths': {path: {'present': False} for path in
                                   ('/usr/sbin/sshd', '/etc/init.d/sshd', '/etc/runlevels/default/sshd')},
                 'expected_configuration': {path: 'a' * 64 for path in
                     ('/etc/network/interfaces', '/etc/dhcpcd.conf', '/etc/dnsmasq.conf', '/etc/nftables.nft')},
                 'configuration_files': {path: {'sha256': 'a' * 64, 'metadata': state(300, 0o644)} for path in
                     ('/etc/network/interfaces', '/etc/dhcpcd.conf', '/etc/dnsmasq.conf', '/etc/nftables.nft')},
                 'expected_includes': {'registry_sha256': 'a' * 64, 'files': {'/etc/example.nft': 'b' * 64}},
                 'include_files': {'/etc/example.nft': {'sha256': 'b' * 64, 'metadata': state(100, 0o644)}},
                 'packages': {'tailscale': '1-r0'}, 'kernel_release': '6.12.1-virt',
                 'alpine_branch': 'v3.23',
                 'state_paths': {'/var/lib/tailscale/tailscaled.state': state(2410, 0o600, gid=103),
                                 '/var/lib/dhcpcd/duid': state(42, 0o640),
                                 '/var/lib/dhcpcd/secret': state(192, 0o400),
                                 '/var/lib/misc/dnsmasq.leases': state(0, 0o644, 103, 104),
                                 '/var/lib/dhcpcd/eth0.lease': state(548, 0o640),
                                 '/var/lib/dhcpcd/eth0.lease6': {'present': False}}}
        dom0 = {'kind': 'klokast.router-inspection.v1', 'box': 'boxa', 'target': 'dom0',
                'accepted_record_present': False, 'pending_record_present': False,
                'configuration_sha256': 'a' * 64,
                'expected_configuration_sha256': 'a' * 64,
                'xen_runtime_matches': True,
                'xen_runtime': {'uuid': '12345678-1234-1234-1234-123456789abc'},
                'xen': {'name': 'router', 'disk': ['phy:/dev/vg0/lv_router,xvda,w'],
                        'kernel': '/mnt/dom0_data/kernel', 'ramdisk': '/mnt/dom0_data/ramdisk'},
                'logical_volumes': {'report': [{'lv': [{'lv_path': '/dev/vg0/lv_router', 'lv_uuid': 'synthetic-uuid'}]}]},
                'boot_artifacts': {name: {'path': '/mnt/dom0_data/' + name, 'sha256': 'a' * 64,
                                          'bytes': 1234}
                                   for name in ('kernel', 'ramdisk')}}
        return guest, dom0

    def test_complete_inspection_only_reports_readiness(self):
        guest, dom0 = self.fixture()
        self.assertEqual(r.legacy_baseline_findings(guest, dom0, 'boxa'), [])

    def test_missing_stable_branch_blocks_legacy_baseline(self):
        guest, dom0 = self.fixture()
        guest['alpine_branch'] = 'edge'
        self.assertIn('router Alpine stable branch evidence is missing',
                      r.legacy_baseline_findings(guest, dom0, 'boxa'))

    def test_missing_changed_and_extra_generated_rules_block_adoption(self):
        for mutate in (lambda g: g.pop('expected_includes'),
                       lambda g: g['include_files'].clear(),
                       lambda g: g['include_files']['/etc/example.nft'].update(sha256='c' * 64),
                       lambda g: g['include_files'].update({'/etc/dnsmasq.d/extra.conf': {}}),
                       lambda g: g['include_files']['/etc/example.nft']['metadata'].update(regular=False)):
            guest, dom0 = self.fixture()
            mutate(guest)
            self.assertEqual(r.legacy_baseline_findings(guest, dom0, 'boxa'),
                             ['router generated firewall or DNS includes differ from the current resource compiler'])

    def test_missing_lease_path_and_first_contact_key_block_adoption(self):
        guest, dom0 = self.fixture()
        guest['dnsmasq_lease_paths'] = []
        guest['first_contact_key']['present'] = True
        findings = r.legacy_baseline_findings(guest, dom0, 'boxa')
        self.assertEqual(len(findings), 2)
        self.assertTrue(any('lease file' in finding for finding in findings))
        self.assertTrue(any('root SSH' in finding for finding in findings))

    def test_retiring_a_key_alone_cannot_qualify_bootstrap_access(self):
        for mutate in (
                lambda g: g.update(sshd_running=True),
                lambda g: g.pop('sshd_running'),
                lambda g: g['openssh_paths']['/usr/sbin/sshd'].update(present=True),
                lambda g: g['openssh_paths'].pop('/etc/init.d/sshd'),
                lambda g: g['packages'].update({'openssh-server': '10.2_p1-r0'})):
            guest, dom0 = self.fixture()
            mutate(guest)
            self.assertEqual(r.legacy_baseline_findings(guest, dom0, 'boxa'),
                             ['router OpenSSH server retirement is incomplete or unknown'])

    def test_absent_or_unlocked_root_evidence_blocks_adoption(self):
        for value in (None, False, 'true', 1):
            guest, dom0 = self.fixture()
            guest['root_password_locked'] = value
            self.assertEqual(r.legacy_baseline_findings(guest, dom0, 'boxa'),
                             ['router root password is not proved locked'])

    def test_missing_changed_or_unsafe_configuration_blocks_adoption(self):
        for mutate in (
                lambda g: g.pop('expected_configuration'),
                lambda g: g['expected_configuration'].pop('/etc/dhcpcd.conf'),
                lambda g: g['configuration_files']['/etc/nftables.nft'].update(sha256='b' * 64),
                lambda g: g['configuration_files']['/etc/dnsmasq.conf']['metadata'].update(regular=False),
                lambda g: g['configuration_files']['/etc/network/interfaces']['metadata'].update(mode='0o666')):
            guest, dom0 = self.fixture()
            mutate(guest)
            self.assertEqual(r.legacy_baseline_findings(guest, dom0, 'boxa'),
                             ['router core configuration differs from the current compiled inventory and templates'])

    def test_unknown_state_and_existing_assignment_block_adoption(self):
        guest, dom0 = self.fixture()
        guest['state_paths']['/var/lib/dhcpcd/duid']['regular'] = False
        guest['unsupported_state']['/var/lib/tailscale/tka'] = True
        dom0['pending_record_present'] = True
        self.assertEqual(len(r.legacy_baseline_findings(guest, dom0, 'boxa')), 3)

    def test_boot_disk_identity_must_be_complete(self):
        guest, dom0 = self.fixture()
        dom0['logical_volumes']['report'][0]['lv'][0]['lv_uuid'] = ''
        dom0['boot_artifacts']['kernel']['sha256'] = 'invalid'
        findings = r.legacy_baseline_findings(guest, dom0, 'boxa')
        self.assertEqual(len(findings), 2)

    def test_live_domain_identity_and_assignment_are_required(self):
        for mutate in (lambda d: d.pop('xen_runtime'),
                       lambda d: d.update(xen_runtime_matches=False),
                       lambda d: d['xen_runtime'].update(uuid='invalid')):
            guest, dom0 = self.fixture()
            mutate(dom0)
            self.assertEqual(r.legacy_baseline_findings(guest, dom0, 'boxa'),
                             ['live router Xen identity or attachments differ from the recorded configuration'])

    def test_expected_xen_configuration_cannot_be_inferred_from_live_file(self):
        guest, dom0 = self.fixture()
        dom0['expected_configuration_sha256'] = 'b' * 64
        self.assertEqual(r.legacy_baseline_findings(guest, dom0, 'boxa'),
                         ['router Xen configuration differs from the compiled inventory and template'])

    def test_inspection_rejects_state_that_copy_guest_cannot_read(self):
        for path, field, value in (
                ('/var/lib/tailscale/tailscaled.state', 'mode', '0o644'),
                ('/var/lib/dhcpcd/duid', 'uid', 100),
                ('/var/lib/dhcpcd/duid', 'gid', 100),
                ('/var/lib/misc/dnsmasq.leases', 'uid', 999),
                ('/var/lib/dhcpcd/secret', 'bytes', 0),
                ('/var/lib/misc/dnsmasq.leases', 'bytes', 5 * 1024 * 1024),
                ('/var/lib/dhcpcd/eth0.lease', 'regular', False)):
            with self.subTest(path=path, field=field):
                guest, dom0 = self.fixture()
                guest['state_paths'].setdefault(path, {'present': True, 'regular': True,
                    'links': 1, 'bytes': 100, 'mode': '0o640', 'uid': 0, 'gid': 0})[field] = value
                self.assertTrue(any('identity or lease' in finding for finding in
                                    r.legacy_baseline_findings(guest, dom0, 'boxa')))

    def test_effective_key_path_and_production_tag_are_required(self):
        guest, dom0 = self.fixture()
        guest['ssh_keys']['ed25519']['path'] = '/tmp/ssh_host_ed25519_key'
        guest['tags'] = ['tag:bootstrap']
        findings = r.legacy_baseline_findings(guest, dom0, 'boxa')
        self.assertEqual(len(findings), 2)

    def test_partial_inspection_cannot_claim_state_is_absent(self):
        guest, dom0 = self.fixture()
        del guest['state_paths']['/var/lib/dhcpcd/eth0.lease6']
        del guest['unsupported_state']['/var/lib/tailscale/tka']
        findings = r.legacy_baseline_findings(guest, dom0, 'boxa')
        self.assertEqual(len(findings), 2)

    def test_boot_hash_must_describe_selected_xen_path(self):
        guest, dom0 = self.fixture()
        dom0['boot_artifacts']['kernel']['path'] = '/mnt/dom0_data/another-kernel'
        self.assertTrue(any('kernel or initramfs' in finding for finding in
                            r.legacy_baseline_findings(guest, dom0, 'boxa')))

    def test_wrong_target_cannot_be_used_as_baseline(self):
        guest, dom0 = self.fixture()
        dom0['box'] = 'boxb'
        with self.assertRaises(UpdateError):
            r.legacy_baseline_findings(guest, dom0, 'boxa')


if __name__ == '__main__':
    unittest.main()
