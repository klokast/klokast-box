"""The dom0 update history: entries, permissions, and its failure rule."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import machine_status  # noqa: E402
import update_history as history  # noqa: E402

OLD = {'image': 'a' * 24, 'commit': '1' * 40, 'configuration': 'x', 'components': {'role vm-dns': '1', 'Instance values': '2'},
       'packages': {'openssl': '3.5.9-r0', 'tailscale': '1.98.10-r1', 'zlib': '1.3-r0'}}
NEW = {'image': 'b' * 24, 'commit': '2' * 40, 'configuration': 'y', 'components': {'role vm-dns': '3', 'Instance values': '2'},
       'packages': {'openssl': '3.5.9-r1', 'nftables': '1.1.6-r1', 'zlib': '1.3-r0'}}


class Entry(unittest.TestCase):
    def test_names_the_package_and_configuration_changes(self):
        value = history.entry('c' * 24, OLD, NEW, 0)
        self.assertEqual((value['at'], value['kind']), ('1970-01-01T00:00:00Z', 'image and configuration'))
        self.assertEqual(value['packages'], {'added': {'nftables': '1.1.6-r1'}, 'removed': {'tailscale': '1.98.10-r1'},
                                             'changed': {'openssl': ['3.5.9-r0', '3.5.9-r1']}})
        self.assertEqual(value['configuration'], ['role vm-dns'])

    def test_kinds(self):
        for before, after, kind in ((None, NEW, 'first record'), (OLD, OLD, 'rebuild'),
                                    (OLD, dict(OLD, image='b' * 24), 'image'),
                                    (OLD, dict(OLD, configuration='y'), 'configuration')):
            with self.subTest(kind=kind): self.assertEqual(history.entry('c' * 24, before, after)['kind'], kind)

    def test_an_unknown_side_is_not_a_change(self):
        value = history.entry('c' * 24, dict(OLD, packages=None, components=None), NEW)
        self.assertIsNone(value['packages']); self.assertIsNone(value['configuration'])

    def test_upstream_tools_count_as_packages(self):
        self.assertEqual(history.packages({'zlib': '1'}, {'tailscale': {'version': '1.104.7'}, 'claude': {'version': '2.1'}}),
                         {'zlib': '1', 'tailscale': '1.104.7', 'claude-code': '2.1'})
        self.assertIsNone(history.packages(None, {}))


class Record(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'history'

    def record(self, operation='c' * 24):
        return history.record('boxa-dom0', operation, lambda: history.entry(operation, OLD, NEW), self.root)

    def test_a_resumed_operation_adds_no_second_entry_and_any_account_can_read(self):
        previous = os.umask(0o077); self.addCleanup(os.umask, previous)
        self.assertEqual([self.record(), self.record(), self.record('d' * 24)], [None, None, None])
        path = self.root / 'boxa-dom0.jsonl'
        self.assertEqual([json.loads(line)['operation'][0] for line in path.read_text().splitlines()], ['c', 'd'])
        self.assertEqual((path.stat().st_mode & 0o777, self.root.stat().st_mode & 0o777), (0o644, 0o755))
        self.assertEqual([p.name for p in self.root.iterdir()], ['boxa-dom0.jsonl'])

    def test_a_failure_is_a_warning_and_keeps_the_history(self):
        self.record()
        before = (self.root / 'boxa-dom0.jsonl').read_text()
        with patch.object(history.os, 'replace', side_effect=OSError('No space left')):
            self.assertIn('boxa-dom0 was not written: OSError: No space left', self.record('d' * 24))
        self.assertEqual((self.root / 'boxa-dom0.jsonl').read_text(), before)
        self.assertIn('was not written', history.record('../x', 'd' * 24, dict, self.root))

    def test_the_status_reader_accepts_each_written_entry(self):
        self.record()
        lines = ['boxa-dom0 ' + line for line in (self.root / 'boxa-dom0.jsonl').read_text().splitlines()]
        row = machine_status.history(lines)['boxa-dom0'][0]
        self.assertEqual((row['kind'], row['seeded'], row['packages']['changed']),
                         ('image and configuration', False, {'openssl': ['3.5.9-r0', '3.5.9-r1']}))
        for old, new in (('3.5.9-r1', '\\u001b[31m'), ('role vm-dns', 'role \\u001b[0m'), ('"operation": "c', '"operation": "/'),
                         ('"kind": "image', '"kind": "other')):
            with self.subTest(new=new), self.assertRaisesRegex(RuntimeError, 'invalid update history'):
                machine_status.history([lines[0].replace(old, new)])


class Status(unittest.TestCase):
    def test_the_entry_sets_the_last_update_and_a_boot_never_does(self):
        entries = [{'at': 100}, {'at': 200}]
        row = {'target': 'boxa-dom0', 'state': 'reachable', 'updated_at': 999, 'history': {}}
        self.assertIsNone(machine_status.add_updates(row, {})['updated_at'])
        self.assertEqual(machine_status.add_updates(row, {'boxa-dom0': entries})['updated_at'], 200)
        vm = dict(row, target='boxa-vpn-egress')
        self.assertEqual(machine_status.add_updates(vm, {})['updated_at'], 999)
        self.assertEqual(machine_status.add_updates(vm, {'boxa-vpn-egress': entries})['updated_at'], 200)
        self.assertEqual(machine_status.add_updates({'target': 'boxa-dom0', 'state': 'unreachable'}, {}),
                         {'target': 'boxa-dom0', 'state': 'unreachable'})

    def test_the_summary_keeps_the_time_without_the_entries(self):
        row = machine_status.add_updates({'target': 'boxa-dom0', 'state': 'reachable'}, {'boxa-dom0': [{'at': 5}]})
        value = machine_status.summary(row)
        self.assertEqual(value['updated_at'], 5); self.assertNotIn('updates', value)


if __name__ == '__main__':
    unittest.main()
