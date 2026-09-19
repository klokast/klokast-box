"""An empty Podman registration database must not hide unregistered files."""
import copy
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_vm_rootful_store as rootful_fixture
from platform_updates import digest, UpdateError
import vm_no_application as noapp


class EmptyStore(unittest.TestCase):
    def setUp(self):
        self.fixture = rootful_fixture.RootfulStore(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.m = self.fixture.m; self.root = self.fixture.root
        self.graph = '/home/neo/.local/share/containers/storage'
        self.fixture.graph = self.root / self.graph[1:]
        self.fixture.graph.mkdir(parents=True)
        self.path = self.fixture.database()
        connection = sqlite3.connect(self.path)
        connection.execute('UPDATE DBConfig SET GraphRoot=?', (self.graph,))
        connection.commit(); connection.close()
        self.owner = {'uid': os.getuid(), 'gid': os.getgid()}

    def collect(self):
        with patch.object(self.m, 'capture', side_effect=AssertionError('must not initialize or run Podman')):
            return self.m.collect_no_application_store(self.root, self.fixture.mounts, self.owner)

    def test_exact_empty_store_is_reconstructable_but_has_no_adoption_authority(self):
        before = self.path.read_bytes()
        value = self.collect()
        self.assertTrue(value['empty']); self.assertTrue(value['complete']); self.assertTrue(value['stable'])
        self.assertFalse(value['adoption_authorized'])
        self.assertEqual(value['unresolved_paths'], [])
        self.assertEqual(noapp.checked_empty_store(value), value)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unregistered_data_and_registered_workload_cannot_be_declared_empty(self):
        p = self.fixture.graph / 'hidden'; p.write_bytes(b'PRIVATE')
        value = self.collect()
        self.assertFalse(value['empty']); self.assertIn(self.graph + '/hidden', value['unresolved_paths'])
        noapp.checked_empty_store(value)
        p.unlink()
        connection = sqlite3.connect(self.path)
        connection.execute('INSERT INTO ContainerConfig VALUES(?,?)', ('private-name', 'SECRET'))
        connection.commit(); connection.close()
        value = self.collect()
        self.assertFalse(value['empty']); self.assertIn(self.graph + '/db.sql', value['unresolved_paths'])
        self.assertNotIn('SECRET', str(value)); self.assertNotIn('private-name', str(value))

    def test_exact_markers_are_supported_but_image_indexes_and_extra_bytes_are_not(self):
        directory = self.fixture.graph / 'overlay-images'; directory.mkdir()
        marker = directory / 'images.json'; marker.write_bytes(b'[]')
        self.assertTrue(self.collect()['empty'])
        marker.write_bytes(b'[{"image":"SECRET"}]')
        value = self.collect()
        self.assertFalse(value['empty']); self.assertNotIn('SECRET', str(value))
        marker.write_bytes(b'[]EXTRA')
        self.assertFalse(self.collect()['empty'])

    def test_changed_database_link_mount_and_timeout_fail_closed(self):
        original = self.m.rootful_database_bytes; reads = []
        def changed(*args, **kwargs):
            reads.append(1); data = original(*args, **kwargs)
            return data if len(reads) == 1 else data + b'changed'
        with patch.object(self.m, 'rootful_database_bytes', side_effect=changed):
            self.assertFalse(self.collect()['complete'])
        with patch.object(self.m, 'rootful_metadata', side_effect=self.m.HostInventoryError('deadline')):
            self.assertFalse(self.collect()['complete'])
        self.fixture.mounts.append({'path': self.graph + '/volumes', 'root': '/', 'type': 'ext4', 'device': '1:2'})
        self.assertFalse(self.collect()['complete']); self.fixture.mounts.pop()
        self.path.unlink(); self.path.symlink_to('/not-readable')
        self.assertFalse(self.collect()['complete'])

    def test_binary_lock_identifier_requires_native_timestamp_counter_and_pid(self):
        marker = self.fixture.graph / 'storage.lock'
        timestamp = 1789778963100000000
        content = timestamp.to_bytes(8, 'little') + (1).to_bytes(8, 'little') + (123).to_bytes(4, 'little') + bytes(range(44))
        marker.write_bytes(content); os.utime(marker, ns=(timestamp, timestamp))
        self.assertTrue(self.collect()['empty'])
        for changed in (content + b'X', b'PRIVATE'.ljust(64, b'X'),
                        content[:8] + bytes(8) + content[16:], content[:16] + bytes(4) + content[20:]):
            marker.write_bytes(changed); os.utime(marker, ns=(timestamp, timestamp))
            self.assertFalse(self.collect()['empty'])
        marker.write_bytes(content); os.utime(marker, ns=(timestamp, timestamp + 2_000_000_000))
        self.assertFalse(self.collect()['empty'])

    def test_receiver_refuses_tampering_omitted_coverage_and_false_empty_claim(self):
        original = self.collect()
        for field, replacement in [('adoption_authorized', True), ('stable', False), ('graph_root', '/outside'),
                                   ('complete', False), ('metadata', {}), ('empty', False), ('database_sha256', None)]:
            value = copy.deepcopy(original); value[field] = replacement
            value['evidence_sha256'] = digest({k: v for k, v in value.items() if k != 'evidence_sha256'})
            with self.subTest(field=field), self.assertRaises(UpdateError): noapp.checked_empty_store(value)
        value = copy.deepcopy(original); value['unresolved_paths'] = [self.graph + '/db.sql']
        value['evidence_sha256'] = digest({k: v for k, v in value.items() if k != 'evidence_sha256'})
        with self.assertRaises(UpdateError): noapp.checked_empty_store(value)


if __name__ == '__main__': unittest.main()
