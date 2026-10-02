"""The supervised fault preserves existing leases and adds only an expired fixture."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1]/'roles/router-state-copy/files/router-rollback-probe'
loader = importlib.machinery.SourceFileLoader('rollback_probe', str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
probe = importlib.util.module_from_spec(spec)
loader.exec_module(probe)


class RollbackProbeTests(unittest.TestCase):
    def setUp(self):
        self.operation = 'ab'*12
        self.ranges = [{'start': '192.0.2.10', 'end': '192.0.2.12'}]
        self.leases = b'1999999999 02:00:00:00:00:01 192.0.2.12 existing *\n'

    def test_existing_leases_are_unchanged_and_fixture_is_expired_and_distinct(self):
        changed, fixture = probe.changed_leases(self.leases, self.ranges, self.operation)
        self.assertEqual(changed, self.leases+fixture)
        expiry, mac, address, hostname, client = fixture.decode().split()
        self.assertEqual(expiry, '1')
        self.assertEqual(address, '192.0.2.11')
        self.assertEqual(mac, '02:ab:ab:ab:ab:ab')
        self.assertEqual(hostname, 'rollback-'+self.operation)
        self.assertEqual(client, '*')

    def test_retry_refuses_to_add_another_fixture(self):
        changed, _ = probe.changed_leases(self.leases, self.ranges, self.operation)
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            probe.changed_leases(changed, self.ranges, self.operation)

    def test_full_pool_never_overwrites_a_real_lease(self):
        with self.assertRaisesRegex(RuntimeError, 'no unused address'):
            probe.changed_leases(self.leases, [{'start': '192.0.2.12', 'end': '192.0.2.12'}], self.operation)

    def test_partial_unsafe_or_oversized_state_is_refused(self):
        for leases in (self.leases.rstrip(), b'bad\x00\n', b'x'*4*1024*1024):
            with self.subTest(leases_size=len(leases)), self.assertRaises(RuntimeError):
                probe.changed_leases(leases, self.ranges, self.operation)
        for ranges in ([], [{'start': '192.0.2.12', 'end': '192.0.2.10'}],
                       [{'start': '10.0.0.1', 'end': '10.255.255.254'}]):
            with self.subTest(ranges=ranges), self.assertRaises(RuntimeError):
                probe.changed_leases(self.leases, ranges, self.operation)


if __name__ == '__main__':
    unittest.main()
