"""Synthetic service qualification must never run on a production guest."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_service_probe as probe


class GuardTests(unittest.TestCase):
    def fixture(self):
        return {'/sys/hypervisor/type': 'xen',
                '/sys/hypervisor/uuid': '11111111-1111-4111-8111-111111111111',
                '/etc/hostname': 'boxa-router\n',
                '/proc/cmdline': 'klokast_router_personalize=1 klokast_router_test=1'}

    def check(self, values, interfaces=('lo',), identity=False):
        with patch.object(probe.os, 'geteuid', return_value=0), \
                patch.object(Path, 'read_text', lambda p: values[str(p)]), \
                patch.object(Path, 'iterdir', return_value=iter(map(Path, interfaces))), \
                patch.object(Path, 'exists', return_value=identity), \
                patch.object(Path, 'is_symlink', return_value=False):
            probe.guard()

    def test_only_empty_networkless_fixture_passes(self):
        self.check(self.fixture())
        for interfaces, identity in ((('lo', 'eth0'), False), (('lo',), True)):
            with self.assertRaises(RuntimeError):
                self.check(self.fixture(), interfaces, identity)

    def test_dom0_normal_boot_and_production_hostname_are_refused(self):
        for key, value in (('/sys/hypervisor/uuid', '00000000-0000-0000-0000-000000000000'),
                           ('/etc/hostname', 'k001-router\n'), ('/proc/cmdline', 'console=hvc0')):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.check({**self.fixture(), key: value})


if __name__ == '__main__':
    unittest.main()
