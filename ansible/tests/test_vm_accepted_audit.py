"""Accepted evidence may resolve only the files and mounts it proves."""
import copy
import datetime as dt
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from platform_updates import digest, UpdateError, timestamp
import vm_accepted_audit as audit
from test_vm_storage_inventory import load_collector


def seal(value):
    return dict(value, evidence_sha256=digest(value))


class AcceptedFiles(unittest.TestCase):
    def setUp(self):
        self.m = load_collector()
        work = tempfile.TemporaryDirectory(); self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.names = frozenset({'etc/shadow', 'etc/hosts'})
        self.shadow_group = 0
        (self.root / 'etc').mkdir()
        for path in self.names:
            (self.root / path).write_text('PRIVATE CONTENT\n')
            (self.root / path).chmod(0o600)

    def collect(self):
        native_fstat, native_stat = os.fstat, os.stat
        shadow = self.root / 'etc/shadow'
        inode = shadow.lstat().st_ino if shadow.exists() else None
        def root_owner(info):
            # Preserve all metadata except fixture ownership on the non-root runner.
            from types import SimpleNamespace
            value = {k: getattr(info, k) for k in ('st_mode', 'st_dev', 'st_ino', 'st_uid', 'st_gid',
                        'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
            value.update(st_uid=0, st_gid=self.shadow_group if info.st_ino == inode else 0)
            return SimpleNamespace(**value)
        with patch.object(self.m, 'ACCEPTED_FILES', self.names), \
                patch.object(self.m.os, 'fstat', side_effect=lambda *a, **kw: root_owner(native_fstat(*a, **kw))), \
                patch.object(self.m.os, 'stat', side_effect=lambda *a, **kw: root_owner(native_stat(*a, **kw))):
            return self.m.accepted_file_hashes(self.root)

    def test_fixed_hashes_do_not_export_private_contents(self):
        result = self.collect()
        self.assertTrue(result['complete'] and result['stable'])
        self.assertEqual(result['files']['/etc/shadow']['sha256'], hashlib.sha256(b'PRIVATE CONTENT\n').hexdigest())
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(result['evidence_sha256'], digest({k: v for k, v in result.items() if k != 'evidence_sha256'}))

    def test_package_shadow_group_is_scoped_to_the_shadow_file(self):
        self.shadow_group = 42
        self.assertTrue(self.collect()['complete'])
        self.shadow_group = 104
        self.assertFalse(self.collect()['complete'])

    def test_links_world_write_and_missing_files_refuse(self):
        path = self.root / 'etc/shadow'
        path.chmod(0o666)
        self.assertFalse(self.collect()['complete'])
        path.unlink(); path.symlink_to('hosts')
        self.assertFalse(self.collect()['complete'])
        path.unlink()
        self.assertFalse(self.collect()['complete'])


class AcceptedAudit(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
        self.source = {'kind': 'klokast.vm-replacement-source.v1', 'dom0': 'boxa-dom0', 'role': 'iot',
            'runtime': 'running', 'autostart': True, 'observed_at': int(self.now.timestamp()),
            'operation_id': 'a' * 24, 'vm_uuid': '11111111-1111-1111-1111-111111111111',
            'files_sha256': {'etc/hostname': '1' * 64}, 'release': {'packages': {'p': '1'}, 'kernel_release': 'kernel'},
            'artifacts': {'kernel': {'sha256': '2' * 64}, 'initramfs': {'sha256': '3' * 64}}}
        self.source['release']['release_sha256'] = digest(self.source['release'])
        self.source['disk_mappings'] = {'/dev/vg0/vmupd_' + 'a' * 24 + suffix: device
                    for suffix, device in (('_root', 'xvda'), ('_data', 'xvdb'))}
        self.source['disks'] = {p: {} for p in self.source['disk_mappings']}
        self.mounts = [{'path': path, 'root': '/', 'type': 'ext4', 'device': device}
                        for path, device in (('/', '202:0'), ('/srv/retained', '202:16'))]
        self.fact = {'packages': {'p': {'version': '1'}}, 'kernel': 'kernel', 'storage': {'mounts': self.mounts},
            'accepted_mount_sources': seal({'kind': 'klokast.vm-accepted-mount-sources.v1', 'complete': True,
                'stable': True, 'error': None, 'devices': {'/': {'source': '/dev/xvda', 'device': '202:0'},
                    '/srv/retained': {'source': '/dev/xvdb', 'device': '202:16'}}}),
            'accepted_file_hashes': seal({'kind': 'klokast.vm-accepted-file-hashes.v1', 'complete': True,
                'stable': True, 'error': None, 'files': {'/etc/hostname': {'sha256': '1' * 64, 'mode': 0o644, 'uid': 0, 'gid': 0}}})}
        self.discovery = {'generated_at': timestamp(self.now), 'hosts': [{'host': 'boxa-iot', 'facts': self.fact}]}
        self.base = {'kind': 'klokast.vm-no-application-qualification.v1', 'box': 'boxa', 'role': 'iot',
            'discovery_sha256': digest(self.discovery), 'qualified': False, 'cleanup_items': [], 'findings': [],
            'items': [{'area': 'file', 'key': path, 'resolved': False, 'classification': 'unknown',
                       'rule': 'unresolved', 'evidence_sha256': 'a' * 64}
                      for path in ('/etc/hostname', '/home/neo/new-workload')]}
        self.base['report_sha256'] = digest(self.base)
        self.boot = {'kind': 'klokast.vm-boot-files.v2', 'artifacts': {'/boot/vmlinuz-virt': '2' * 64, '/boot/initramfs-virt': '3' * 64}}

    def compare(self):
        with patch.object(audit.noapp, 'checked_boot_files', return_value=self.boot):
            return audit.compare(self.base, self.source, self.discovery, self.now)

    def test_new_application_file_stays_unresolved(self):
        result = self.compare()
        self.assertTrue(result['generated_files_match'] and result['boot_match'] and result['packages_match'])
        self.assertEqual(result['summary']['unresolved'], 1)
        self.assertFalse(result['qualified'])
        self.assertFalse(next(r for r in result['items'] if r['key'] == '/home/neo/new-workload')['resolved'])

    def test_changed_personalization_cannot_resolve_a_file(self):
        self.source['files_sha256']['etc/hostname'] = 'b' * 64
        result = self.compare()
        self.assertFalse(result['generated_files_match'])
        self.assertEqual(result['summary']['unresolved'], 2)

    def test_matching_hash_with_changed_mode_is_not_accepted(self):
        self.fact['accepted_file_hashes']['files']['/etc/hostname']['mode'] = 0o755
        value = self.fact['accepted_file_hashes']
        value['evidence_sha256'] = digest({k: v for k, v in value.items() if k != 'evidence_sha256'})
        self.base['discovery_sha256'] = digest(self.discovery)
        self.base['report_sha256'] = digest({k: v for k, v in self.base.items() if k != 'report_sha256'})
        self.assertFalse(self.compare()['generated_files_match'])

    def test_stale_or_spliced_sources_refuse(self):
        for field, value in (('observed_at', 1), ('dom0', 'boxb-dom0'), ('runtime', 'stopped'),
                             ('operation_id', 'b' * 24), ('files_sha256', {'etc/../private': 'a' * 64})):
            original = copy.deepcopy(self.source)
            self.source[field] = value
            with self.subTest(field=field), self.assertRaises(UpdateError): self.compare()
            self.source = original


if __name__ == '__main__': unittest.main()
