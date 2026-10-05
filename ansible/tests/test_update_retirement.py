"""Retired launch commands must fail before controller or guest access."""
import contextlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(relative):
    loader = importlib.machinery.SourceFileLoader('retirement_' + Path(relative).name, str(ROOT / relative))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RetirementTests(unittest.TestCase):
    def test_controller_update_commands_are_removed(self):
        for tool, commands in {
            'platform-update': ('daily', 'replace', 'adopt', 'resume', 'policy'),
            'platform-router-update': ('scheduled', 'daily-prepare', 'daily-cutover',
                'prepare-replacement', 'run-replacement-cutover', 'adopt-legacy'),
            'platform-maintenance': ('policy', 'release', 'adopt'),
        }.items():
            for command in commands:
                with self.subTest(tool=tool, command=command):
                    result = subprocess.run([sys.executable, str(ROOT / 'ansible/bin' / tool), command],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('invalid choice', result.stderr)

    def test_installed_readers_reject_new_transactions_before_host_access(self):
        for path, commands in {
            'ansible/roles/router-update-recovery/files/router-update-transaction':
                ('run', 'stage-cutover', 'accept', 'adopt-baseline'),
            'ansible/roles/vm-update-recovery/files/vm-update-transaction':
                ('arm', 'start', 'accept', 'adopt-source'),
        }.items():
            module = load(path)
            for command in commands:
                with self.subTest(path=path, command=command), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        module.main([command])
                    self.assertEqual(caught.exception.code, 2)

    def test_first_install_version_source_has_no_update_authority(self):
        router = load('ansible/bin/platform-router-update')
        with patch.object(router.transport, 'command', side_effect=AssertionError('unexpected controller operation')):
            source = router.schedule_source()
            _, authority, policy, _ = router.check_policy_at('boxa', 'a' * 40)
        self.assertFalse(source['enabled'])
        self.assertFalse(source['replacement_ready'])
        self.assertIsNone(authority)
        self.assertFalse(policy['enabled'])
        self.assertEqual(policy['branch-delay-days'], 21)
        self.assertTrue(callable(router.provision_initial_phase))
        self.assertTrue(callable(router.test_state_copy))


if __name__ == '__main__':
    unittest.main()
