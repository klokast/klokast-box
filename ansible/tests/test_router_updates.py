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
                   'generic_tests': {name: True for name in ('identity_absent', 'exact_packages', 'kernel_modules', 'openrc')}})


def branch(name, date='2026-01-01'):
    return {'rel_branch': name, 'git_branch': name[1:] + '-stable', 'branch_date': date,
            'eol_date': '2028-01-01', 'arches': ['x86_64'],
            'repos': [{'name': 'main', 'eol_date': '2028-01-01'},
                      {'name': 'community', 'eol_date': '2027-01-01'}],
            'releases': [{'version': name[1:] + '.0', 'date': date}]}


class RouterCheckTests(unittest.TestCase):
    def fixture(self):
        accepted = {'box': 'boxa', 'role': 'router', 'generation': 'f' * 64, 'release': release()}
        return dict(box='boxa', role='router', accepted=accepted,
                    live={'observed_at': timestamp(NOW), 'box': 'boxa', 'role': 'router',
                          'generation': accepted['generation'], 'packages': {p['name']: p['version'] for p in inputs()['packages']},
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

    def test_dependency_removal(self):
        f = self.fixture()
        accepted = f['accepted']['release']
        accepted['inputs']['packages'].append(package('zz-old-dependency'))
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
    def fixture(self):
        guest = {'kind': 'klokast.router-inspection.v1', 'box': 'boxa', 'target': 'router',
                 'tailscale_running': True, 'tailscale_ssh': True, 'machine_id': 'node-1',
                 'overlay_ipv6_enabled': False, 'unsupported_state': {'tka': False},
                 'dnsmasq_lease_paths': ['/var/lib/misc/dnsmasq.leases'],
                 'ssh_keys': {'ed25519': {'fingerprint': 'SHA256:synthetic',
                                         'metadata': {'regular': True, 'links': 1}}},
                 'first_contact_key': {'present': False},
                 'packages': {'tailscale': '1-r0'}, 'kernel_release': '6.12.1-virt',
                 'state_paths': {path: {'present': True, 'regular': True, 'links': 1}
                                 for path in ('/var/lib/tailscale/tailscaled.state',
                                              '/var/lib/dhcpcd/duid', '/var/lib/dhcpcd/secret',
                                              '/var/lib/misc/dnsmasq.leases')}}
        dom0 = {'kind': 'klokast.router-inspection.v1', 'box': 'boxa', 'target': 'dom0',
                'accepted_record_present': False, 'pending_record_present': False,
                'configuration_sha256': 'a' * 64,
                'xen': {'name': 'router', 'disk': ['phy:/dev/vg0/lv_router,xvda,w']},
                'logical_volumes': {'report': [{'lv': [{'lv_path': '/dev/vg0/lv_router', 'lv_uuid': 'synthetic-uuid'}]}]},
                'boot_artifacts': {name: {'path': '/mnt/dom0_data/' + name, 'sha256': 'a' * 64}
                                   for name in ('kernel', 'ramdisk')}}
        return guest, dom0

    def test_complete_inspection_only_reports_readiness(self):
        guest, dom0 = self.fixture()
        self.assertEqual(r.legacy_baseline_findings(guest, dom0, 'boxa'), [])

    def test_missing_lease_path_and_first_contact_key_block_adoption(self):
        guest, dom0 = self.fixture()
        guest['dnsmasq_lease_paths'] = []
        guest['first_contact_key']['present'] = True
        findings = r.legacy_baseline_findings(guest, dom0, 'boxa')
        self.assertEqual(len(findings), 2)
        self.assertTrue(any('lease file' in finding for finding in findings))
        self.assertTrue(any('root SSH' in finding for finding in findings))

    def test_unknown_state_and_existing_assignment_block_adoption(self):
        guest, dom0 = self.fixture()
        guest['state_paths']['/var/lib/dhcpcd/duid']['regular'] = False
        guest['unsupported_state']['tka'] = True
        dom0['pending_record_present'] = True
        self.assertEqual(len(r.legacy_baseline_findings(guest, dom0, 'boxa')), 3)

    def test_boot_disk_identity_must_be_complete(self):
        guest, dom0 = self.fixture()
        dom0['logical_volumes']['report'][0]['lv'][0]['lv_uuid'] = ''
        dom0['boot_artifacts']['kernel']['sha256'] = 'invalid'
        findings = r.legacy_baseline_findings(guest, dom0, 'boxa')
        self.assertEqual(len(findings), 2)

    def test_wrong_target_cannot_be_used_as_baseline(self):
        guest, dom0 = self.fixture()
        dom0['box'] = 'boxb'
        with self.assertRaises(UpdateError):
            r.legacy_baseline_findings(guest, dom0, 'boxa')


if __name__ == '__main__':
    unittest.main()
