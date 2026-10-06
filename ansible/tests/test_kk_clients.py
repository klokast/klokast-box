"""Exercise the MacBook dispatcher without private state or network access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ClientDispatchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='kk-clients-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'checkout with spaces'
        self.kk = self.repo / 'klokast-dev/bin/kk'
        self.kk.parent.mkdir(parents=True)
        shutil.copy2(ROOT / 'klokast-dev/bin/kk', self.kk)
        self.instance = self.root / 'private instance'
        self.instance.mkdir()
        self.value = {'schema-version': 1,
                      'tailscale': {'tailnet-dns-name': 'fixture.ts.net'},
                      'apps': {'sample-tool': {'desired-state': 'present'}}}
        self.write_instance()
        self.marker = self.root / 'client.json'
        self.environment = os.environ.copy()
        self.environment.pop('KLOKAST_INSTANCE', None)
        self.environment['KLOKAST_TEST_MARKER'] = str(self.marker)
        self.environment['KLOKAST_TAILNET_SUFFIX'] = 'wrong.ts.net'
        self.client('sample-tool')

    def write_instance(self, root=None):
        root = root or self.instance
        (root / 'klokast-instance.json').write_text(json.dumps(self.value))

    def client(self, app):
        client = self.repo / 'apps' / app / 'bin' / (app + '-client')
        client.parent.mkdir(parents=True, exist_ok=True)
        client.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, sys
value = {"argv": sys.argv[1:], "input": sys.stdin.read(),
         "instance": os.environ["KLOKAST_INSTANCE"],
         "suffix": os.environ["KLOKAST_TAILNET_SUFFIX"]}
pathlib.Path(os.environ["KLOKAST_TEST_MARKER"]).write_text(json.dumps(value))
print("client stdout")
print("client stderr", file=sys.stderr)
sys.exit(int(os.environ.get("KLOKAST_TEST_EXIT", "0")))
''')
        client.chmod(0o755)
        return client

    def run_kk(self, *args, select=True, input=''):
        prefix = ['--instance', str(self.instance)] if select else []
        return subprocess.run([str(self.kk), *prefix, *args], env=self.environment,
                              cwd=self.root, input=input, capture_output=True,
                              text=True, check=False)

    def rejected(self, result, message):
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_generic_dispatch_preserves_streams_arguments_and_context(self):
        arguments = ['command', 'two words', '', '$(touch injected)', '; exit 9',
                     '--instance', 'client option', '--help']
        result = self.run_kk('sample-tool', *arguments, input='input to application\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'client stdout\n')
        self.assertEqual(result.stderr, 'client stderr\n')
        recorded = json.loads(self.marker.read_text())
        self.assertEqual(recorded['argv'], arguments)
        self.assertEqual(recorded['input'], 'input to application\n')
        self.assertEqual(recorded['instance'], str(self.instance))
        self.assertEqual(recorded['suffix'], 'fixture.ts.net')
        self.assertFalse((self.root / 'injected').exists())

    def test_application_exit_status_is_preserved(self):
        self.environment['KLOKAST_TEST_EXIT'] = '37'
        self.assertEqual(self.run_kk('sample-tool', 'fail').returncode, 37)

    def test_environment_selection_and_explicit_precedence(self):
        self.environment['KLOKAST_INSTANCE'] = str(self.instance)
        self.assertEqual(self.run_kk('sample-tool', 'command', select=False).returncode, 0)
        self.environment['KLOKAST_INSTANCE'] = str(self.root / 'does-not-exist')
        self.assertEqual(self.run_kk('sample-tool', 'command').returncode, 0)

    def test_no_command_and_help_are_owned_by_client(self):
        for arguments in ([], ['--help']):
            with self.subTest(arguments=arguments):
                result = self.run_kk('sample-tool', *arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(self.marker.read_text())['argv'], arguments)

    def test_wrapper_help_and_missing_arguments(self):
        self.assertEqual(self.run_kk('--help', select=False).returncode, 0)
        self.assertEqual(self.run_kk(select=False).returncode, 2)
        self.rejected(self.run_kk('--instance', select=False), 'needs a worktree path')
        self.rejected(self.run_kk('--bogus', select=False), 'unknown wrapper option')
        self.rejected(self.run_kk('sample-tool', select=False), 'select a private Instance')

    def test_undeclared_and_absent_apps_do_not_execute(self):
        self.rejected(self.run_kk('other', 'command'), 'not declared in Instance')
        self.value['apps']['sample-tool']['desired-state'] = 'absent'
        self.write_instance()
        self.rejected(self.run_kk('sample-tool', 'command'), 'declared absent')

    def test_invalid_application_names_do_not_execute(self):
        for name in ('../sample-tool', '/bin/sh', 'sample/tool', 'Sample', '.',
                     'x;touch injected', 'a' * 64):
            with self.subTest(name=name):
                self.rejected(self.run_kk(name, 'command'), 'invalid application name')

    def test_missing_and_nonexecutable_client(self):
        self.value['apps']['nextcloud'] = {'desired-state': 'present'}
        self.write_instance()
        self.rejected(self.run_kk('nextcloud', 'command'), 'client interface unavailable')
        self.client('sample-tool').chmod(0o644)
        self.rejected(self.run_kk('sample-tool', 'command'), 'not executable')

    def test_client_cannot_resolve_outside_application(self):
        client = self.repo / 'apps/sample-tool/bin/sample-tool-client'
        outside = self.root / 'outside-client'
        client.rename(outside)
        client.symlink_to(outside)
        self.rejected(self.run_kk('sample-tool', 'command'), 'inside its application directory')

    def test_instance_read_and_parse_errors(self):
        path = self.instance / 'klokast-instance.json'
        for content in ('{', '{"apps": {}, "apps": {}}', '{"value": NaN}',
                        '[]', '{"schema-version": true}', '{"schema-version": 2}'):
            with self.subTest(content=content):
                path.write_text(content)
                self.rejected(self.run_kk('sample-tool', 'command'), 'kk:')
        path.unlink()
        self.rejected(self.run_kk('sample-tool', 'command'), 'cannot read Instance')

    def test_invalid_dispatch_fields(self):
        for change in (lambda value: value.update(apps=[]),
                       lambda value: value['apps'].update({'sample-tool': []}),
                       lambda value: value['apps']['sample-tool'].update({'desired-state': 'other'}),
                       lambda value: value['apps'].update({'../unsafe': {'desired-state': 'present'}}),
                       lambda value: value.update(tailscale=None),
                       lambda value: value['tailscale'].update({'tailnet-dns-name': 'not-a-tailnet'})):
            with self.subTest(change=change):
                original = json.loads(json.dumps(self.value))
                change(self.value)
                self.write_instance()
                self.rejected(self.run_kk('sample-tool', 'command'), 'kk:')
                self.value = original

    def test_existing_torrent_client_uses_instance_dns(self):
        self.value['apps']['torrent'] = {'desired-state': 'present'}
        self.write_instance()
        client = self.client('torrent')
        shutil.copy2(ROOT / 'apps/torrent/bin/torrent-client', client)
        result = self.run_kk('torrent', 'status', '--to', 'boxb')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'https://boxb-torrent.fixture.ts.net\n')


if __name__ == '__main__':
    unittest.main()
