"""Transport behavior against a stub Tailscale executable, without network access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / 'ansible/bin/platform-tailscale-ssh'
sys.path.insert(0, str(ROOT / 'ansible/lib'))
from app_support import tailscale_ssh


class TransportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='tailscale-transport-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.log = self.directory / 'invocations.jsonl'
        self.env = {**os.environ, 'PATH': str(self.directory),
                    'TEST_TRANSPORT_LOG': str(self.log)}
        stub = self.directory / 'tailscale'
        stub.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
record = {'argv': sys.argv[1:], 'stdin': sys.stdin.buffer.read().hex()}
with open(os.environ['TEST_TRANSPORT_LOG'], 'a') as log:
    log.write(json.dumps(record) + '\\n')
sys.stdout.write(json.dumps(record))
sys.stderr.write('stub stderr\\n')
sys.exit(int(os.environ.get('TEST_TRANSPORT_EXIT', '0')))
''')
        stub.chmod(0o755)

    def invoke(self, *arguments, stdin=b''):
        return subprocess.run([str(ADAPTER), *arguments], input=stdin,
                              capture_output=True, env=self.env, timeout=10)

    def test_ansible_arguments_preserve_command_stdin_and_streams(self):
        # SSH plugin arguments from Ansible core 2.20, including inventory args.
        command = ['sh', '-s', '--', 'two words', '$(touch sentinel)',
                   '`touch sentinel`', "a'b", '; echo injected', '-o', 'remote=value']
        result = self.invoke(
            '-vvv', '-C', '-o', 'ControlMaster=no', '-o', 'ControlPersist=no',
            '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'PreferredAuthentications=gssapi-with-mic,gssapi-keyex,hostbased,publickey',
            '-o', 'PasswordAuthentication=no', '-o', 'User="neo"',
            '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=accept-new',
            'boxa-torrent', *command, stdin=b'printf hello\n\x00\xff')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            'argv': ['ssh', 'neo@boxa-torrent', *command],
            'stdin': b'printf hello\n\x00\xff'.hex(),
        })
        self.assertEqual(result.stderr, b'stub stderr\n')

    def test_user_forms_and_option_terminator(self):
        for arguments, target in (
            (['boxa-torrent'], 'boxa-torrent'),
            (['neo@boxa-torrent'], 'neo@boxa-torrent'),
            (['--', 'boxa-torrent'], 'boxa-torrent'),
            (['-l', 'neo', '--', 'boxa-torrent'], 'neo@boxa-torrent'),
            (['-o', "User='neo'", 'boxa-torrent'], 'neo@boxa-torrent'),
            (['-l', 'neo', '-o', 'User="neo"', 'neo@boxa-torrent'], 'neo@boxa-torrent'),
            (['-l', 'neo', '100.64.1.2'], 'neo@100.64.1.2'),
            (['-l', 'neo', 'fd7a:115c:a1e0::1'], 'neo@fd7a:115c:a1e0::1'),
        ):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments, 'true')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['argv'], ['ssh', target, 'true'])

    def test_documented_compatibility_flags_are_consumed(self):
        arguments = ['-i', '/unused/key', '-F', '/unused/config', '-S', '/unused/socket',
                     '-b', '192.0.2.1', '-c', 'unused-cipher', '-m', 'unused-mac', '-p', '22',
                     '-t', '-tt', '-T', '-C', '-v', '-vv', '-vvvv']
        for key, value in {
            'ControlMaster': 'no', 'ControlPersist': 'no', 'ControlPath': '/unused/socket',
            'StrictHostKeyChecking': 'yes', 'UserKnownHostsFile': '/unused/known_hosts',
            'ConnectTimeout': '10', 'KbdInteractiveAuthentication': 'no',
            'PreferredAuthentications': 'publickey', 'PasswordAuthentication': 'no',
            'GSSAPIAuthentication': 'no', 'BatchMode': 'yes', 'IdentityFile': '/unused/key',
            'IdentitiesOnly': 'yes', 'Port': '22',
        }.items():
            arguments.extend(['-o', f'{key}={value}'])
        result = self.invoke(*arguments, 'boxa-torrent')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['argv'], ['ssh', 'boxa-torrent'])

    def test_invalid_inputs_fail_before_tailscale(self):
        cases = [[], ['--'], [''], ['--', '-destination'],
                 ['-Z', 'boxa'], ['-vZ', 'boxa'], ['-oUser=neo', 'boxa'],
                 ['-o', 'ProxyCommand=ignored', 'boxa'], ['-o', 'User', 'boxa'],
                 ['-o', 'User=', 'boxa'], ['-o', 'User=""', 'boxa'],
                 ['-o', 'ControlMaster=', 'boxa'], ['-l', '', 'boxa'],
                 ['-l', 'neo', '-o', 'User=root', 'boxa'],
                 ['-l', 'neo', 'root@boxa'], ['neo@'], ['@boxa'],
                 ['neo@root@boxa'], ['neo@-boxa'], ['two hosts'],
                 ['-l', 'two users', 'boxa']]
        for flag in ('-l', '-o', '-i', '-F', '-S', '-b', '-c', '-m', '-p'):
            cases.extend(([flag], [flag, ''], [flag, '-T', 'boxa']))
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 255)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'platform-tailscale-ssh:', result.stderr)
                self.assertFalse(self.log.exists())

    def test_child_failure_is_preserved(self):
        self.env['TEST_TRANSPORT_EXIT'] = '37'
        result = self.invoke('-l', 'neo', 'boxa', 'false')
        self.assertEqual(result.returncode, 37)
        self.assertEqual(result.stderr, b'stub stderr\n')
        self.assertEqual(json.loads(result.stdout)['argv'], ['ssh', 'neo@boxa', 'false'])


class EnvironmentTest(unittest.TestCase):
    def test_settings_copy_input_and_preserve_caller_fields(self):
        original = {
            'PATH': '/caller/bin', 'ANSIBLE_CONFIG': '/caller/ansible.cfg',
            'ANSIBLE_ROLES_PATH': '/caller/roles', 'ANSIBLE_SSH_EXECUTABLE': '/usr/bin/ssh',
            'ANSIBLE_SSH_ARGS': '-o ControlMaster=auto',
            'ANSIBLE_SSH_COMMON_ARGS': '-o UserKnownHostsFile=/unused',
            'ANSIBLE_SSH_EXTRA_ARGS': '-o UnsupportedOption=yes',
            'ANSIBLE_SSH_TRANSFER_METHOD': 'sftp', 'ANSIBLE_SSH_USETTY': 'true',
        }
        before = dict(original)
        result = tailscale_ssh.ansible_environment(original, executable=ADAPTER)
        self.assertEqual(original, before)
        self.assertEqual(result, {**original,
            'ANSIBLE_SSH_EXECUTABLE': str(ADAPTER),
            'ANSIBLE_SSH_ARGS': '-o ControlMaster=no -o ControlPersist=no',
            'ANSIBLE_SSH_COMMON_ARGS': '', 'ANSIBLE_SSH_TRANSFER_METHOD': 'piped',
            'ANSIBLE_SSH_USETTY': 'false'})

    def test_default_requires_installed_adapter_without_source_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing'
            with patch.object(tailscale_ssh, 'DEFAULT_EXECUTABLE', missing):
                with self.assertRaisesRegex(OSError, 'converge the controller toolchain'):
                    tailscale_ssh.ansible_environment({})

    def test_non_executable_directory_and_relative_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'not-executable'
            file.write_text('unused')
            file.chmod(0o644)
            for path in (file, Path(directory), Path('relative-adapter')):
                with self.subTest(path=path), self.assertRaises(OSError):
                    tailscale_ssh.ansible_environment({}, executable=path)


if __name__ == '__main__':
    unittest.main()
