"""Native audit evidence never becomes approval to discard modified files."""
import copy
import hashlib
import unittest
from unittest.mock import patch

from test_vm_storage_inventory import load_collector
import vm_storage_inventory as storage


class PackageAudit(unittest.TestCase):
    def setUp(self):
        self.collector = load_collector()
        self.database = 'P:synthetic\nV:1-r0\n'
        self.sha = hashlib.sha256(self.database.encode()).hexdigest()

    def collect(self, outputs, databases=None):
        with patch.object(self.collector, 'text', side_effect=databases or [self.database, self.database]), \
                patch.object(self.collector, 'capture', side_effect=outputs) as capture:
            result = self.collector.collect_package_audit(self.sha)
        return result, capture

    def test_audit_includes_configuration_and_metadata_without_file_contents(self):
        result, calls = self.collect([{'rc': 0, 'stdout': 'U etc/hosts\nm var/lib/\n'}] * 2)
        self.assertTrue(result['complete']); self.assertTrue(result['stable'])
        self.assertFalse(result['adoption_authorized'])
        self.assertEqual(result['differences'], [{'code': 'U', 'path': '/etc/hosts'}, {'code': 'm', 'path': '/var/lib'}])
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(calls.call_args.args[0], ['/sbin/apk', 'audit', '--system', '--check-permissions', '--protected-paths', '/dev/null'])
        self.assertEqual(storage.checked_package_audit(result, self.sha), result)

    def test_empty_output_is_only_a_match_with_the_local_database(self):
        result, _ = self.collect([{'rc': 0, 'stdout': ''}] * 2)
        self.assertTrue(result['complete']); self.assertTrue(result['stable'])
        self.assertEqual(result['differences'], [])
        self.assertFalse(result['adoption_authorized'])
        storage.checked_package_audit(result, self.sha)

    def test_errors_timeouts_changed_database_or_unstable_rows_stay_unknown(self):
        cases = [([{'rc': 0, 'stdout': 'e etc/hosts\n'}] * 2, None),
                 ([{'rc': 124, 'stdout': ''}] * 2, None),
                 ([{'rc': 0, 'stdout': ''}] * 2, [self.database, 'different database']),
                 ([{'rc': 0, 'stdout': 'U etc/hosts\n'}, {'rc': 0, 'stdout': ''}], None),
                 ([{'rc': 0, 'stdout': 'unknown output\n'}] * 2, None)]
        for outputs, databases in cases:
            with self.subTest(outputs=outputs, databases=databases):
                result, _ = self.collect(outputs, databases)
                with self.assertRaises(storage.UpdateError): storage.checked_package_audit(result, self.sha)
        result, calls = self.collect([], ['different database'])
        self.assertFalse(result['complete']); calls.assert_not_called()

    def test_ambiguous_paths_details_duplicates_and_excessive_output_are_refused(self):
        for raw in ('U ../outside\n', 'U etc//hosts\n', 'U /etc/hosts\nM etc/hosts\n',
                    '- mode=600 uid=0\n', 'U etc/file\x00name\n', 'U ' + 'x' * (1024 * 1024)):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                self.collector.package_audit_rows(raw)

    def test_controller_rejects_forged_coverage_and_error_rows(self):
        value, _ = self.collect([{'rc': 0, 'stdout': 'm var/lib/\n'}] * 2)
        for key, replacement in (('complete', False), ('stable', False), ('adoption_authorized', True),
                                 ('database_sha256', 'f' * 64), ('protected_paths', 'default'),
                                 ('check_permissions', False), ('extra', True),
                                 ('differences', [{'code': 'e', 'path': '/etc/hosts'}]),
                                 ('differences', [{'code': 'U', 'path': '/etc/hosts'}] * 2)):
            bad = copy.deepcopy(value); bad[key] = replacement
            with self.subTest(key=key), self.assertRaises(storage.UpdateError):
                storage.checked_package_audit(bad, self.sha)


if __name__ == '__main__': unittest.main()
