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


class CLI(unittest.TestCase):
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
