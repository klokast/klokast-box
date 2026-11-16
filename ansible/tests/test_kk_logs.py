"""kk logs asks each controller for its own files and the active one for the other machines."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'klokast-dev/lib'))
import logs_client as logs

TOTALS = {'files': 3, 'bytes': 10, 'expire': 1, 'expire_bytes': 4, 'unregistered': 0, 'large': 0}


def machine(name, **extra):
    return dict({'machine': name, 'state': 'reachable', 'totals': dict(TOTALS), 'roots': [], 'unregistered': [],
                 'large': [], 'days': 14, 'runs': 10}, **extra)


class KkLogsTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def remote(user, host, argv, **kwargs):
            self.calls.append((host, argv))
            if argv[0].endswith('klokast-controller-guard'):
                return {'active': host == 'boxa-ops', 'role': 'active' if host == 'boxa-ops' else 'standby', 'hostname': host}
            scope = argv[argv.index('--scope') + 1]
            return {'log': '/run.log', 'machines': [machine(host)] if scope == 'local' else [machine('boxa-dom0')]}
        for target, name, value in ((logs.client, 'remote', remote),
                                    (logs.client, 'declared_controllers', lambda instance: ['boxa', 'boxb'])):
            mock = patch.object(target, name, side_effect=value); mock.start(); self.addCleanup(mock.stop)

    def run_kk(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = logs.main(['--instance', '/fixture', *argv])
        return code, output.getvalue()

    def test_report_covers_both_controllers_and_the_active_platform_scope(self):
        code, text = self.run_kk()
        self.assertEqual(code, 0, text)
        scans = [(host, argv[argv.index('--scope') + 1]) for host, argv in self.calls if 'scan' in argv]
        self.assertEqual(scans, [('boxa-ops', 'local'), ('boxb-ops', 'local'), ('boxa-ops', 'platform')])
        for name in ('boxa-ops', 'boxb-ops', 'boxa-dom0', 'Operations journal'): self.assertIn(name, text)

    def test_clean_plans_unless_apply_is_given(self):
        code, text = self.run_kk('clean')
        self.assertIn('Nothing was deleted', text)
        self.assertFalse(any('--apply' in argv for _, argv in self.calls))
        self.calls.clear(); self.run_kk('clean', '--apply')
        self.assertEqual(sum('--apply' in argv for _, argv in self.calls), 3)

    def test_machines_route_to_their_controller(self):
        self.assertEqual(logs.route('/fixture', 'boxb-ops'), ('boxb', 'local'))
        self.assertEqual(logs.route('/fixture', 'boxb-dom0'), ('boxa', 'platform'))
        with self.assertRaises(ValueError): logs.route('/fixture', 'boxc-ops')

    def test_contents_never_reach_a_machine_other_than_the_macbook(self):
        with patch.object(logs.platform, 'system', return_value='Linux'), tempfile.TemporaryDirectory() as directory:
            code, text = self.run_kk('get', 'boxa-dom0', '/var/log/messages', '--to', directory)
            self.assertEqual(code, 2); self.assertIn('only on the operator MacBook', text)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_journal_shows_entries_and_expiring_other_files(self):
        journal = '/var/lib/klokast/operations-journal'
        roots = [{'root': journal, 'rule': 'audit', 'files': 3, 'bytes': 30, 'expire': 0, 'oldest': 1, 'newest': 2,
                  'children': {'cases': 2, 'forum': 1}},
                 {'root': journal, 'rule': 'age', 'files': 5, 'bytes': 50, 'expire': 4, 'oldest': 1, 'newest': 2}]
        with patch.object(logs, 'collect', return_value=([machine('boxa-ops', roots=roots)], 'boxa', [], [])):
            code, text = self.run_kk()
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r'boxa-ops\s+active\s+2\s+1\s+30 B .* 5 \(50 B\), 4 expired')

    def test_unregistered_files_need_attention(self):
        with patch.object(logs, 'collect', return_value=([machine('boxa-ops', unregistered=[{'path': '/srv/x.log', 'bytes': 1}],
                                                                   totals=dict(TOTALS, unregistered=1))], 'boxa', [], [])):
            code, text = self.run_kk()
        self.assertEqual(code, 1); self.assertIn('unregistered /srv/x.log', text)


if __name__ == '__main__': unittest.main()
