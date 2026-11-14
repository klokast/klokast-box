"""The controller credential report must name each missing item and never carry a secret."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import controller_credentials as credentials

KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixture boxa-ops'
SAFE = {'present': True, 'safe': True, 'client_id_sha256': 'a' * 64}


class Fake:
    """Answer each controller command; record the commands for inspection."""

    def __init__(self, files, failing=()):
        self.files, self.failing, self.calls = files, set(failing), []

    def __call__(self, argv, *, stdin=None, timeout=60, env=None):
        self.calls.append((argv, env))
        text = ' '.join(argv)
        def done(code=0, out=''):
            return subprocess.CompletedProcess(argv, code, out, 'refused by fixture' if code else '')
        if any(word in text for word in self.failing):
            return done(1)
        if argv[:3] == ['doas', '-n', 'python3']:
            return done(out=json.dumps(self.files))
        if '--status' in argv:
            return done(out=json.dumps({'role': 'standby'}))
        if argv[-2:] == ['get-url', 'origin']:
            return done(out='ssh://git@ssh.github.com:443/family/klokast-instance.git\n')
        if argv[-2:] == ['--get', 'core.sshCommand']:
            return done(out='ssh -i ' + str(credentials.WRITE_KEY) + '\n')
        if argv[0] == 'ssh-keygen':
            return done(out=KEY + '\n')
        if 'github-metadata' in argv:
            return done(out='{"configured": true, "sources": ["codex-stable"]}')
        return done()


class ControllerCredentialReport(unittest.TestCase):
    def report(self, fake):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        read, write = Path(directory.name) / 'read', Path(directory.name) / 'write'
        read.write_text('private'); write.write_text('private')
        with mock.patch.object(credentials, 'run', fake), \
                mock.patch.object(credentials, 'READ_KEY', read), \
                mock.patch.object(credentials, 'WRITE_KEY', write), \
                mock.patch.object(credentials, 'FREEBOX_BROKER', Path(directory.name) / 'absent'), \
                mock.patch.object(credentials.socket, 'gethostname', return_value='boxa-ops'):
            return credentials.status()

    def states(self, value):
        return {entry['id']: entry['state'] for entry in value['items']}

    def test_complete_controller_is_ready(self):
        value = self.report(Fake({credentials.POLICY: SAFE, credentials.DEVICES: SAFE}))
        self.assertTrue(value['ready'], value)
        self.assertEqual(value['instance_repository'], 'family/klokast-instance')
        self.assertEqual(set(self.states(value).values()), {'ok'})
        self.assertNotIn('freebox', self.states(value))

    def test_absent_tailscale_clients_block_the_switch(self):
        value = self.report(Fake({credentials.POLICY: {'present': False}, credentials.DEVICES: {'present': False}}))
        self.assertFalse(value['ready'])
        self.assertEqual(self.states(value)['tailscale-policy'], 'absent')
        self.assertEqual(self.states(value)['tailscale-devices'], 'absent')
        self.assertEqual(self.states(value)['instance-read'], 'ok')

    def test_refused_keys_report_their_public_key_only(self):
        value = self.report(Fake({credentials.POLICY: SAFE, credentials.DEVICES: SAFE},
                                 failing=('ls-remote', '--dry-run')))
        items = {entry['id']: entry for entry in value['items']}
        self.assertEqual(items['instance-read']['state'], 'failed')
        self.assertEqual(items['instance-write']['state'], 'failed')
        self.assertEqual(items['instance-write']['public_key'], KEY)
        self.assertNotIn('private', json.dumps(value))

    def test_each_git_check_uses_its_own_key(self):
        fake = Fake({credentials.POLICY: SAFE, credentials.DEVICES: SAFE})
        self.report(fake)
        commands = {argv[3]: env['GIT_SSH_COMMAND'] for argv, env in fake.calls if env and argv[0] == 'git'}
        self.assertTrue(commands['ls-remote'].startswith('ssh -i ') and commands['ls-remote'].split()[2].endswith('read'))
        self.assertTrue(commands['push'].split()[2].endswith('write'))

    def test_unsafe_file_mode_is_reported(self):
        value = self.report(Fake({credentials.POLICY: {'present': True, 'safe': False}, credentials.DEVICES: SAFE}))
        self.assertEqual(self.states(value)['tailscale-policy'], 'unsafe')

    def test_root_probe_returns_no_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tailscale-policy.env'
            path.write_text('TAILNET_ID="-"\nTS_OAUTH_CLIENT_ID="client"\nTS_OAUTH_CLIENT_SECRET="fixture-secret"\n')
            path.chmod(0o600)
            result = subprocess.run([sys.executable, '-', str(path), str(Path(directory) / 'absent')],
                                    input=credentials.ROOT_PROBE, text=True, capture_output=True, check=True)
        self.assertNotIn('fixture-secret', result.stdout)
        observed = json.loads(result.stdout)
        self.assertFalse(observed[str(Path(directory) / 'absent')]['present'])


if __name__ == '__main__':
    unittest.main()
