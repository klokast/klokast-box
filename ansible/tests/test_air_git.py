"""Runner Git capability boundaries, idempotence, revocation and secret handling."""
import contextlib
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))


def load():
    loader = SourceFileLoader('ksa_air_git_test', str(ROOT / 'klokast-ops/secret-authority/bin/ksa-air-git'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RunnerGitTest(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.config = {'app_id': 1, 'installation_id': 2, 'private_key': 'SECRET-PEM'}
        self.key = 'ssh-ed25519 AAAATEST'

    def existing(self, *, key=None, title=None, read_only=False):
        return Mock(key=key or self.key, title=title or self.mod.title('boxa'),
                    read_only=read_only, id=17)

    def test_registration_is_idempotent_and_preserves_unrelated_keys(self):
        repo = Mock()
        unrelated = self.existing(key='ssh-ed25519 OTHER', title='unrelated')
        repo.get_keys.return_value = [unrelated]
        repo.create_key.return_value = self.existing()
        self.assertEqual(self.mod.register(repo, 'boxa', self.key)['result'], 'registered')
        repo.create_key.assert_called_once_with(self.mod.title('boxa'), self.key, read_only=False)
        repo.reset_mock()
        repo.get_keys.return_value = [unrelated, self.existing()]
        self.assertEqual(self.mod.register(repo, 'boxa', self.key)['result'], 'unchanged')
        repo.create_key.assert_not_called()
        unrelated.delete.assert_not_called()

    def test_conflicts_never_rotate_or_expand_an_existing_key(self):
        for existing in (self.existing(read_only=True), self.existing(title='another runner'),
                         self.existing(key='ssh-ed25519 OLD')):
            repo = Mock()
            repo.get_keys.return_value = [existing]
            with self.assertRaises(self.mod.source.SourceError):
                self.mod.register(repo, 'boxa', self.key)
            repo.create_key.assert_not_called()
            existing.delete.assert_not_called()

    def test_revoke_deletes_only_exact_runner_title(self):
        target, other = self.existing(), self.existing(title=self.mod.title('boxab'))
        repo = Mock()
        repo.get_keys.return_value = [target, other]
        self.assertEqual(self.mod.revoke(repo, 'boxa')['key_ids'], [17])
        target.delete.assert_called_once()
        other.delete.assert_not_called()

    def test_undeclared_runner_is_refused_before_provider_access(self):
        with patch.object(self.mod.os, 'geteuid', return_value=0), \
                patch.object(self.mod.source, 'require_controller'), \
                patch.object(self.mod.source, 'snapshot', return_value={'instance': {'airunners': []}}), \
                patch.object(self.mod, 'repository') as provider, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.mod.main(['register', '--box', 'boxa']), 1)
        provider.assert_not_called()

    def test_standby_refusal_precedes_secret_read(self):
        with patch.object(self.mod.os, 'geteuid', return_value=0), \
                patch.object(self.mod.source, 'require_controller', side_effect=self.mod.source.SourceError('standby')), \
                patch.object(self.mod, 'read_credential') as read, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.mod.main(['check']), 1)
        read.assert_not_called()

    def test_offline_revocation_does_not_require_current_runner_declaration(self):
        repo = Mock()
        repo.get_keys.return_value = []
        with patch.object(self.mod.os, 'geteuid', return_value=0), \
                patch.object(self.mod.source, 'require_controller'), \
                patch.object(self.mod.source, 'snapshot') as snapshot, \
                patch.object(self.mod, 'audit'), patch.object(self.mod, 'read_credential'), \
                patch.object(self.mod, 'repository', return_value=contextlib.nullcontext(repo)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.mod.main(['revoke', '--box', 'boxa']), 0)
        snapshot.assert_not_called()

    def test_invalid_key_is_rejected_before_registration(self):
        for key in ('ssh-rsa AAAA', 'ssh-ed25519 AAAA\nssh-ed25519 BBBB', 'x' * 16385):
            with self.assertRaises(self.mod.source.SourceError):
                self.mod.public_key(key)

    def test_invalid_credential_errors_do_not_print_secrets(self):
        output = io.StringIO()
        with patch.object(self.mod.os, 'geteuid', return_value=0), \
                patch.object(self.mod.source, 'require_controller'), \
                patch.object(self.mod.sys, 'stdin', io.StringIO(json.dumps(self.config))), \
                patch.object(self.mod, 'configure', side_effect=ValueError('bad key SECRET-PEM')), \
                contextlib.redirect_stderr(output):
            self.assertEqual(self.mod.main(['configure']), 1)
        self.assertNotIn('SECRET-PEM', output.getvalue())

    def test_configure_validates_before_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.mod, 'CREDENTIAL', Path(directory) / 'air-git.json'):
            self.mod.CREDENTIAL.write_text('old')
            with patch.object(self.mod, 'repository', side_effect=self.mod.source.SourceError('denied')):
                with self.assertRaises(self.mod.source.SourceError):
                    self.mod.configure(self.config)
            self.assertEqual(self.mod.CREDENTIAL.read_text(), 'old')
            repo = Mock()
            repo.get_keys.return_value = []
            with patch.object(self.mod, 'repository', return_value=contextlib.nullcontext(repo)):
                self.mod.configure(self.config)
            self.assertEqual(json.loads(self.mod.CREDENTIAL.read_text()), self.config)
            self.assertEqual(self.mod.CREDENTIAL.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [self.mod.CREDENTIAL])

    def test_unsafe_credential_mode_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.mod, 'CREDENTIAL', Path(directory) / 'air-git.json'):
            self.mod.CREDENTIAL.write_text(json.dumps(self.config))
            self.mod.CREDENTIAL.chmod(0o644)
            with self.assertRaises(self.mod.source.SourceError): self.mod.read_credential()
            target = Path(directory) / 'target'
            self.mod.CREDENTIAL.rename(target)
            self.mod.CREDENTIAL.symlink_to(target)
            with self.assertRaises(self.mod.source.SourceError): self.mod.read_credential()

    def fake_github(self, *, repositories=None, permissions=None):
        class ProviderError(Exception):
            status = 403
        integration, discovery, mutation = Mock(), Mock(), Mock()
        installation = SimpleNamespace(id=2, repository_selection='selected',
                                       permissions=permissions or self.mod.PERMISSIONS)
        integration.get_repo_installation.return_value = installation
        integration.requester.requestJsonAndCheck.side_effect = [({}, {'token': 'METADATA-SECRET'}),
                                                               ({}, {'token': 'ADMIN-SECRET'})]
        discovery.requester.requestJsonAndCheck.side_effect = [({}, {
            'total_count': len(repositories or [self.mod.REPOSITORY]),
            'repositories': [{'full_name': name} for name in repositories or [self.mod.REPOSITORY]]}), ({}, {})]
        mutation.get_repo.return_value = Mock(private=False, full_name=self.mod.REPOSITORY)
        module = SimpleNamespace(
            Auth=SimpleNamespace(AppAuth=Mock(), Token=Mock()), GithubException=ProviderError,
            GithubIntegration=Mock(return_value=contextlib.nullcontext(integration)),
            Github=Mock(side_effect=[contextlib.nullcontext(discovery), contextlib.nullcontext(mutation)]))
        return module, integration, discovery, mutation

    def test_tokens_are_scoped_and_revoked_even_when_operation_fails(self):
        github, integration, discovery, mutation = self.fake_github()
        with patch.dict(sys.modules, {'github': github}):
            with self.assertRaisesRegex(RuntimeError, 'failed operation'):
                with self.mod.repository(self.config):
                    raise RuntimeError('failed operation')
        inputs = [call.kwargs['input'] for call in integration.requester.requestJsonAndCheck.call_args_list]
        self.assertEqual(inputs, [{'permissions': {'metadata': 'read'}},
                                 {'repositories': ['klokast-box'], 'permissions': self.mod.PERMISSIONS}])
        discovery.requester.requestJsonAndCheck.assert_any_call('DELETE', '/installation/token')
        mutation.requester.requestJsonAndCheck.assert_called_once_with('DELETE', '/installation/token')

    def test_wrong_repository_selection_cannot_mint_admin_token(self):
        github, integration, discovery, mutation = self.fake_github(repositories=[self.mod.REPOSITORY, 'owner/private'])
        with patch.dict(sys.modules, {'github': github}), self.assertRaises(self.mod.source.SourceError):
            with self.mod.repository(self.config): self.fail('must not yield')
        self.assertEqual(integration.requester.requestJsonAndCheck.call_count, 1)
        discovery.requester.requestJsonAndCheck.assert_any_call('DELETE', '/installation/token')
        mutation.get_repo.assert_not_called()

    def test_excess_app_permission_is_refused_before_token_mint(self):
        github, integration, _, _ = self.fake_github(permissions={**self.mod.PERMISSIONS, 'contents': 'write'})
        with patch.dict(sys.modules, {'github': github}), self.assertRaises(self.mod.source.SourceError):
            with self.mod.repository(self.config): self.fail('must not yield')
        integration.requester.requestJsonAndCheck.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec('github'), 'uses the controller PyGithub package')
    def test_installed_pygithub_protocol_without_network_or_real_credentials(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import requests
        pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        calls = []
        def send(session, request, **kwargs):
            parsed = urlsplit(request.url)
            self.assertEqual(parsed.hostname, 'api.github.com')
            path = parsed.path
            body = json.loads(request.body) if request.body else None
            calls.append((request.method, path, body, request.headers.get('Authorization')))
            status = 200
            if path.endswith('/installation'):
                data = {'id': 2, 'repository_selection': 'selected', 'permissions': self.mod.PERMISSIONS}
            elif path.endswith('/access_tokens'):
                token = 'ADMIN-SECRET' if 'repositories' in body else 'METADATA-SECRET'
                data = {'token': token}
                status = 201
            elif path == '/installation/repositories':
                data = {'total_count': 1, 'repositories': [{'full_name': self.mod.REPOSITORY}]}
            elif request.method == 'DELETE' and path == '/installation/token':
                data = None
                status = 204
            elif path == '/repos/' + self.mod.REPOSITORY:
                data = {'id': 10, 'full_name': self.mod.REPOSITORY, 'private': False,
                        'url': 'https://api.github.com' + path}
            elif path == '/repos/' + self.mod.REPOSITORY + '/keys':
                data = [] if request.method == 'GET' else {'id': 17, **body}
                status = 200 if request.method == 'GET' else 201
            else:
                raise AssertionError('unexpected request: ' + request.method + ' ' + path)
            response = requests.Response()
            response.status_code, response.url = status, request.url
            response.headers['Content-Type'] = 'application/json'
            response._content = json.dumps(data).encode() if data is not None else b''
            return response
        with patch.object(requests.Session, 'send', send):
            with self.mod.repository({**self.config, 'private_key': pem}) as repo:
                result = self.mod.register(repo, 'boxa', self.key)
        self.assertEqual(result, {'result': 'registered', 'key_id': 17})
        mutations = [row for row in calls if row[0] == 'POST' and row[1].endswith('/keys')]
        self.assertEqual(mutations[0][2]['read_only'], False)
        self.assertIn('ADMIN-SECRET', mutations[0][3])
        self.assertEqual(sum(row[0] == 'DELETE' for row in calls), 2)

    def test_laptop_installer_transports_secret_only_on_stdin(self):
        path = ROOT / 'klokast-dev/bin/install-air-github-app'
        loader = SourceFileLoader('air_git_installer_test', str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        installer = importlib.util.module_from_spec(spec)
        loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            pem = Path(directory) / 'key.pem'
            pem.write_text('SECRET-PEM')
            with patch.object(sys, 'argv', [str(path), '--controller', 'boxa-ops', '--app-id', '1',
                                           '--installation-id', '2', '--pem', str(pem)]), \
                    patch.object(installer.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(installer.main(), 0)
        self.assertNotIn('SECRET-PEM', ' '.join(run.call_args.args[0]))
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['private_key'], 'SECRET-PEM')
        self.assertEqual(run.call_args.args[0][-2:], ['/usr/local/sbin/ksa-air-git', 'configure'])


if __name__ == '__main__': unittest.main()
