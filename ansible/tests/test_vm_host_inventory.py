"""Host metadata coverage, bounded traversal, and secret exclusion."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from test_vm_storage_inventory import load_collector
import vm_storage_inventory as storage


class HostInventory(unittest.TestCase):
    def setUp(self):
        self.m = load_collector()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('etc/crontabs', 'etc/init.d', 'etc/runlevels/default', 'srv/data', 'lib/apk/db', 'proc',
                     'home/neo/.local/share/containers/storage'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/passwd').write_text('root:SECRET:0:0:PRIVATE:/root:/bin/sh\nneo:x:1000:1000:PRIVATE:/home/neo:/bin/sh\nnobody:x:65534:65534::/:/sbin/nologin\n')
        (self.root / 'etc/crontabs/root').write_text('SECRET=do-not-report\n')
        (self.root / 'etc/init.d/sample').write_text('#!/bin/sh\nSECRET=do-not-report\n')
        (self.root / 'etc/runlevels/default/sample').symlink_to('../../init.d/sample')
        (self.root / 'srv/data/user-file').write_text('PRIVATE APPLICATION CONTENT')
        (self.root / 'proc/must-not-read').write_text('PRIVATE PROC CONTENT')
        (self.root / 'lib/apk/db/installed').write_text('P:base\nV:1\nF:etc\nR:passwd\nF:etc/init.d\nR:sample\nF:srv\nF:srv/data\n')
        self.mounts = [{'path': '/', 'type': 'ext4', 'root': '/', 'device': '1:1'},
                       {'path': '/proc', 'type': 'proc', 'root': '/', 'device': '0:1'}]
        self.graph = '/home/neo/.local/share/containers/storage'

    def collect(self):
        with patch.object(self.m, 'mount_inventory', return_value=self.mounts):
            return self.m.collect_host({'podman_storage': {'graph_root': self.graph, 'graph_root_directory': True}}, self.root)

    def test_inventory_includes_accounts_services_timers_and_unknown_data_without_contents(self):
        value = self.collect()
        self.assertTrue(value['complete'])
        self.assertTrue(value['stable'])
        self.assertFalse(value['data_accounted'])
        self.assertFalse(value['package_integrity_verified'])
        paths = {v['path'] for v in value['unowned_paths']}
        self.assertIn('/srv/data/user-file', paths)
        self.assertNotIn('/etc/passwd', paths)
        self.assertNotIn('/proc/must-not-read', paths)
        self.assertIn({'path': self.graph, 'reason': 'podman-store'}, value['delegated_roots'])
        scripts = {v['path'] for v in value['maintenance_files']}
        self.assertIn('/etc/crontabs/root', scripts)
        self.assertIn('/etc/runlevels/default/sample', scripts)
        self.assertNotIn('SECRET', json.dumps(value))
        self.assertNotIn('PRIVATE', json.dumps(value))
        result = storage.assess_host_data({'host_inventory': value}, 'boxa-bak')
        self.assertFalse(result['adoption_ready'])
        self.assertIsNotNone(result['inventory'])
        self.assertIn('host.unowned-paths', {v['code'] for v in result['findings']})

    def test_symlink_does_not_expand_metadata_scope(self):
        (self.root / 'srv/data/alias').symlink_to(self.root / 'proc')
        value = self.collect()
        rows = {v['path']: v for v in value['unowned_paths']}
        self.assertIn('link_sha256', rows['/srv/data/alias'])
        self.assertNotIn('/srv/data/alias/must-not-read', rows)

    def test_missing_ownership_or_accounts_is_unknown_not_empty_success(self):
        for filename in ('lib/apk/db/installed', 'etc/passwd'):
            path = self.root / filename
            before = path.read_bytes(); path.write_bytes(b'')
            value = self.collect()
            self.assertFalse(value['complete'])
            self.assertIsNone(storage.assess_host_data({'host_inventory': value}, 'boxa-bak')['inventory'])
            path.write_bytes(before)

    def test_changed_script_or_mount_inventory_is_unstable(self):
        original = self.m.maintenance_files
        calls = []
        def changing(root, deadline, mounts):
            result = original(root, deadline, mounts)
            calls.append(True)
            if len(calls) == 1:
                (root / 'etc/init.d/sample').write_text('changed')
            return result
        with patch.object(self.m, 'maintenance_files', side_effect=changing):
            value = self.collect()
        self.assertTrue(value['complete'])
        self.assertFalse(value['stable'])
        self.assertIsNone(storage.assess_host_data({'host_inventory': value}, 'boxa-bak')['inventory'])
        with patch.object(self.m, 'mount_inventory', side_effect=[self.mounts, self.mounts + [{'path': '/new'}]]):
            self.assertFalse(self.m.collect_host({}, self.root)['stable'])

    def test_special_maintenance_file_and_alias_directory_are_refused(self):
        import os
        os.mkfifo(self.root / 'etc/crontabs/pipe')
        self.assertFalse(self.collect()['complete'])
        (self.root / 'etc/crontabs/pipe').unlink()
        (self.root / 'etc/periodic').symlink_to(self.root / 'srv')
        self.assertFalse(self.collect()['complete'])

    def test_special_passwd_and_separate_maintenance_mount_are_refused(self):
        import os
        passwd = self.root / 'etc/passwd'
        original = passwd.read_bytes()
        passwd.unlink(); os.mkfifo(passwd)
        self.assertFalse(self.collect()['complete'])
        passwd.unlink(); passwd.write_bytes(original)
        self.mounts.append({'path': '/etc/crontabs', 'type': 'nfs', 'root': '/', 'device': '0:9'})
        self.assertFalse(self.collect()['complete'])

    def test_deadline_and_excess_paths_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'time or entry limit'):
            self.m.host_tree(self.root, set(), self.mounts, self.graph, time.monotonic() - 1)
        with self.assertRaisesRegex(ValueError, 'time or entry limit'):
            self.m.maintenance_files(self.root, time.monotonic() - 1)

    def test_escaped_and_duplicate_mount_paths_cannot_expand_traversal(self):
        for extra in ({'path': '/srv/escaped\\040space'}, dict(self.mounts[0])):
            with patch.object(self.m, 'mount_inventory', return_value=self.mounts + [extra]):
                self.assertFalse(self.m.collect_host({}, self.root)['complete'])

    def test_package_path_parser_refuses_unsafe_and_incomplete_ownership(self):
        for value in ('', 'P:base\n', 'P:base\nR:file', 'P:base\nF:../etc\nR:file',
                      'P:base\nF:etc\nR:../secret', 'P:base\nF:etc\nR:.'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.m.package_paths(value)

    def test_malformed_observations_and_duplicate_accounts_cannot_hide_unknowns(self):
        original = self.collect()
        for change in (lambda v: v.update(stable=False),
                       lambda v: v['accounts'].append(v['accounts'][0]),
                       lambda v: v['accounts'][0].update(uid=True),
                       lambda v: v['unowned_paths'].append({'path': '/srv/data', 'mode': -1, 'uid': 1, 'gid': 1}),
                       lambda v: v['maintenance_files'].append({'path': '/etc/../secret', 'sha256': 'a' * 64}),
                       lambda v: v['delegated_roots'].append({'path': '/extra', 'reason': 'disposable'})):
            value = copy.deepcopy(original); change(value)
            result = storage.assess_host_data({'host_inventory': value}, 'boxa-bak')
            self.assertIsNone(result['inventory'])
            self.assertFalse(result['adoption_ready'])

    def test_package_ownership_and_claimed_approval_do_not_authorize_adoption(self):
        value = self.collect()
        value.update(package_integrity_verified=True, data_accounted=True)
        value['unowned_paths'] = []
        result = storage.assess_host_data({'host_inventory': value}, 'boxa-bak')
        self.assertFalse(result['adoption_ready'])
        self.assertIn('host.accounting-unverified', {v['code'] for v in result['findings']})

    def test_unclassified_directory_is_an_unresolved_root_not_implicitly_disposable(self):
        opaque = self.root / 'srv/unknown'
        opaque.mkdir()
        (opaque / 'private').write_text('keep all contents')
        self.mounts.append({'path': '/srv/unknown/nested', 'type': 'ext4', 'root': '/', 'device': '2:1'})
        value = self.collect()
        self.assertTrue(value['complete'])
        self.assertIn({'path': '/srv/unknown', 'reason': 'unclassified-directory'}, value['delegated_roots'])
        self.assertIn({'path': '/srv/unknown/nested', 'reason': 'mount'}, value['delegated_roots'])
        self.assertNotIn('/srv/unknown/private', {v['path'] for v in value['unowned_paths']})
        result = storage.assess_host_data({'host_inventory': value}, 'boxa-bak')
        self.assertFalse(result['adoption_ready'])
        self.assertIn('host.unowned-paths', {v['code'] for v in result['findings']})


if __name__ == '__main__':
    unittest.main()
