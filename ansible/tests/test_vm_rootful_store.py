"""Inspect copied rootful registration metadata without initializing Podman."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from test_vm_storage_inventory import load_collector
import vm_storage_inventory as storage


class RootfulStore(unittest.TestCase):
    def setUp(self):
        self.m = load_collector()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.graph = self.root / self.m.ROOTFUL_GRAPH[1:]
        self.graph.mkdir(parents=True)
        self.mounts = [{'path': '/', 'root': '/', 'type': 'ext4', 'device': '1:1'}]

    def database(self, relative='db.sql', rows=False):
        path = self.graph / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.execute('CREATE TABLE DBConfig(SchemaVersion INTEGER, GraphRoot TEXT)')
            connection.execute('INSERT INTO DBConfig VALUES(1, ?)', (self.m.ROOTFUL_GRAPH,))
            for table in self.m.ROOTFUL_TABLES:
                if table != 'DBConfig':
                    connection.execute(f'CREATE TABLE {table}(ID TEXT, JSON TEXT)')
            if rows:
                for table in ('ContainerConfig', 'ContainerState', 'VolumeConfig', 'VolumeState'):
                    connection.execute(f'INSERT INTO {table} VALUES(?, ?)', ('PRIVATE_NAME', 'SECRET_ENVIRONMENT'))
            connection.commit()
        finally:
            connection.close()
        path.chmod(0o600)
        return path

    def collect(self):
        # Native command execution must never be needed for this reader.
        with patch.object(self.m, 'capture', side_effect=AssertionError('unexpected command')):
            return self.m.collect_rootful_store(self.root, self.mounts)

    def test_registered_state_counts_do_not_emit_contents_or_modify_database(self):
        path = self.database(rows=True)
        before = path.read_bytes()
        value = self.collect()
        self.assertTrue(value['complete']); self.assertTrue(value['stable'])
        self.assertFalse(value['adoption_authorized']); self.assertFalse(value['custom_store_coverage'])
        self.assertEqual(value['database']['table_rows']['ContainerConfig'], 1)
        self.assertEqual(value['database']['table_rows']['VolumeConfig'], 1)
        self.assertEqual(value['database']['sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.graph.iterdir()), [path])
        self.assertNotIn('PRIVATE', json.dumps(value)); self.assertNotIn('SECRET', json.dumps(value))
        self.assertEqual(storage.checked_rootful_store(value), value)

    def test_absent_store_and_present_empty_directory_are_distinct(self):
        empty = self.collect()
        self.assertTrue(empty['present']); self.assertEqual(empty['entries'], 1)
        self.assertIsNone(empty['database'])
        storage.checked_rootful_store(empty)
        self.graph.rmdir()
        absent = self.collect()
        self.assertFalse(absent['present']); self.assertEqual(absent['entries'], 0)
        storage.checked_rootful_store(absent)

    def test_empty_registration_does_not_hide_unregistered_data(self):
        self.database('libpod/db.sql')
        (self.graph / 'unregistered-layer').write_text('PRIVATE DATA')
        value = self.collect()
        self.assertEqual(sum(value['database']['table_rows'].values()), 1)
        self.assertEqual(value['entries'], 4)
        self.assertFalse(value['adoption_authorized'])
        self.assertNotIn('PRIVATE DATA', json.dumps(value))

    def test_journal_legacy_database_duplicate_database_and_mount_are_unknown(self):
        self.database()
        for name in ('db.sql-wal', 'db.sql-shm', 'db.sql-journal', 'bolt_state.db'):
            path = self.graph / name
            path.touch()
            with self.subTest(name=name):
                self.assertFalse(self.collect()['complete'])
            path.unlink()
        other = self.database('libpod/db.sql')
        self.assertFalse(self.collect()['complete'])
        other.unlink(); other.parent.rmdir()
        self.mounts.append({'path': self.m.ROOTFUL_GRAPH + '/volume', 'root': '/', 'type': 'ext4', 'device': '2:1'})
        self.assertFalse(self.collect()['complete'])

    def test_unsafe_path_or_database_metadata_is_unknown(self):
        path = self.database()
        path.chmod(0o666)
        self.assertFalse(self.collect()['complete'])
        path.chmod(0o600)
        path.unlink(); path.symlink_to('/missing')
        self.assertFalse(self.collect()['complete'])
        path.unlink(); self.graph.rmdir(); self.graph.symlink_to('/missing')
        self.assertFalse(self.collect()['complete'])

    def test_unknown_schema_and_views_are_refused_without_emitting_sql(self):
        path = self.database()
        connection = sqlite3.connect(path)
        connection.execute('DROP TABLE ContainerConfig')
        connection.execute("CREATE VIEW ContainerConfig AS SELECT 'SECRET' AS ID")
        connection.commit(); connection.close()
        value = self.collect()
        self.assertFalse(value['complete'])
        self.assertNotIn('SECRET', json.dumps(value))
        with self.assertRaises(ValueError):
            self.m.rootful_table_counts(b'not SQLite', time.monotonic() + 2)

    def test_time_entry_and_database_size_limits_fail_closed(self):
        path = self.database()
        with self.assertRaises(ValueError):
            self.m.rootful_table_counts(path.read_bytes(), time.monotonic() - 1)
        with self.assertRaises(ValueError):
            self.m.rootful_metadata(self.root, self.mounts, time.monotonic() - 1)
        with path.open('ab') as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        self.assertFalse(self.collect()['complete'])
        with patch.object(self.m, 'unowned_tree', side_effect=self.m.HostInventoryError('entry limit')):
            self.assertFalse(self.collect()['complete'])

    def test_changed_database_and_tree_remain_unknown(self):
        self.database()
        original = self.m.rootful_database_bytes
        calls = []
        def changed(*args):
            value = original(*args)
            calls.append(True)
            return value if len(calls) == 1 else value + b'changed'
        with patch.object(self.m, 'rootful_database_bytes', side_effect=changed):
            self.assertFalse(self.collect()['complete'])
        original_metadata = self.m.rootful_metadata
        calls.clear()
        def changed_tree(*args):
            value = original_metadata(*args)
            if not calls:
                (self.graph / 'new-file').touch()
            calls.append(True)
            return value
        with patch.object(self.m, 'rootful_metadata', side_effect=changed_tree):
            value = self.collect()
            self.assertTrue(value['complete']); self.assertFalse(value['stable'])
        with self.assertRaises(storage.UpdateError): storage.checked_rootful_store(value)

    def test_receiver_rejects_invented_authority_coverage_and_counts(self):
        self.database()
        original = self.collect()
        for key, replacement in (('adoption_authorized', True), ('custom_store_coverage', True),
                                 ('complete', False), ('stable', False), ('present', False),
                                 ('graph_root', '/outside'), ('entries', True), ('extra', 1)):
            value = copy.deepcopy(original); value[key] = replacement
            with self.subTest(key=key), self.assertRaises(storage.UpdateError):
                storage.checked_rootful_store(value)
        for replacement in (-1, True, '0'):
            value = copy.deepcopy(original)
            value['database']['table_rows']['ContainerConfig'] = replacement
            with self.assertRaises(storage.UpdateError): storage.checked_rootful_store(value)
        for replacement in ([], {}, None):
            value = copy.deepcopy(original); value['database']['path'] = replacement
            with self.assertRaises(storage.UpdateError): storage.checked_rootful_store(value)


if __name__ == '__main__': unittest.main()
