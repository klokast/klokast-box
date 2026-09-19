"""Qualification must preserve unknown data and refuse contradictory sources."""
import copy
import datetime as dt
import hashlib
import io
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_platform_updates import NOW, load_cli
from test_vm_retention import source
import test_vm_host_inventory as host_fixture
from test_vm_storage_inventory import CATALOG
from platform_updates import REPORT_KIND, UpdateError, digest, timestamp
import vm_no_application as noapp
import vm_config_audit as config_audit


def registry(retention):
    view = {'schema_version': 1, 'boxes': {b: {} for b in retention['projection']['boxes']},
            'apps': {'static-site': {'enabled': False}}}
    return {'schema_version': 1, 'kind': 'klokast.registry-source-status.v1',
            **{k: retention[k] for k in ('source', 'authority_state_sha256', 'engine_commit')},
            'rendered': {'kind': 'klokast.registry.v1', 'schema_version': 1, 'valid': True, 'diagnostics': [],
                         'engine': {'commit': retention['engine_commit']}, 'inputs': retention['inputs'],
                         'repository': {'head_commit': retention['private_commit'], 'clean': True, 'branch': 'main'},
                         'projection': {'registry': view, 'registry_sha256': digest(view), 'scopes': []}}}


def rehash(value):
    value['rendered']['projection']['registry_sha256'] = digest(value['rendered']['projection']['registry'])


class Sources(unittest.TestCase):
    def test_checked_backend_retention_does_not_block_dmz_and_never_grants_execution(self):
        retained = source()
        result = noapp.source_intent(retained, registry(retained), [CATALOG], 'boxa', 'dmz')
        self.assertTrue(result['eligible'])
        self.assertEqual(result['datasets'], [])
        self.assertNotIn('adoption_authorized', result)

    def test_stopped_guest_new_workload_and_unknown_dataset_block(self):
        for mode in ('stopped', 'workload', 'dataset'):
            retained = source(); reg = registry(retained)
            view = reg['rendered']['projection']['registry']
            if mode == 'stopped':
                view['boxes']['boxa']['shared_guests'] = {'iot': {'runtime_state': 'stopped'}}
            elif mode == 'workload':
                view['apps']['new-app'] = {'enabled': True}
            else:
                retained['projection']['datasets'][0]['dataset'] = 'unknown'
                retained['projection_sha256'] = digest(retained['projection'])
            rehash(reg)
            with self.subTest(mode=mode):
                self.assertFalse(noapp.source_intent(retained, reg, [CATALOG], 'boxa', 'iot')['eligible'])

    def test_backend_and_undeclared_box_are_excluded(self):
        retained = source()
        for box, role in [('boxa', 'bak'), ('missing', 'dmz'), ('boxa', 'router')]:
            with self.assertRaises(UpdateError):
                noapp.source_intent(retained, registry(retained), [CATALOG], box, role)

    def test_mixed_authority_engine_private_commit_input_bytes_and_projection_refuse(self):
        retained = source()
        for change in (
            lambda v: v.update(authority_state_sha256='0' * 64),
            lambda v: v.update(engine_commit='0' * 40),
            lambda v: v['rendered']['repository'].update(head_commit='0' * 40),
            lambda v: v['rendered']['repository'].update(clean=False),
            lambda v: v['rendered'].update(inputs=[]),
            lambda v: v['rendered']['projection'].update(registry_sha256='0' * 64),
            lambda v: v['rendered'].update(valid=False),
        ):
            reg = registry(retained); change(reg)
            with self.subTest(change=change), self.assertRaises(UpdateError):
                noapp.source_intent(retained, reg, [CATALOG], 'boxa', 'dmz')


class Qualification(unittest.TestCase):
    def setUp(self):
        self.fixture = host_fixture.HostInventory(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        fact = {'hostname': 'boxa-dmz', 'box': 'boxa', 'role': 'dmz', 'os': {'id': 'alpine'},
                'architecture': 'x86_64', 'observed_at': timestamp(NOW), 'host_inventory': self.fixture.collect(),
                'containers': [], 'volumes': [], 'storage': {'mounts': self.fixture.mounts}}
        self.observed = {'kind': REPORT_KIND, 'generated_at': timestamp(NOW), 'complete': True,
                         'implementation_commit': 'b' * 40, 'hosts': [{'host': 'boxa-dmz', 'facts': fact,
                         'target': {'box': 'boxa', 'role': 'dmz', 'runtime': 'running'}}]}

    def report(self, observed=None):
        return noapp.report(self.observed if observed is None else observed, 'boxa', 'dmz', 'b' * 40, NOW)

    def test_only_verified_empty_rootful_paths_are_reconstructable(self):
        path = '/var/lib/containers/storage/db.sql'
        entry = {'path': path, 'mode': stat.S_IFREG | 0o600, 'uid': 0, 'gid': 0}
        self.assertEqual(noapp.path_classification(entry)[0], 'unknown')
        self.assertTrue(noapp.path_classification(
            entry, verified_rootful_paths=frozenset({path}))[2])
        changed = {**entry, 'mode': stat.S_IFREG | 0o666}
        self.assertFalse(noapp.path_classification(
            changed, verified_rootful_paths=frozenset({path}))[2])

    def test_unknown_file_and_timers_are_never_archived_away_or_approved(self):
        result = self.report()
        by_key = {row['key']: row for row in result['items']}
        self.assertEqual(by_key['/srv/data/user-file']['classification'], 'unknown')
        self.assertFalse(by_key['/etc/crontabs/root']['resolved'])
        self.assertFalse(result['qualified']); self.assertFalse(result['classification_complete'])
        self.assertFalse(result['adoption_authorized']); self.assertIsNone(result['adoption_intent'])
        self.assertFalse(result['application_tests']['executed'])
        self.assertEqual(result['report_sha256'], digest({k: v for k, v in result.items() if k != 'report_sha256'}))

    def test_cleanup_is_exact_metadata_and_never_implicit_removal_approval(self):
        directory = self.fixture.root / 'var/tmp/klokast-static-site-backup'
        directory.mkdir(parents=True); (directory / 'static-site.tar').write_bytes(b'PRIVATE')
        self.observed['hosts'][0]['facts']['host_inventory'] = self.fixture.collect()
        inventory = self.observed['hosts'][0]['facts']['host_inventory']
        for row in inventory['unowned_paths'] + inventory['unowned_tree']['entries']:
            row['uid'] = 0
        tree = inventory['unowned_tree']
        tree['metadata_sha256'] = digest({k: tree[k] for k in ('roots', 'entries', 'excluded')})
        result = self.report()
        selected = {v['key']: v for v in result['cleanup_items']}
        self.assertIn('/var/tmp/klokast-static-site-backup/static-site.tar', selected)
        for row in selected.values():
            self.assertFalse(row['removal_approved'])
            self.assertEqual(row['evidence_sha256'], digest(row['metadata']))
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual((directory / 'static-site.tar').read_bytes(), b'PRIVATE')

    def test_old_packages_and_expired_source_branch_do_not_add_safety_refusals(self):
        before = self.report()
        self.observed['hosts'][0].update(update_status='unsupported', updates=[{'old': '1', 'new': '2'}],
                                       support={'community': False}, branch='v3.20')
        after = self.report()
        self.assertEqual(before['findings'], after['findings'])

    def test_incomplete_stale_future_and_stopped_guest_never_qualify(self):
        for mode in ('incomplete', 'stale', 'future', 'stopped', 'duplicate'):
            observed = copy.deepcopy(self.observed)
            if mode == 'incomplete': observed['complete'] = False
            elif mode == 'stale': observed['generated_at'] = timestamp(NOW - dt.timedelta(hours=3))
            elif mode == 'future': observed['generated_at'] = timestamp(NOW + dt.timedelta(hours=1))
            elif mode == 'stopped': observed['hosts'][0]['target']['runtime'] = 'stopped'
            else: observed['hosts'].append(observed['hosts'][0])
            with self.subTest(mode=mode):
                result = self.report(observed)
                self.assertFalse(result['qualified']); self.assertEqual(result['items'], [])

    def test_application_container_and_unknown_mount_stay_visible(self):
        fact = self.observed['hosts'][0]['facts']
        fact['containers'] = [{'name': 'new-workload'}]
        fact['storage']['mounts'].append({'path': '/srv/data', 'type': 'nfs', 'root': '/', 'device': '1:2'})
        result = self.report()
        self.assertIn('qualification.containers', {f['code'] for f in result['findings']})
        self.assertTrue(any(v['area'] == 'mount' and v['key'] == '/srv/data' and v['classification'] == 'unknown' for v in result['items']))

    def test_known_filename_cannot_hide_special_file_or_link(self):
        for mode in (stat.S_IFIFO | 0o600, stat.S_IFLNK | 0o777, stat.S_IFREG | 0o666):
            category, _, resolved = noapp.path_classification({'path': '/var/log/apk.log', 'mode': mode, 'uid': 0})
            self.assertEqual(category, 'unknown'); self.assertFalse(resolved)

    def test_legacy_modloop_files_are_reconstructable_only_with_exact_recipe_context(self):
        kernel = '6.18.7-0-virt'
        for path in (f'/lib/modules/{kernel}/kernel/crypto/example.ko',
                     f'/lib/modules/{kernel}/modules.dep',
                     f'/lib/modules/{kernel}/kernel-suffix',
                     '/lib/firmware/qat_4xxx.bin.zst'):
            entry = {'path': path, 'mode': stat.S_IFREG | 0o644,
                     'uid': 0, 'gid': 0, 'links': 1, 'bytes': 32}
            with self.subTest(path=path):
                self.assertEqual(noapp.path_classification(entry, legacy_kernel=kernel)[0],
                                 'reconstructable-os-state')
                self.assertTrue(noapp.path_classification(entry, legacy_kernel=kernel)[2])
                self.assertFalse(noapp.path_classification(entry)[2])
                for key, value in (('uid', 1000), ('links', 2), ('bytes', 17 * 1024 * 1024),
                                   ('mode', stat.S_IFREG | 0o666)):
                    changed = {**entry, key: value}
                    self.assertFalse(noapp.path_classification(changed, legacy_kernel=kernel)[2])
        other = {'path': '/lib/modules/6.18.8-0-virt/kernel/crypto/example.ko',
                 'mode': stat.S_IFREG | 0o644, 'uid': 0, 'gid': 0, 'links': 1, 'bytes': 32}
        self.assertFalse(noapp.path_classification(other, legacy_kernel=kernel)[2])

    def test_busybox_link_requires_exact_target_and_installed_package(self):
        entry = {'path': '/bin/arch', 'mode': stat.S_IFLNK | 0o777, 'uid': 0,
                 'gid': 0, 'link_sha256': noapp.BUSYBOX_LINK_SHA256}
        category, _, resolved = noapp.path_classification(entry, busybox_present=True)
        self.assertEqual(category, 'reconstructable-os-state')
        self.assertTrue(resolved)
        self.assertFalse(noapp.path_classification(entry)[2])
        for key, value in (('uid', 1000), ('gid', 1000), ('link_sha256', '0' * 64)):
            self.assertFalse(noapp.path_classification({**entry, key: value}, busybox_present=True)[2])
        suid = {**entry, 'path': '/bin/mount', 'link_sha256': noapp.BBSUID_LINK_SHA256}
        self.assertTrue(noapp.path_classification(suid, busybox_suid_present=True)[2])
        self.assertFalse(noapp.path_classification(suid, busybox_present=True)[2])
        pinentry = {**entry, 'path': '/usr/bin/pinentry', 'link_sha256': noapp.PINENTRY_LINK_SHA256}
        self.assertTrue(noapp.path_classification(pinentry, pinentry_present=True)[2])
        self.assertFalse(noapp.path_classification({**pinentry, 'path': '/usr/bin/other'}, pinentry_present=True)[2])

    def test_generated_ca_links_require_the_package_chain_and_unchanged_target(self):
        pem = '/etc/ssl/certs/ca-cert-Example_Root.pem'
        hashed = '/etc/ssl/certs/1234abcd.0'
        source = '/usr/share/ca-certificates/mozilla/Example_Root.crt'
        def link(path, target):
            return {'path': path, 'mode': stat.S_IFLNK | 0o777, 'uid': 0, 'gid': 0,
                    'link_sha256': hashlib.sha256(target.encode()).hexdigest()}
        entries = {pem: link(pem, source), hashed: link(hashed, pem.rsplit('/', 1)[1])}
        packages = {'ca-certificates': {}, 'ca-certificates-bundle': {}}
        database = 'a' * 64
        audit = {'kind': 'klokast.vm-package-audit.v1', 'complete': True, 'stable': True,
                 'database_sha256': database, 'protected_paths': 'none',
                 'check_permissions': True, 'differences': [], 'adoption_authorized': False}
        self.assertEqual(noapp.certificate_link_resolutions(entries, packages, audit, database), {pem, hashed})
        for item in entries.values():
            self.assertTrue(noapp.path_classification(
                item, verified_ca_links={pem, hashed})[2])
        self.assertEqual(noapp.certificate_link_resolutions(entries, {}, audit, database), set())
        self.assertEqual(noapp.certificate_link_resolutions(entries, packages,
                                                            {**audit, 'differences': [{'code': 'U', 'path': source}]}, database), set())
        self.assertEqual(noapp.certificate_link_resolutions({**entries, source: {'path': source}},
                                                            packages, audit, database), set())
        changed = {**entries, pem: link(pem, '/tmp/private.crt')}
        self.assertEqual(noapp.certificate_link_resolutions(changed, packages, audit, database), set())
        self.assertEqual(noapp.certificate_link_resolutions(entries, packages,
                                                            {**audit, 'stable': False}, database), set())

    def test_ca_chain_uses_the_collected_apk_database_identity(self):
        original = self.observed['hosts'][0]['facts']['host_inventory']['package_database_sha256']
        with patch.object(noapp, 'certificate_link_resolutions', return_value=set()) as chain:
            self.report()
        self.assertEqual(chain.call_args.args[3], original)

    def test_nginx_default_copy_needs_matching_package_and_audit_evidence(self):
        source = '/usr/share/nginx/http-default_server.conf'
        target = '/etc/nginx/http.d/default.conf'
        database = 'a' * 64
        audit = {'kind': 'klokast.vm-package-audit.v1', 'complete': True, 'stable': True,
                 'database_sha256': database, 'protected_paths': 'none',
                 'check_permissions': True, 'differences': [], 'adoption_authorized': False}
        value = {'kind': 'klokast.vm-nginx-default-copy.v1', 'source': source, 'target': target,
                 'complete': True, 'stable': True, 'present': True, 'source_owned': True,
                 'source_sha256': 'b' * 64, 'target_sha256': 'b' * 64,
                 'matching': True, 'error_log_empty': True,
                 'adoption_authorized': False, 'error': None}
        value['evidence_sha256'] = digest(value)
        entries = {target: {'path': target, 'mode': stat.S_IFREG | 0o644,
                            'uid': 0, 'gid': 0}}
        self.assertTrue(noapp.checked_nginx_default_copy(value, {'nginx': {}}, audit, database, entries))
        self.assertTrue(noapp.path_classification(entries[target], nginx_default_copy=True)[2])
        log = {'path': '/var/log/nginx/error.log', 'mode': stat.S_IFREG | 0o644,
               'uid': 0, 'gid': 0}
        self.assertTrue(noapp.path_classification(log, nginx_error_empty=True)[2])
        self.assertFalse(noapp.path_classification(log, nginx_error_empty=False)[2])
        for changed_value, changed_audit, changed_entries in (
            ({**value, 'stable': False}, audit, entries),
            (value, {**audit, 'differences': [{'code': 'U', 'path': source}]}, entries),
            (value, audit, {**entries, source: {'path': source}}),
            (value, audit, {target: {**entries[target], 'mode': stat.S_IFLNK | 0o777}}),
        ):
            with self.assertRaises(UpdateError):
                noapp.checked_nginx_default_copy(changed_value, {'nginx': {}},
                                                changed_audit, database, changed_entries)

    def test_fixed_service_rule_rejects_changed_scripts_and_extra_boot_state(self):
        service = {'name': 'tailscale', 'script': {'sha256': 'a' * 64},
                   'runlevels': ['default'], 'markers': ['started']}
        self.assertTrue(noapp.fixed_service_resolution(service, {}, True, set()))
        for entries, audited, changed, changed_service in (
            ({'/etc/init.d/tailscale': {}}, True, set(), service),
            ({}, False, set(), service),
            ({}, True, {'/etc/init.d/tailscale'}, service),
            ({}, True, set(), {**service, 'runlevels': ['boot']}),
            ({}, True, set(), {**service, 'markers': ['failed']}),
            ({}, True, set(), {**service, 'name': 'unreviewed'}),
        ):
            self.assertFalse(noapp.fixed_service_resolution(changed_service, entries,
                                                            audited, changed))

    def test_fixed_accounts_do_not_accept_extra_identity_or_role_drift(self):
        account = {'name': 'nginx', 'uid': 103, 'gid': 104,
                   'home': '/var/lib/nginx', 'shell': '/sbin/nologin'}
        self.assertTrue(noapp.fixed_account_classification(account, 'dmz', True,
                                                            {'nginx': {}})[2])
        self.assertFalse(noapp.fixed_account_classification(account, 'iot', True,
                                                             {'nginx': {}})[2])
        self.assertFalse(noapp.fixed_account_classification(account, 'dmz', True, {})[2])
        self.assertFalse(noapp.fixed_account_classification({**account, 'shell': '/bin/ash'},
                                                             'dmz', True, {'nginx': {}})[2])
        self.assertFalse(noapp.fixed_account_classification({**account, 'name': 'app'},
                                                             'dmz', True, {'nginx': {}})[2])
        old = {'name': 'sshd', 'uid': 22, 'gid': 22,
               'home': '/dev/null', 'shell': '/sbin/nologin'}
        self.assertTrue(noapp.fixed_account_classification(old, 'iot', True, {})[2])
        self.assertFalse(noapp.fixed_account_classification(old, 'iot', False, {})[2])
        self.assertFalse(noapp.fixed_account_classification({**old, 'name': 'neo'},
                                                             'iot', True, {})[2])

    def test_legacy_firmware_requires_a_matching_bounded_receipt(self):
        name = '/lib/firmware/qat_402xx.bin.zst'
        shallow = {name: {'path': name, 'mode': stat.S_IFREG | 0o644, 'uid': 0, 'gid': 0}}
        row = {**shallow[name], 'links': 1, 'bytes': 32, 'sha256': 'a' * 64}
        receipt = {'kind': 'klokast.vm-legacy-firmware.v1', 'complete': True,
                   'stable': True, 'files': [row], 'adoption_authorized': False,
                   'error': None}
        receipt['evidence_sha256'] = digest(receipt)
        self.assertEqual(noapp.checked_legacy_firmware(receipt, shallow), [row])
        self.assertTrue(noapp.path_classification(row, legacy_kernel='6.18.8-0-virt')[2])
        for changed in ({**receipt, 'stable': False},
                        {**receipt, 'files': [{**row, 'links': 2}]},
                        {**receipt, 'files': [{**row, 'path': '/tmp/unknown'}]}):
            with self.assertRaises(UpdateError):
                noapp.checked_legacy_firmware(changed, shallow)

    def test_current_ansible_collector_copy_does_not_hide_other_temp_files(self):
        path = '/tmp/ansible-tmp-123.456-7-8/collect-vm-update-facts'
        entry = {'path': path, 'mode': stat.S_IFREG | 0o700, 'uid': 1000, 'gid': 1000}
        receipt = {'kind': 'klokast.vm-inspection-artifact.v1', 'path': path,
                   'pid': 123, 'complete': True, 'stable': True,
                   **{k: entry[k] for k in ('mode', 'uid', 'gid')},
                   'links': 1, 'bytes': noapp.COLLECTOR_SOURCE.stat().st_size,
                   'sha256': hashlib.sha256(noapp.COLLECTOR_SOURCE.read_bytes()).hexdigest(),
                   'adoption_authorized': False, 'error': None}
        receipt['evidence_sha256'] = digest(receipt)
        self.assertEqual(noapp.checked_inspection_artifact(
            receipt, {path: entry}, 123, {'uid': 1000, 'gid': 1000}), path)
        self.assertTrue(noapp.path_classification(entry, inspection_artifact=path)[2])
        other = '/tmp/ansible-tmp-122.456-7-8/collect-vm-update-facts'
        self.assertFalse(noapp.path_classification({**entry, 'path': other},
                                                   inspection_artifact=path)[2])
        for changed in ({**receipt, 'pid': 124},
                        {**receipt, 'sha256': '0' * 64},
                        {**receipt, 'path': other}):
            with self.assertRaises(UpdateError):
                noapp.checked_inspection_artifact(changed, {path: entry}, 123,
                                                  {'uid': 1000, 'gid': 1000})

    def test_only_exact_bounded_tailscale_log_files_are_reconstructable(self):
        entry = {'path': '/home/neo/.local/share/tailscale/tailscaled.log2.txt',
                 'mode': stat.S_IFREG | 0o600, 'uid': 1000, 'gid': 1000,
                 'links': 1, 'bytes': 2803}
        options = {'tailscale_log_owner': {'uid': 1000, 'gid': 1000},
                   'tailscale_present': True}
        self.assertTrue(noapp.path_classification(entry, **options)[2])
        for changed in ({**entry, 'path': entry['path'] + '.old'},
                        {**entry, 'uid': 0},
                        {**entry, 'mode': stat.S_IFREG | 0o666},
                        {**entry, 'links': 2},
                        {**entry, 'bytes': 17 * 1024 * 1024}):
            self.assertFalse(noapp.path_classification(changed, **options)[2])
        self.assertFalse(noapp.path_classification(entry, tailscale_log_owner=options['tailscale_log_owner'])[2])

    def test_only_exact_empty_rootless_runroot_files_are_reconstructable(self):
        entry = {'path': '/tmp/storage-run-1000/libpod/tmp/events/events.log',
                 'mode': stat.S_IFREG | 0o600, 'uid': 1000, 'gid': 1000,
                 'links': 1, 'bytes': 2048}
        self.assertTrue(noapp.path_classification(entry, empty_rootless_runtime=True)[2])
        for changed in ({**entry, 'path': entry['path'] + '.old'}, {**entry, 'uid': 0},
                        {**entry, 'links': 2}, {**entry, 'bytes': 3 * 1024 * 1024},
                        {**entry, 'mode': stat.S_IFREG | 0o666}):
            self.assertFalse(noapp.path_classification(changed, empty_rootless_runtime=True)[2])
        self.assertFalse(noapp.path_classification(entry)[2])


class ConfigComparison(unittest.TestCase):
    def base(self):
        rows = [
            {'area': 'file', 'key': '/etc/motd', 'classification': 'generated-configuration',
             'rule': 'compare with rendered machine inputs', 'resolved': False, 'evidence_sha256': '1' * 64},
            {'area': 'package-difference', 'key': '/etc/fstab',
             'classification': 'generated-configuration', 'rule': 'compare with approved machine recipe',
             'resolved': False, 'evidence_sha256': '2' * 64},
            {'area': 'file', 'key': '/etc/ssh/ssh_host_ed25519_key',
             'classification': 'retained-machine-identity', 'rule': 'retain',
             'resolved': False, 'evidence_sha256': '3' * 64},
        ]
        return noapp.finish({'kind': 'klokast.vm-no-application-qualification.v1',
                             'box': 'boxa', 'role': 'dmz', 'implementation_commit': 'a' * 40,
                             'intent': {'engine_commit': 'a' * 40}, 'items': rows,
                             'cleanup_items': [], 'findings': [],
                             'classification_complete': False, 'qualified': False,
                             'application_tests': {'status': 'not-run', 'executed': False},
                             'adoption_intent': None, 'adoption_authorized': False})

    def comparison(self, base, approved=True):
        expected = {path: {'sha256': 'b' * 64, 'source': 'ansible/roles/vm-base/tasks/main.yml',
                           'source_sha256': 'c' * 64} for path in config_audit.PATHS}
        observed = {path: 'b' * 64 for path in config_audit.PATHS}
        observed['/etc/nftables.nft'] = 'd' * 64
        return config_audit.report('boxa-dmz', 'a' * 40, base['report_sha256'],
                                   expected, observed, approved)

    def test_only_matching_generated_rows_resolve(self):
        base = self.base()
        result = noapp.with_config_comparison(base, self.comparison(base))
        self.assertEqual(result['kind'], 'klokast.vm-no-application-qualification.v2')
        self.assertEqual(result['summary']['unresolved'], 1)
        self.assertFalse(result['classification_complete'])
        self.assertEqual([(row['area'], row['key']) for row in result['items'] if row['resolved']],
                         [('file', '/etc/motd'), ('package-difference', '/etc/fstab')])

    def test_unapproved_or_changed_comparison_cannot_resolve(self):
        base = self.base()
        base['intent']['engine_commit'] = '0' * 40
        base['report_sha256'] = digest({k: v for k, v in base.items() if k != 'report_sha256'})
        unapproved = self.comparison(base, False)
        self.assertEqual(noapp.with_config_comparison(base, unapproved)['summary']['unresolved'], 3)
        with self.assertRaises(UpdateError):
            noapp.with_config_comparison(base, {**unapproved, 'source_commit': '0' * 40})
        changed = copy.deepcopy(unapproved)
        changed['rows'][0]['observed_sha256'] = '0' * 64
        with self.assertRaises(UpdateError):
            noapp.with_config_comparison(base, changed)


class CLI(unittest.TestCase):
    def test_prepare_binds_stable_live_config_without_granting_adoption(self):
        cli = load_cli()
        base = ConfigComparison().base()
        intent = {'box': 'boxa', 'role': 'dmz', 'eligible': True,
                  'engine_commit': 'a' * 40, 'workloads': [], 'datasets': []}
        base['intent'] = intent
        base['report_sha256'] = digest({k: v for k, v in base.items() if k != 'report_sha256'})
        expected = {path: {'sha256': 'b' * 64, 'source': 'ansible/roles/vm-base/tasks/main.yml',
                           'source_sha256': 'c' * 64} for path in config_audit.PATHS}
        observed = {path: 'b' * 64 for path in config_audit.PATHS}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'current.json').write_text(json.dumps({'implementation_commit': 'a' * 40}))
            def command(argv, **_kwargs):
                if argv[0] == 'git':
                    return 'a' * 40 if argv[-1] == 'HEAD' else ''
                if argv[0] == 'ansible-inventory':
                    return '{}'
                return '{}'
            with patch.object(cli, 'STATE', root), patch.object(cli, 'require_controller'), \
                    patch.object(cli, 'command', side_effect=command), \
                    patch.object(cli.vm_no_application, 'source_intent', return_value=intent), \
                    patch.object(cli.vm_no_application, 'report', return_value=base), \
                    patch.object(cli.vm_config_audit, 'render', return_value=expected), \
                    patch.object(cli.vm_config_audit, 'guest_hashes', return_value=observed) as guest:
                result, path = cli.adoption_prepare('boxa', 'dmz')
            self.assertEqual(guest.call_count, 2)
            self.assertEqual(result['kind'], 'klokast.vm-no-application-qualification.v2')
            self.assertEqual(result['summary']['unresolved'], 1)
            self.assertFalse(result['qualified'])
            self.assertEqual(json.loads(path.read_text()), result)
            self.assertEqual(len(list((root / 'config-audits').glob('*.json'))), 1)

    def test_prepare_writes_blocked_report_and_rechecks_both_sources(self):
        cli = load_cli(); retained = source(); reg = registry(retained)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def command(argv, **kwargs):
                if argv[-1] == 'vm-retention-status': return json.dumps(retained)
                if argv[-1] == 'registry-source-status': return json.dumps(reg)
                if argv[-1] == 'HEAD': return 'b' * 40
                return ''
            with patch.object(cli, 'STATE', root), patch.object(cli, 'require_controller'), \
                    patch.object(cli, 'command', side_effect=command), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(['adopt', 'prepare', '--box', 'boxa', '--role', 'dmz', '--json']), 1)
            result = json.loads(output.getvalue())
            self.assertFalse(result['qualified'])
            self.assertEqual(json.loads((root / 'qualifications' / (result['report_sha256'] + '.json')).read_text()), result)
            self.assertEqual(list(root.iterdir()), [root / 'qualifications'])

    def test_changed_source_refuses_record_publication(self):
        cli = load_cli(); retained = source(); reg = registry(retained); reads = []
        with tempfile.TemporaryDirectory() as temporary:
            def command(argv, **kwargs):
                if argv[-1] == 'vm-retention-status':
                    reads.append(1); return json.dumps(retained if len(reads) == 1 else {})
                if argv[-1] == 'registry-source-status': return json.dumps(reg)
                if argv[-1] == 'HEAD': return 'b' * 40
                return ''
            with patch.object(cli, 'STATE', Path(temporary)), patch.object(cli, 'require_controller'), \
                    patch.object(cli, 'command', side_effect=command), self.assertRaisesRegex(UpdateError, 'changed'):
                cli.adoption_prepare('boxa', 'dmz')
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == '__main__': unittest.main()
