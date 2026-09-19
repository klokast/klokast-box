"""Boundary checks for read-only shared-VM configuration comparisons."""
from pathlib import Path
import sys
import unittest


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import vm_config_audit as audit


class ConfigAuditTests(unittest.TestCase):
    def variables(self):
        return {
            'node_name': 'k001', 'node_domain_role': 'dmz',
            'node_hostname': 'k001-dmz',
            'vm_bootstrap_network_interfaces': 'auto lo\niface lo inet loopback\n',
            'alpine_repositories': ['https://example.invalid/v3.23/main'],
            'podman_host_registries': [],
        }

    def test_fixed_recipes_are_complete_and_bound_to_sources(self):
        result = audit.render(REPO, 'k001-dmz', self.variables())
        self.assertEqual(set(result), set(audit.PATHS))
        self.assertEqual(result['/etc/hostname']['sha256'], audit.sha(b'k001-dmz\n'))
        for row in result.values():
            self.assertEqual(len(row['source_sha256']), 64)
            self.assertTrue((REPO / row['source']).is_file())

    def test_wrong_inventory_identity_is_rejected(self):
        values = self.variables()
        values['node_hostname'] = 'k002-dmz'
        with self.assertRaises(audit.ConfigAuditError):
            audit.render(REPO, 'k001-dmz', values)

    def test_guest_hash_response_requires_each_unique_fixed_path(self):
        rows = '\n'.join('a' * 64 + '  ' + path for path in audit.PATHS)
        self.assertEqual(len(audit.parse_guest_hashes(rows)), len(audit.PATHS))
        with self.assertRaises(audit.ConfigAuditError):
            audit.parse_guest_hashes(rows + '\n' + 'a' * 64 + '  ' + audit.PATHS[0])
        with self.assertRaises(audit.ConfigAuditError):
            audit.parse_guest_hashes(rows.replace(audit.PATHS[0], '/etc/shadow'))
        with self.assertRaises(audit.ConfigAuditError):
            audit.parse_guest_hashes(rows.split('\n', 1)[1])

    def test_report_marks_difference_and_never_calls_it_approval(self):
        expected = audit.render(REPO, 'k001-dmz', self.variables())
        observed = {path: row['sha256'] for path, row in expected.items()}
        observed['/etc/nftables.nft'] = 'b' * 64
        result = audit.report('k001-dmz', 'a' * 40, 'c' * 64,
                              expected, observed, False)
        self.assertEqual(result['authority'], 'comparison-only')
        self.assertFalse(result['approved_engine'])
        self.assertEqual([row['path'] for row in result['rows'] if not row['match']],
                         ['/etc/nftables.nft'])
        self.assertEqual(len(result['report_sha256']), 64)


if __name__ == '__main__':
    unittest.main()
