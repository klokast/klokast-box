import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


CLIENT = Path(__file__).resolve().parents[1] / 'bin/torrent-client'
BASH = os.environ.get('KLOKAST_TEST_BASH', shutil.which('bash'))


class TorrentClientTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='torrent-client-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.log = self.root / 'tools.jsonl'
        self.env = os.environ.copy()
        self.env.update(PATH=str(self.root), KLOKAST_TEST_TOOL_LOG=str(self.log),
                        KLOKAST_TAILNET_SUFFIX='actual.ts.net')
        # A controlled PATH prevents any real network or browser operation.
        for name in ('tailscale', 'ssh', 'open'):
            self.install_tool(name)
        (self.root / 'cat').symlink_to(shutil.which('cat'))

    def install_tool(self, name):
        path = self.root / name
        path.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, sys
with open(os.environ['KLOKAST_TEST_TOOL_LOG'], 'a') as log:
    log.write(json.dumps({'tool': pathlib.Path(sys.argv[0]).name, 'args': sys.argv[1:]}) + '\\n')
sys.exit(int(os.environ.get('KLOKAST_TEST_OPEN_RC', '0')))
''')
        path.chmod(0o755)

    def run_client(self, *args, **environment):
        return subprocess.run([BASH, str(CLIENT), *args], env={**self.env, **environment},
                              capture_output=True, text=True, check=False)

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_status_only_prints_url_and_accepts_box_alias(self):
        for flag in ('--to', '--box'):
            result = self.run_client('status', flag, 'boxb')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'https://boxb-torrent.actual.ts.net\n')
            self.assertEqual(self.events(), [])

    def test_existing_default_suffix_is_preserved(self):
        self.env.pop('KLOKAST_TAILNET_SUFFIX', None)
        result = self.run_client('status', '--to', 'boxb')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'https://boxb-torrent.example.ts.net\n')

    def test_open_passes_only_url_to_local_browser(self):
        result = self.run_client('open', '--to', 'boxb')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'https://boxb-torrent.actual.ts.net\n')
        self.assertEqual(self.events(), [{'tool': 'open', 'args': ['https://boxb-torrent.actual.ts.net']}])

    def test_open_without_browser_still_prints_url(self):
        (self.root / 'open').unlink()
        result = self.run_client('open', '--to', 'boxb')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'https://boxb-torrent.actual.ts.net\n')
        self.assertEqual(self.events(), [])

    def test_browser_failure_preserves_current_url_output(self):
        result = self.run_client('open', '--to', 'boxb', KLOKAST_TEST_OPEN_RC='7')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'https://boxb-torrent.actual.ts.net\n')
        self.assertEqual(len(self.events()), 1)

    def test_invalid_arguments_never_call_tools(self):
        for action in ('status', 'open'):
            for args in ([], ['--to'], ['--box'], ['--to', 'boxb-bak'],
                         ['--to', 'invalid;target'], ['--unknown']):
                with self.subTest(action=action, args=args):
                    result = self.run_client(action, *args)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('torrent-client:', result.stderr)
                    self.assertEqual(self.events(), [])

    def test_help_is_local_and_describes_status(self):
        for args in (['--help'], ['status', '--help']):
            result = self.run_client(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('does not check runtime health', result.stdout)
            self.assertEqual(self.events(), [])


if __name__ == '__main__':
    unittest.main()
