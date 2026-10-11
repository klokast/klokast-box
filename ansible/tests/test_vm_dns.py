"""Keep Tailscale DNS intent on success and on an OpenResolv update failure."""
import contextlib
import io
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import yaml


class DNSPreferenceRecovery(unittest.TestCase):
    def invoke(self, accepted, fail_hook=False):
        tasks = yaml.safe_load((Path(__file__).resolve().parents[1] / 'roles/vm-dns/tasks/main.yml').read_text())
        code = next(t['ansible.builtin.command']['argv'][-1] for t in tasks
                    if t['name'] == 'Apply the declared DNS source and bypass filter')
        calls = []
        output = io.StringIO()
        def command(argv, **kwargs):
            calls.append(argv)
            if argv == ['tailscale', 'debug', 'prefs']:
                import json
                return SimpleNamespace(returncode=0, stdout=json.dumps({'CorpDNS': accepted, 'Persist': 'private-fixture'}))
            return SimpleNamespace(returncode=int(fail_hook and argv[0].startswith('/etc/network/')), stdout='')
        with patch('subprocess.run', side_effect=command), patch.dict(os.environ), contextlib.redirect_stdout(output):
            if fail_hook:
                with self.assertRaisesRegex(RuntimeError, 'DNS convergence command failed'):
                    exec(code, {})
            else:
                exec(code, {})
        self.assertNotIn('private-fixture', output.getvalue())
        return calls

    def test_enabled_dns_is_restored_when_the_hook_fails(self):
        calls = self.invoke(True, fail_hook=True)
        self.assertEqual(calls[-1], ['tailscale', 'set', '--accept-dns=true'])
        self.assertEqual(calls[1], ['tailscale', 'set', '--accept-dns=false'])

    def test_disabled_dns_remains_disabled(self):
        calls = self.invoke(False)
        self.assertFalse(any(argv[:2] == ['tailscale', 'set'] for argv in calls))

    def test_enabled_dns_is_restored_after_success(self):
        self.assertEqual(self.invoke(True)[-1], ['tailscale', 'set', '--accept-dns=true'])


if __name__ == '__main__':
    unittest.main()
