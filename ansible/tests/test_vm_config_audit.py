"""Boundary checks for read-only shared-VM configuration comparisons."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace


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

    def test_old_firewall_accepts_only_exact_source_and_one_missing_underlay_permit(self):
        values = self.variables()
        values['podman_vm_firewall_input_udp_rules'] = [{
            'interface': 'eth0', 'source': '192.0.2.1', 'destination': '192.0.2.2',
            'port': 41641, 'comment': 'ops-dmz-tailscale-underlay-input'}]
        source = (REPO / audit.LEGACY_FIREWALL_SOURCE).read_bytes()
        self.assertEqual(audit.sha(source), audit.LEGACY_FIREWALL_SHA256)
        env, context = audit.render_context('k001-dmz', values)
        rendered = env.from_string(source.decode()).render(context).encode()
        full = audit.legacy_firewall_match(REPO, 'k001-dmz', values, rendered)
        self.assertTrue(full['match'])
        self.assertFalse(full['missing_underlay_permit'])
        lines = rendered.decode().splitlines(keepends=True)
        without = ''.join(line for line in lines if
                          'ops-dmz-tailscale-underlay-input' not in line).encode()
        result = audit.legacy_firewall_match(REPO, 'k001-dmz', values, without)
        self.assertTrue(result['match'])
        self.assertTrue(result['missing_underlay_permit'])
        for changed in (rendered.replace(b'policy drop', b'policy accept'),
                        rendered.replace(b'ct state invalid drop\n', b''),
                        rendered.replace(b'    chain input {', b'    chain input {\n        tcp dport 443 accept'),
                        without.replace(b'policy drop', b'policy accept')):
            self.assertFalse(audit.legacy_firewall_match(REPO, 'k001-dmz', values, changed)['match'])
        self.assertFalse(audit.legacy_firewall_match(REPO, 'k001-iot',
                                                     {**values, 'node_domain_role': 'iot',
                                                      'node_hostname': 'k001-iot'}, without)['match'])
        receipt = audit.legacy_firewall_report(REPO, 'k001-dmz', values, without,
                                               'c' * 64, 'a' * 40, False)
        self.assertEqual(receipt['authority'], 'comparison-only')
        self.assertFalse(receipt['approved_engine'])

    def test_guest_firewall_probe_rejects_invalid_target_and_failed_read(self):
        with self.assertRaises(audit.ConfigAuditError):
            audit.guest_firewall_bytes('k001-bak')
        with patch.object(audit.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout=b'', stderr=b'private diagnostic')):
            with self.assertRaises(audit.ConfigAuditError) as error:
                audit.guest_firewall_bytes('k001-dmz')
            self.assertNotIn('private diagnostic', str(error.exception))


if __name__ == '__main__':
    unittest.main()
