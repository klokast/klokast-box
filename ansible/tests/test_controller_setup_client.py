"""The MacBook controller setup must relay secrets only through the existing installers."""
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'klokast-dev/lib'))
import controller_setup as setup

KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixture boxa-ops'


def report(**states):
    items = [{'id': name, 'required': True, 'state': states.get(name, 'ok'), 'detail': 'fixture',
              'public_key': KEY} for name in ('tailscale-policy', 'tailscale-devices', 'instance-read',
                                              'instance-write', 'github-metadata')]
    return {'kind': 'klokast.controller-credentials.v1', 'hostname': 'boxa-ops', 'role': 'standby',
            'instance_repository': 'family/klokast-instance', 'items': items,
            'ready': all(i['state'] == 'ok' for i in items)}


class ControllerSetupClient(unittest.TestCase):
    def remote(self, value, code=3):
        return mock.patch.object(setup, 'ssh', return_value=subprocess.CompletedProcess(
            [], code, json.dumps(value), ''))

    def test_check_reports_without_prompts(self):
        with self.remote(report(**{'tailscale-policy': 'absent'})), \
                mock.patch.object(setup, 'ask', side_effect=AssertionError('prompted')), \
                mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(setup.main(['boxa-ops', '--check']), 3)
        self.assertIn('MISSING', output.getvalue())
        self.assertIn('Not ready', output.getvalue())

    def test_report_for_another_controller_is_refused(self):
        with self.remote(dict(report(), hostname='boxb-ops')), mock.patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(setup.main(['boxa-ops', '--check']), 1)

    def test_tailscale_files_are_private_and_removed(self):
        seen = {}

        def installer(argv, **kwargs):
            for name in ('--policy-env', '--devices-env'):
                path = Path(argv[argv.index(name) + 1])
                seen[name] = (stat.S_IMODE(path.stat().st_mode), path.read_text(), path)
            return subprocess.CompletedProcess(argv, 0)
        answers = iter(['policy-id', 'devices-id'])
        with mock.patch.object(setup, 'offer'), \
                mock.patch('builtins.input', side_effect=lambda prompt: next(answers)), \
                mock.patch.object(setup.getpass, 'getpass', side_effect=['policy-secret', 'devices-secret']), \
                mock.patch.object(setup.subprocess, 'run', side_effect=installer) as run, \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            setup.tailscale('boxa-ops')
        argv = run.call_args.args[0]
        self.assertTrue(argv[0].endswith('klokast-dev/bin/install-tailscale-oauth'))
        self.assertEqual(argv[1:3], ['--controller', 'boxa-ops'])
        mode, content, path = seen['--policy-env']
        self.assertEqual(mode, 0o600)
        self.assertEqual('TAILNET_ID="-"\nTS_OAUTH_CLIENT_ID="policy-id"\nTS_OAUTH_CLIENT_SECRET="policy-secret"\n', content)
        self.assertIn('devices-secret', seen['--devices-env'][1])
        self.assertFalse(path.exists())
        self.assertNotIn('secret', ' '.join(argv))

    def test_identical_clients_are_refused(self):
        answers = iter(['same-id', 'same-id'])
        with mock.patch.object(setup, 'offer'), \
                mock.patch('builtins.input', side_effect=lambda prompt: next(answers)), \
                mock.patch.object(setup.getpass, 'getpass', return_value='secret'), \
                mock.patch.object(setup.subprocess, 'run') as run, \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaises(setup.SetupError):
                setup.tailscale('boxa-ops')
        run.assert_not_called()

    def test_unsafe_prompt_values_are_asked_again(self):
        answers = iter(['bad value', 'a"b', 'good'])
        with mock.patch('builtins.input', side_effect=lambda prompt: next(answers)), \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(setup.ask('value: '), 'good')

    def test_read_key_becomes_a_read_only_deploy_key(self):
        entry = report(**{'instance-read': 'failed'})['items'][2]
        with mock.patch.object(setup, 'gh_login', return_value='klokast-mac'), \
                mock.patch.object(setup, 'confirm', return_value=True), \
                mock.patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run, \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            setup.register(entry, 'boxa-ops', 'family/klokast-instance')
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ['gh', 'repo', 'deploy-key', 'add'])
        self.assertIn('family/klokast-instance', argv)
        self.assertNotIn('--allow-write', argv)

    def test_invalid_public_key_is_refused(self):
        entry = dict(report()['items'][3], public_key='ssh-ed25519 AAAA\nextra')
        with mock.patch('sys.stdout', new_callable=io.StringIO), self.assertRaises(setup.SetupError):
            setup.register(entry, 'boxa-ops', 'family/klokast-instance')

    def test_kk_refuses_a_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / 'uname'
            tool.write_text('#!/bin/sh\necho Linux\n')
            tool.chmod(0o755)
            result = subprocess.run([str(ROOT / 'klokast-dev/bin/kk'), 'setup', '--controller', 'boxa-ops'],
                                    env=dict(os.environ, PATH=directory + ':' + os.environ['PATH']),
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('operator MacBook', result.stderr)


if __name__ == '__main__':
    unittest.main()
