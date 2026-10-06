import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


CLIENT = Path(__file__).resolve().parents[1] / 'bin/music-client'
BASH = os.environ.get('KLOKAST_TEST_BASH', shutil.which('bash'))

# All external effects are replaced by tools that record arguments only.
TOOL = '''import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['KLOKAST_TEST_TOOL_LOG'], 'a') as log:
    log.write(json.dumps({'tool': name, 'args': args}) + '\\n')
if name == 'tailscale':
    if args[0] == 'status':
        print(os.environ.get('KLOKAST_TEST_TAILSCALE_STATUS', ''))
    elif args[0] == 'ping':
        sys.exit(int(os.environ.get('KLOKAST_TEST_PING_RC', '1')))
elif name == 'ssh':
    if args[-1] == 'true':
        allowed = os.environ.get('KLOKAST_TEST_REACHABLE', '').split(',')
        if args[-2] not in allowed:
            print('test ingress unreachable', file=sys.stderr)
            sys.exit(1)
    elif args[-1] == 'sudo -n /sbin/poweroff':
        sys.exit(int(os.environ.get('KLOKAST_TEST_POWEROFF_RC', '0')))
    else:
        sys.exit(int(os.environ.get('KLOKAST_TEST_INDEX_RC', '0')))
elif name == 'rsync':
    if '--version' in args:
        sys.exit(0 if os.environ.get('KLOKAST_TEST_MODERN_RSYNC', '1') == '1' else 1)
    sys.exit(int(os.environ.get('KLOKAST_TEST_TRANSFER_RC', '0')))
'''


class MusicClientTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='music-client-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tools = self.root / 'bin'
        self.tools.mkdir()
        self.log = self.root / 'tools.jsonl'
        for name in ('tailscale', 'ssh', 'rsync', 'sleep'):
            path = self.tools / name
            path.write_text(f'#!{sys.executable}\n' + TOOL)
            path.chmod(0o755)
        self.source = self.root / 'Music files'
        self.source.mkdir()
        (self.source / 'track.flac').write_text('fixture')
        self.env = os.environ.copy()
        self.env.update(PATH=f'{self.tools}:{self.env["PATH"]}',
                        KLOKAST_TEST_TOOL_LOG=str(self.log),
                        KLOKAST_TAILNET_SUFFIX='actual.ts.net',
                        KLOKAST_TEST_REACHABLE='music@100.64.0.8',
                        KLOKAST_TEST_TAILSCALE_STATUS='100.64.0.8 boxb-music-upload user linux active')

    def run_client(self, *args, **environment):
        return subprocess.run([BASH, str(CLIENT), *map(str, args)],
                              env={**self.env, **environment}, capture_output=True,
                              text=True, check=False)

    def events(self, tool=None):
        values = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [v for v in values if tool is None or v['tool'] == tool]

    def upload(self, **environment):
        return self.run_client('upload', '--from', self.source, '--to', 'boxb', **environment)

    def test_upload_keeps_paths_transport_and_index_order(self):
        result = self.upload()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('https://boxb-music.actual.ts.net', result.stdout)
        transfer = self.events('rsync')[-1]['args']
        self.assertEqual(transfer, ['-a', '--human-readable', '--info=progress2', '-e',
                                   'ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new',
                                   str(self.source) + '/', 'music@100.64.0.8:/music/library/'])
        ssh = self.events('ssh')
        self.assertEqual(ssh[0]['args'][-2:], ['music@100.64.0.8', 'true'])
        self.assertEqual(ssh[1]['args'][-2:], ['music@100.64.0.8',
                        'printf "update\\nclose\\n" | nc 127.0.0.1 6600 >/dev/null'])
        self.assertEqual([v['tool'] for v in self.events()], ['tailscale', 'ssh', 'rsync', 'rsync', 'ssh'])
        self.assertEqual((self.source / 'track.flac').read_text(), 'fixture')

    def test_discovery_skips_offline_and_duplicate_targets(self):
        result = self.upload(KLOKAST_TEST_TAILSCALE_STATUS='\n'.join([
            '100.64.0.9 boxb-music-upload user linux offline',
            '100.64.0.10 boxb-music-upload user linux active',
            '100.64.0.10 boxb-music-upload-1 user linux active',
            '100.64.0.8 boxb-music-upload-2 user linux active',
            '100.64.0.11 boxa-music-upload user linux active']))
        self.assertEqual(result.returncode, 0, result.stderr)
        probes = [v['args'][-2] for v in self.events('ssh') if v['args'][-1] == 'true']
        self.assertEqual(probes, ['music@100.64.0.10', 'music@100.64.0.8'])

    def test_dns_fallback_and_older_rsync_progress(self):
        result = self.upload(KLOKAST_TEST_TAILSCALE_STATUS='',
                             KLOKAST_TEST_REACHABLE='music@boxb-music-upload',
                             KLOKAST_TEST_MODERN_RSYNC='0')
        self.assertEqual(result.returncode, 0, result.stderr)
        transfer = self.events('rsync')[-1]['args']
        self.assertIn('--progress', transfer)
        self.assertNotIn('--info=progress2', transfer)
        self.assertEqual(transfer[-1], 'music@boxb-music-upload:/music/library/')

    def test_unreachable_ingress_never_transfers_or_indexes(self):
        result = self.upload(KLOKAST_TEST_REACHABLE='')
        self.assertEqual(result.returncode, 2)
        self.assertIn('music-client: cannot reach music upload ingress', result.stderr)
        self.assertEqual(self.events('rsync'), [])
        self.assertTrue(all(v['args'][-1] == 'true' for v in self.events('ssh')))

    def test_transfer_failure_never_indexes(self):
        result = self.upload(KLOKAST_TEST_TRANSFER_RC='23')
        self.assertEqual(result.returncode, 23)
        self.assertTrue(all(v['args'][-1] == 'true' for v in self.events('ssh')))

    def test_index_failure_is_visible(self):
        result = self.upload(KLOKAST_TEST_INDEX_RC='7')
        self.assertEqual(result.returncode, 7)
        self.assertNotIn('Upload complete', result.stdout)

    def test_invalid_upload_inputs_do_not_call_tools(self):
        for args in ([], ['--to', 'boxb'], ['--from', str(self.source)],
                     ['--from', str(self.source), '--to', 'boxb-bak'],
                     ['--from', str(self.root / 'missing'), '--to', 'boxb'],
                     ['--unknown'], ['--from'], ['--to']):
            with self.subTest(args=args):
                result = self.run_client('upload', *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn('music-client:', result.stderr)
                self.assertEqual(self.events(), [])

    def test_poweroff_uses_existing_account_command_and_check(self):
        for ssh_rc in ('0', '255'):
            with self.subTest(ssh_rc=ssh_rc):
                self.log.unlink(missing_ok=True)
                result = self.run_client('poweroff', 'boxb-streamer', KLOKAST_TEST_POWEROFF_RC=ssh_rc)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.events('ssh')[0]['args'],
                                 ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                                  '-o', 'StrictHostKeyChecking=accept-new',
                                  'streamer-power@boxb-streamer.actual.ts.net', 'sudo -n /sbin/poweroff'])
                self.assertEqual(self.events('sleep')[0]['args'], ['20'])
                self.assertEqual(self.events('tailscale')[0]['args'],
                                 ['ping', '--c', '1', '--timeout=5s', 'boxb-streamer.actual.ts.net'])

    def test_poweroff_ssh_failure_stops_before_wait(self):
        result = self.run_client('poweroff', 'boxb-streamer', KLOKAST_TEST_POWEROFF_RC='5')
        self.assertEqual(result.returncode, 2)
        self.assertIn('ssh rc 5', result.stderr)
        self.assertEqual(self.events('sleep'), [])
        self.assertEqual(self.events('tailscale'), [])

    def test_poweroff_detects_endpoint_still_online(self):
        result = self.run_client('poweroff', 'boxb-streamer', KLOKAST_TEST_PING_RC='0')
        self.assertEqual(result.returncode, 2)
        self.assertIn('still answers on Tailscale', result.stderr)

    def test_poweroff_rejects_wrong_target_before_tools(self):
        for args in ([], ['boxb-bak'], ['boxb-bak-streamer'], ['boxb-streamer', 'extra']):
            with self.subTest(args=args):
                self.assertEqual(self.run_client('poweroff', *args).returncode, 2)
                self.assertEqual(self.events(), [])

    def test_help_is_local(self):
        for args in (['--help'], ['upload', '--help'], ['poweroff', '--help']):
            result = self.run_client(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('music-client upload', result.stdout)
            self.assertEqual(self.events(), [])


if __name__ == '__main__':
    unittest.main()
