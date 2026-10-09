"""Execute the fetch boundary with a fake Go tool; no network or credentials."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


FETCH = Path(__file__).resolve().parents[2] / 'tools/tailscale-distsign/fetch'


class GoModuleFetchTests(unittest.TestCase):
    def run_fetch(self, proxy, fail='', inherited=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            (source / 'go.sum').write_text('unchanged dependency checksums\n')
            fake = root / 'go'
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
keys = ('GOPROXY', 'GOSUMDB', 'GONOSUMDB', 'GONOPROXY', 'GOPRIVATE',
        'GOINSECURE', 'GOENV', 'GOFLAGS', 'GOTOOLCHAIN', 'GOWORK',
        'GOMODCACHE', 'GOPATH')
with open(os.environ['FETCH_TEST_LOG'], 'a') as log:
    log.write(json.dumps({'args': sys.argv[1:], 'cwd': str(Path.cwd()),
                         'env': {key: os.environ.get(key) for key in keys}}) + '\\n')
if sys.argv[-1] == os.environ['FETCH_TEST_FAIL']:
    raise SystemExit(23)
''')
            fake.chmod(0o700)
            log = root / 'calls.jsonl'
            env = {'PATH': os.environ['PATH'], 'FETCH_TEST_LOG': str(log),
                   'FETCH_TEST_FAIL': fail, **(inherited or {})}
            if proxy is not None:
                env['GOPROXY'] = proxy
            output = root / 'output'
            result = subprocess.run(['/bin/sh', str(FETCH), str(source), str(output), str(fake)],
                                    env=env, capture_output=True, text=True)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            self.assertEqual((source / 'go.sum').read_text(), 'unchanged dependency checksums\n')
            return result, calls, str(source), str(output)

    def test_both_sources_keep_checksum_verification_and_isolated_cache(self):
        for proxy in ('https://goproxy.cn', 'https://proxy.golang.org'):
            with self.subTest(proxy=proxy):
                result, calls, source, output = self.run_fetch(proxy, inherited={
                    'GOSUMDB': 'off', 'GONOSUMDB': '*', 'GONOPROXY': '*',
                    'GOPRIVATE': '*', 'GOINSECURE': '*', 'GOENV': '/untrusted',
                    'GOFLAGS': '-modfile=/untrusted', 'GOTOOLCHAIN': 'auto',
                    'GOWORK': '/untrusted', 'GOMODCACHE': '/untrusted'})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([c['args'] for c in calls], [['mod', 'download'], ['mod', 'verify']])
                for call in calls:
                    self.assertEqual(call['cwd'], source)
                    self.assertEqual(call['env'], {
                        'GOPROXY': proxy, 'GOSUMDB': 'sum.golang.org',
                        'GONOSUMDB': 'none', 'GONOPROXY': 'none', 'GOPRIVATE': '',
                        'GOINSECURE': '', 'GOENV': 'off', 'GOFLAGS': '',
                        'GOTOOLCHAIN': 'local', 'GOWORK': 'off',
                        'GOMODCACHE': output + '/path/pkg/mod', 'GOPATH': output + '/path'})

    def test_absent_or_unapproved_sources_stop_before_go(self):
        for proxy in (None, '', 'direct', 'http://goproxy.cn',
                      'https://goproxy.cn|direct', 'https://unapproved.example'):
            with self.subTest(proxy=proxy):
                result, calls, _, _ = self.run_fetch(proxy)
                self.assertEqual(result.returncode, 2)
                self.assertIn('Platform-selected', result.stderr)
                self.assertEqual(calls, [])

    def test_download_or_verification_failure_is_not_hidden(self):
        for failure, expected in (('download', 1), ('verify', 2)):
            with self.subTest(failure=failure):
                result, calls, _, _ = self.run_fetch('https://goproxy.cn', fail=failure)
                self.assertEqual(result.returncode, 23)
                self.assertEqual(len(calls), expected)


if __name__ == '__main__':
    unittest.main()
