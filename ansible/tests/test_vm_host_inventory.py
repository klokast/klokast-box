"""Host metadata coverage, bounded traversal, and secret exclusion."""
import copy
import json
import os
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
                     'home/neo/.local/share/containers/storage', 'run/openrc/started'):
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
        (self.root / 'proc/sys/kernel/random').mkdir(parents=True)
        (self.root / 'proc/sys/kernel/random/boot_id').write_text('11111111-1111-1111-1111-111111111111\n')
        self.process(1, '/bin/busybox', parent=0)

    def process(self, pid, executable, parent=1, service=None):
        proc = self.root / 'proc' / str(pid); proc.mkdir()
        fields = ['S', str(parent)] + ['0'] * 18
        fields[19] = '12345'
        (proc / 'stat').write_text(str(pid) + ' (PRIVATE ) process) ' + ' '.join(fields))
        (proc / 'status').write_text('Name:\tPRIVATE\nUid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\n')
        (proc / 'exe').symlink_to(executable)
        (proc / 'cmdline').write_bytes(b'/sbin/supervise-daemon\0' + (service or 'PRIVATE').encode() + b'\0SECRET\0')
        (proc / 'environ').write_bytes(b'SECRET=never-read')
        return proc

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

    def test_runtime_directory_probe_is_exact_and_stable(self):
        for name in ('run/lock', 'var/lib/tailscale'):
            (self.root / name).mkdir(parents=True)
        before = self.m.runtime_directory_metadata(self.root)
        receipt = self.m.collect_runtime_directories(before,
                                                     self.m.runtime_directory_metadata(self.root))
        self.assertTrue(receipt['complete'])
        self.assertTrue(receipt['stable'])
        self.assertEqual([row['path'] for row in receipt['entries']],
                         ['/run/lock', '/var/lib/tailscale'])
        (self.root / 'run/lock').rmdir()
        (self.root / 'run/lock').symlink_to('/tmp/other')
        self.assertFalse(self.m.collect_runtime_directories(
            before, self.m.runtime_directory_metadata(self.root))['stable'])

    def test_symlink_does_not_expand_metadata_scope(self):
        (self.root / 'srv/data/alias').symlink_to(self.root / 'proc')
        value = self.collect()
        rows = {v['path']: v for v in value['unowned_paths']}
        self.assertIn('link_sha256', rows['/srv/data/alias'])
        self.assertNotIn('/srv/data/alias/must-not-read', rows)

    def test_nginx_default_copy_requires_an_owned_source_and_exact_safe_bytes(self):
        source = '/usr/share/nginx/http-default_server.conf'
        target = '/etc/nginx/http.d/default.conf'
        for name in (source, target):
            (self.root / name[1:]).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name[1:]).write_text('server { listen 80; }\n')
        log = self.root / 'var/log/nginx/error.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.touch()
        good = self.m.collect_nginx_default_copy(self.root, {source}, self.mounts)
        self.assertTrue(good['complete']); self.assertTrue(good['stable'])
        self.assertTrue(good['matching']); self.assertTrue(good['source_owned'])
        self.assertTrue(good['error_log_empty'])
        self.assertNotIn('listen 80', json.dumps(good))
        self.assertFalse(self.m.collect_nginx_default_copy(self.root, set(), self.mounts)['matching'])
        log.write_text('request details\n')
        self.assertFalse(self.m.collect_nginx_default_copy(self.root, {source}, self.mounts)['error_log_empty'])
        (self.root / target[1:]).write_text('different default\n')
        self.assertFalse(self.m.collect_nginx_default_copy(self.root, {source}, self.mounts)['matching'])
        (self.root / target[1:]).unlink()
        (self.root / target[1:]).symlink_to(self.root / source[1:])
        self.assertFalse(self.m.collect_nginx_default_copy(self.root, {source}, self.mounts)['complete'])
        (self.root / target[1:]).unlink()
        (self.root / target[1:]).write_text('server { listen 80; }\n')
        parent = (self.root / target[1:]).parent
        parent.rename(parent.with_name('http.d-real'))
        parent.symlink_to(parent.with_name('http.d-real'))
        self.assertFalse(self.m.collect_nginx_default_copy(self.root, {source}, self.mounts)['complete'])

    def test_legacy_firmware_receipt_rejects_links_and_changed_shape(self):
        name = '/lib/firmware/qat_402xx.bin.zst'
        target = self.root / name[1:]
        target.parent.mkdir(parents=True)
        target.write_bytes(b'firmware bytes')
        result = self.m.collect_legacy_firmware(self.root, self.mounts)
        self.assertTrue(result['complete']); self.assertTrue(result['stable'])
        self.assertEqual([row['path'] for row in result['files']], [name])
        self.assertNotIn('firmware bytes', json.dumps(result))
        target.unlink()
        target.symlink_to('/tmp/other')
        self.assertFalse(self.m.collect_legacy_firmware(self.root, self.mounts)['complete'])
        target.unlink()
        target.write_bytes(b'firmware bytes')
        target.parent.rename(target.parent.with_name('firmware-real'))
        target.parent.symlink_to(target.parent.with_name('firmware-real'))
        self.assertFalse(self.m.collect_legacy_firmware(self.root, self.mounts)['complete'])

    def test_running_collector_receipt_binds_only_its_exact_staged_copy(self):
        path = '/tmp/ansible-tmp-123.456-7-8/collect-vm-update-facts'
        target = self.root / path[1:]
        target.parent.mkdir(parents=True)
        target.write_bytes(Path(self.m.__file__).read_bytes())
        owner = {'uid': os.geteuid(), 'gid': os.getegid()}
        with patch.object(self.m.os.path, 'abspath', return_value=path):
            result = self.m.collect_inspection_artifact(self.root, owner)
            self.assertTrue(result['complete']); self.assertTrue(result['stable'])
            self.assertEqual(result['path'], path)
            target.write_bytes(b'other collector')
            self.assertNotEqual(self.m.collect_inspection_artifact(self.root, owner)['sha256'],
                                result['sha256'])
            target.unlink()
            target.symlink_to('/tmp/other')
            self.assertFalse(self.m.collect_inspection_artifact(self.root, owner)['complete'])

    def test_deep_metadata_accounts_for_directories_without_file_contents(self):
        (self.root / 'home/neo/saved-data').write_text('PRIVATE APPLICATION CONTENT')
        (self.root / 'home/neo/alias').symlink_to(self.root / 'proc')
        (self.root / self.graph[1:] / 'private-container-file').write_text('SECRET')
        value = self.collect()
        tree = value['unowned_tree']
        self.assertTrue(tree['complete']); self.assertTrue(tree['stable'])
        self.assertFalse(tree['data_accounted'])
        paths = {v['path'] for v in tree['entries']}
        self.assertIn('/home/neo/saved-data', paths)
        self.assertIn('/home/neo/alias', paths)
        self.assertNotIn('/home/neo/alias/must-not-read', paths)
        self.assertNotIn(self.graph + '/private-container-file', paths)
        self.assertIn({'path': self.graph, 'reason': 'podman-store'}, tree['excluded'])
        self.assertNotIn('PRIVATE', json.dumps(tree)); self.assertNotIn('SECRET', json.dumps(tree))
        checked = storage.checked_unowned_tree(tree, value['delegated_roots'])
        self.assertEqual(checked, tree)

    def test_deep_metadata_failure_does_not_hide_shallow_inventory(self):
        with patch.object(self.m, 'unowned_tree', side_effect=self.m.HostInventoryError('bounded tree failed')):
            value = self.collect()
        self.assertTrue(value['complete']); self.assertTrue(value['stable'])
        self.assertFalse(value['unowned_tree']['complete'])
        codes = {v['code'] for v in storage.assess_host_data({'host_inventory': value}, 'boxa-iot')['findings']}
        self.assertIn('host.unowned-tree-unknown', codes)

    def test_deep_metadata_boundaries_and_changes_are_not_empty_success(self):
        delegated = [{'path': '/home/neo', 'reason': 'unclassified-directory'}]
        mounts = self.mounts + [{'path': '/home/neo/attached', 'type': 'ext4', 'root': '/', 'device': '2:1'}]
        (self.root / 'home/neo/attached').mkdir()
        (self.root / 'home/neo/attached/private-file').touch()
        value = self.m.unowned_tree(self.root, delegated, mounts, self.graph, time.monotonic() + 10)
        self.assertIn({'path': '/home/neo/attached', 'reason': 'mount'}, value['excluded'])
        self.assertNotIn('/home/neo/attached/private-file', [v['path'] for v in value['entries']])
        for deadline, limit in ((time.monotonic() - 1, 8192), (time.monotonic() + 10, 1)):
            with self.assertRaises(self.m.HostInventoryError):
                self.m.unowned_tree(self.root, delegated, mounts, self.graph, deadline, limit=limit)
        original = self.m.unowned_tree; calls = []
        def change(*args):
            result = original(*args)
            if not calls: (self.root / 'home/neo/changed').touch()
            calls.append(True)
            return result
        with patch.object(self.m, 'unowned_tree', side_effect=change):
            value = self.m.collect_unowned(self.root, delegated, mounts, self.graph, time.monotonic() + 10)
        self.assertTrue(value['complete']); self.assertFalse(value['stable'])

    def test_deep_inventory_rejects_forged_scope_and_missing_parent(self):
        original = self.collect()
        for change in (lambda v: v.update(data_accounted=True),
                       lambda v: v.update(roots=[]), lambda v: v.update(metadata_sha256='f' * 64),
                       lambda v: v['entries'].pop(0), lambda v: v['excluded'].clear(),
                       lambda v: v['entries'][0].update(path='/outside/other'),
                       lambda v: v['entries'][0].update(contents='SECRET')):
            tree = copy.deepcopy(original['unowned_tree']); change(tree)
            # Recomputing a public hash cannot grant scope or repair coverage.
            if tree['metadata_sha256'] != 'f' * 64:
                tree['metadata_sha256'] = storage.digest({k: tree[k] for k in ('roots', 'entries', 'excluded')})
            with self.assertRaises(storage.UpdateError):
                storage.checked_unowned_tree(tree, original['delegated_roots'])

    def test_unmarked_supervisor_is_visible_without_arguments_or_environment(self):
        self.process(21, '/sbin/supervise-daemon', service='sample')
        self.process(22, '/usr/sbin/tailscaled', parent=21)
        value = self.collect()
        self.assertTrue(value['complete']); self.assertTrue(value['stable'])
        self.assertNotIn('PRIVATE', json.dumps(value)); self.assertNotIn('SECRET', json.dumps(value))
        rows = value['processes']['processes']
        self.assertEqual(rows[1]['service'], 'sample')
        result = storage.assess_host_data({'host_inventory': value}, 'boxa-dmz')
        self.assertIn('host.unmarked-supervisor', {v['code'] for v in result['findings']})
        (self.root / 'run/openrc/started/sample').symlink_to('/etc/init.d/sample')
        result = storage.assess_host_data({'host_inventory': self.collect()}, 'boxa-dmz')
        self.assertNotIn('host.unmarked-supervisor', {v['code'] for v in result['findings']})
        self.assertFalse(result['adoption_ready'])

    def test_changed_pid_identity_makes_the_whole_host_snapshot_unstable(self):
        original = self.m.process_inventory
        calls = []
        def changed(*args):
            result = original(*args)
            if not calls:
                p = self.root / 'proc/1/stat'
                p.write_text(p.read_text().replace('12345', '12346'))
            calls.append(True)
            return result
        with patch.object(self.m, 'process_inventory', side_effect=changed):
            value = self.collect()
        self.assertTrue(value['complete']); self.assertFalse(value['stable'])

    def test_fixed_process_roles_do_not_approve_arbitrary_shell_commands(self):
        (self.root / 'proc/1/cmdline').write_bytes(b'/sbin/init\0')
        getty = self.process(21, '/bin/busybox')
        getty.joinpath('cmdline').write_bytes(b'/sbin/getty\00038400\0tty1\0')
        shell = self.process(22, '/bin/busybox')
        shell.joinpath('cmdline').write_bytes(b'sh\0-c\0PRIVATE SECRET\0')
        value = self.collect()
        rows = value['processes']['processes']
        self.assertEqual([row['no_application_role'] for row in rows],
                         ['os-init', 'console-getty', 'unknown'])
        self.assertNotIn('PRIVATE', json.dumps(value)); self.assertNotIn('SECRET', json.dumps(value))
        self.assertIsNotNone(storage.assess_host_data({'host_inventory': value}, 'boxa-dmz')['inventory'])
        getty.joinpath('cmdline').write_bytes(b'/sbin/getty\00038400\0tty1\0EXTRA\0')
        self.assertEqual(self.collect()['processes']['processes'][1]['no_application_role'], 'unknown')

    def test_process_roles_cannot_claim_other_identity_or_inspection_ancestry(self):
        self.process(21, '/usr/bin/python3.14')
        self.process(22, '/bin/busybox')
        original = self.collect()
        for role in ('os-init', 'console-getty', 'kernel-thread', 'podman-pause',
                     'tailscale-supervisor', 'tailscale-daemon', 'inspection-process'):
            value = copy.deepcopy(original)
            value['processes']['processes'][1]['no_application_role'] = role
            result = storage.assess_host_data({'host_inventory': value}, 'boxa-dmz')
            self.assertIn('host.processes-unknown', {v['code'] for v in result['findings']})
        value = copy.deepcopy(original)
        value['processes']['collector_pid'] = 21
        value['processes']['processes'][1]['no_application_role'] = 'inspection-process'
        self.assertIsNotNone(storage.assess_host_data({'host_inventory': value}, 'boxa-dmz')['inventory'])
        value['processes']['processes'][2]['no_application_role'] = 'inspection-process'
        result = storage.assess_host_data({'host_inventory': value}, 'boxa-dmz')
        self.assertIn('host.processes-unknown', {v['code'] for v in result['findings']})

    def test_deleted_and_missing_user_executables_are_unknown(self):
        p = self.process(21, '/usr/sbin/tailscaled (deleted)')
        for deleted in (True, False):
            if not deleted: (p / 'exe').unlink()
            value = self.collect()
            result = storage.assess_host_data({'host_inventory': value}, 'boxa-dmz')
            self.assertIn('host.process-executable-unknown', {v['code'] for v in result['findings']})

    def test_process_limits_and_incomplete_or_forged_coverage_fail_closed(self):
        services = self.service_snapshot()
        with self.assertRaisesRegex(ValueError, 'time limit'):
            self.m.process_inventory(self.root, services, time.monotonic() - 1)
        original = self.collect()
        for change in (lambda v: v.update(processes=[]), lambda v: v.update(boot_id='invalid'),
                       lambda v: v.update(health_verified=True),
                       lambda v: v['processes'].append(copy.deepcopy(v['processes'][0])),
                       lambda v: v['processes'][0].update(parent_pid=42),
                       lambda v: v['processes'][0].update(uids=[0]),
                       lambda v: v['processes'][0].update(service='sample'),
                       lambda v: v['processes'][0].update(kernel_thread=True),
                       lambda v: v['processes'][0].update(arguments=['SECRET'])):
            value = copy.deepcopy(original); change(value['processes'])
            result = storage.assess_host_data({'host_inventory': value}, 'boxa-dmz')
            self.assertIn('host.processes-unknown', {v['code'] for v in result['findings']})
        (self.root / 'proc/1/status').write_text('Uid:\t0\n')
        self.assertFalse(self.collect()['complete'])

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


    def service_snapshot(self):
        return self.m.native_services(self.root, self.m.maintenance_files(self.root, time.monotonic() + 10),
                                      time.monotonic() + 10)

    def test_native_services_report_disabled_and_manual_services_without_running_scripts(self):
        marker = self.root / 'run/openrc/started/manual'
        marker.symlink_to('/etc/init.d/manual')
        (self.root / 'etc/init.d/manual').write_text('#!/bin/sh\nexit 99\n')
        (self.root / 'etc/init.d/disabled').write_text('#!/bin/sh\nexit 99\n')
        with patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no service execution')):
            value = self.collect()
        self.assertTrue(value['complete'])
        rows = {v['name']: v for v in value['native_services']['services']}
        self.assertEqual(rows['sample']['runlevels'], ['default'])
        self.assertEqual(rows['sample']['markers'], [])
        self.assertEqual(rows['disabled']['runlevels'], [])
        self.assertEqual(rows['disabled']['markers'], [])
        self.assertEqual(rows['manual']['markers'], ['started'])
        self.assertEqual(rows['manual']['runlevels'], [])
        self.assertFalse(value['native_services']['health_verified'])
        result = storage.assess_host_data({'host_inventory': value}, 'boxa-iot')
        self.assertIn('native_services', result['inventory'])
        self.assertFalse(result['adoption_ready'])

    def test_missing_openrc_is_unknown_not_stopped(self):
        (self.root / 'run/openrc/started').rmdir()
        self.assertFalse(self.collect()['complete'])
        value = self.collect()
        value.update(complete=True, stable=True)
        result = storage.assess_host_data({'host_inventory': value}, 'boxa-iot')
        self.assertIn('host.services-unknown', {v['code'] for v in result['findings']})

    def test_old_metadata_report_is_not_complete_service_evidence(self):
        value = self.collect()
        del value['native_services']
        result = storage.assess_host_data({'host_inventory': value}, 'boxa-iot')
        self.assertIn('host.services-unknown', {v['code'] for v in result['findings']})

    def test_orphan_failed_and_scheduled_services_stay_visible(self):
        for state in ('failed', 'scheduled/dependency'):
            directory = self.root / 'run/openrc' / state
            directory.mkdir(parents=True)
            (directory / 'orphan').symlink_to('/etc/init.d/orphan')
        value = self.collect()
        self.assertTrue(value['complete'])
        result = storage.assess_host_data({'host_inventory': value}, 'boxa-iot')
        self.assertTrue({'host.service-script-missing', 'host.service-failed', 'host.service-transition'} <=
                        {v['code'] for v in result['findings']})

    def test_marker_directory_alias_or_special_entry_fails_closed(self):
        import os
        directory = self.root / 'run/openrc/failed'
        directory.symlink_to(self.root / 'srv/data')
        self.assertFalse(self.collect()['complete'])
        directory.unlink(); directory.mkdir()
        os.mkfifo(directory / 'secret')
        self.assertFalse(self.collect()['complete'])

    def test_marker_links_are_never_followed_or_emitted(self):
        (self.root / 'run/openrc/started/sample').symlink_to('/srv/SECRET-do-not-read')
        result = self.service_snapshot()
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(result['services'][0]['markers'], ['started'])

    def test_marker_change_between_passes_is_unstable(self):
        original = self.m.native_services
        calls = []
        def changing(*args):
            value = original(*args)
            if not calls:
                (self.root / 'run/openrc/started/sample').symlink_to('/etc/init.d/sample')
            calls.append(True)
            return value
        with patch.object(self.m, 'native_services', side_effect=changing):
            value = self.collect()
        self.assertTrue(value['complete'])
        self.assertFalse(value['stable'])
        self.assertIsNone(storage.assess_host_data({'host_inventory': value}, 'boxa-iot')['inventory'])

    def test_marker_link_change_is_unstable_even_if_service_name_is_unchanged(self):
        marker = self.root / 'run/openrc/started/sample'
        marker.symlink_to('/etc/init.d/sample')
        before = self.service_snapshot()
        marker.unlink(); marker.symlink_to('/etc/init.d/other')
        self.assertNotEqual(before['markers_sha256'], self.service_snapshot()['markers_sha256'])

    def test_service_limits_and_unsafe_names_refuse_inventory(self):
        with self.assertRaisesRegex(ValueError, 'time or entry limit'):
            (self.root / 'run/openrc/started/sample').symlink_to('/etc/init.d/sample')
            self.m.native_services(self.root, [], time.monotonic() - 1)
        (self.root / 'run/openrc/started/bad\nname').symlink_to('/etc/init.d/sample')
        self.assertFalse(self.collect()['complete'])

    def test_forged_native_service_coverage_cannot_hide_a_workload(self):
        original = self.collect()
        for change in (lambda v: v.update(health_verified=True),
                       lambda v: v.update(services=[]),
                       lambda v: v['services'].append(copy.deepcopy(v['services'][0])),
                       lambda v: v['services'][0].update(script=None),
                       lambda v: v['services'][0].update(runlevels=[]),
                       lambda v: v['services'][0].update(markers=['healthy']),
                       lambda v: v['services'][0].update(markers=[True]),
                       lambda v: v.update(markers_sha256=None)):
            value = copy.deepcopy(original); change(value['native_services'])
            result = storage.assess_host_data({'host_inventory': value}, 'boxa-iot')
            self.assertIn('host.services-unknown', {v['code'] for v in result['findings']})
            self.assertNotIn('native_services', result['inventory'])
            self.assertFalse(result['adoption_ready'])


if __name__ == '__main__':
    unittest.main()
