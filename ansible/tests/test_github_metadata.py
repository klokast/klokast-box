"""Fixed-source credential custody, fresh metadata and persistent rate-limit waits."""
import contextlib
from importlib.machinery import SourceFileLoader
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))
import github_metadata as client
import codex_artifact


def load(path, name):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    return module


class Response(io.BytesIO):
    def __init__(self, code=200, value=None, headers=None):
        super().__init__(json.dumps(value or {'draft': False, 'tag_name': 'rust-v1.2.3'}).encode())
        self.code = code; self.headers = headers or {}


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.b = load(ROOT / 'klokast-ops/secret-authority/bin/ksa-github-metadata', 'metadata_broker_test')
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.token = 'github_pat_' + 'X' * 40
        for name, value in (('STATE', self.root), ('CREDENTIAL', self.root / 'token.json'), ('AUDIT', self.root / 'audit.jsonl')):
            p = patch.object(self.b, name, value); p.start(); self.addCleanup(p.stop)

    def fetch(self, response, cache=None):
        opener = Mock(); opener.open.return_value = response
        with patch.object(self.b, 'transport', return_value=opener), patch.object(self.b.time, 'time', return_value=1000):
            result = self.b.request('codex-stable', self.token, cache or {})
        return result, opener

    def cache(self):
        return json.loads((self.root / 'codex-stable.json').read_text())

    def test_fixed_get_bearer_and_no_secret_in_result_state_or_audit(self):
        value, opener = self.fetch(Response(headers={'ETag': '"release"'}))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, self.b.SOURCES['codex-stable'])
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.token)
        for result in (json.dumps(value), (self.root / 'codex-stable.json').read_text(), self.b.AUDIT.read_text()):
            self.assertNotIn(self.token, result)
        self.assertEqual(stat.S_IMODE((self.root / 'codex-stable.json').stat().st_mode), 0o600)

    def test_cached_release_requires_an_authenticated_unchanged_response(self):
        expected, _ = self.fetch(Response(headers={'ETag': '"release"'}))
        actual, opener = self.fetch(Response(304), self.cache())
        self.assertEqual(actual, expected)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('If-none-match'), '"release"')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.token)

    def test_network_error_does_not_return_saved_release(self):
        self.fetch(Response(headers={'ETag': '"release"'}))
        opener = Mock(); opener.open.side_effect = urllib.error.URLError(self.token)
        with patch.object(self.b, 'transport', return_value=opener):
            with self.assertRaisesRegex(self.b.source.SourceError, 'no cached release') as error:
                self.b.request('codex-stable', self.token, self.cache())
        self.assertNotIn(self.token, str(error.exception))

    def test_primary_limit_survives_restarts_and_suppresses_early_requests(self):
        response = Response(403, {'message': 'API rate limit exceeded'},
                            {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1500', 'Retry-After': '900'})
        with self.assertRaisesRegex(self.b.source.SourceError, 'retry after'):
            self.fetch(response)
        saved = self.cache(); self.assertEqual(saved['retry_at'], 1900)
        with patch.object(self.b, 'transport') as transport, patch.object(self.b.time, 'time', return_value=1200):
            with self.assertRaisesRegex(self.b.source.SourceError, 'rate limited'):
                self.b.request('codex-stable', self.token, saved)
            transport.assert_not_called()
        with patch.object(self.b.time, 'time', return_value=1901), patch.object(self.b, 'transport') as transport:
            transport.return_value.open.return_value = Response()
            self.b.request('codex-stable', self.token, saved)
        self.assertNotIn('retry_at', self.cache())

    def test_secondary_limit_without_retry_header_uses_increasing_backoff(self):
        for attempt, delay in enumerate((60, 120, 240)):
            saved = self.cache() if attempt else {}
            saved.pop('retry_at', None)
            with self.assertRaises(self.b.source.SourceError):
                self.fetch(Response(403, {'message': 'You have exceeded a secondary rate limit'}), saved)
            self.assertEqual(self.cache()['retry_at'], 1000 + delay)

    def test_http_date_retry_after_and_success_with_exhausted_quota(self):
        headers = {'Retry-After': 'Thu, 01 Jan 1970 00:30:00 GMT'}
        self.assertEqual(self.b.retry_time(headers, 1000, 0), 1800)
        self.fetch(Response(headers={'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '2000'}))
        self.assertEqual(self.cache()['retry_at'], 2001)

    def test_token_rotation_does_not_inherit_the_previous_token_wait_or_etag(self):
        self.fetch(Response(headers={'ETag': '"old"'}))
        saved = dict(self.cache(), retry_at=9000)
        self.token = 'github_pat_' + 'Y' * 40
        _, opener = self.fetch(Response(), saved)
        self.assertIsNone(opener.open.call_args.args[0].get_header('If-none-match'))

    def test_redirects_cannot_receive_authorization(self):
        redirect = self.b.NoRedirect()
        self.assertIsNone(redirect.redirect_request(Mock(), Mock(), 302, 'Found', {}, 'https://example.invalid'))
        with self.assertRaisesRegex(self.b.source.SourceError, 'HTTP 302'):
            self.fetch(Response(302))

    def test_invalid_oversized_and_cacheless_responses_are_refused(self):
        invalid = Response(); invalid.seek(0); invalid.truncate(); invalid.write(b'[]'); invalid.seek(0)
        for response in (Response(304), invalid):
            with self.assertRaises(self.b.source.SourceError): self.fetch(response)
        with patch.object(self.b, 'MAX_BYTES', 10):
            with self.assertRaisesRegex(self.b.source.SourceError, 'size limit'): self.fetch(Response())

    def test_authentication_failure_does_not_start_a_rate_wait_or_print_body(self):
        with self.assertRaisesRegex(self.b.source.SourceError, 'HTTP 401') as error:
            self.fetch(Response(401, {'message': self.token}))
        self.assertNotIn(self.token, str(error.exception))
        self.assertFalse((self.root / 'codex-stable.json').exists())

    def test_failed_configure_preserves_the_previous_credential(self):
        self.b.CREDENTIAL.write_text('previous')
        output = io.StringIO()
        with patch.object(self.b, 'authority'), patch.object(self.b.sys, 'stdin', io.StringIO(json.dumps({'token': self.token}))), \
                patch.object(self.b, 'request', side_effect=ValueError(self.token)), contextlib.redirect_stderr(output):
            self.assertEqual(self.b.main(['configure']), 1)
        self.assertEqual(self.b.CREDENTIAL.read_text(), 'previous')
        self.assertNotIn(self.token, output.getvalue())

    def test_configure_saves_only_after_successful_check(self):
        with patch.object(self.b, 'authority'), patch.object(self.b.sys, 'stdin', io.StringIO(json.dumps({'token': self.token}))), \
                patch.object(self.b, 'request') as request, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.b.main(['configure']), 0)
        request.assert_called_once_with('codex-stable', self.token, {}, None)
        self.assertEqual(json.loads(self.b.CREDENTIAL.read_text()), {'token': self.token})
        self.assertEqual(stat.S_IMODE(self.b.CREDENTIAL.stat().st_mode), 0o600)

    def test_unauthorized_hosts_and_nonroot_fail_before_secret_reads(self):
        with patch.object(self.b.os, 'geteuid', return_value=1000):
            with self.assertRaisesRegex(self.b.source.SourceError, 'doas'): self.b.authority()
        for name in ('k001-air', 'vultr-ops'):
            with patch.object(self.b.os, 'geteuid', return_value=0), patch.object(self.b.socket, 'gethostname', return_value=name), \
                    patch.object(self.b.source, 'as_controller') as guard:
                with self.assertRaises(self.b.source.SourceError): self.b.authority()
                guard.assert_not_called()

    def test_fenced_controller_fails_before_secret_read_and_standby_uses_local_scope(self):
        with patch.object(self.b.os, 'geteuid', return_value=0), \
                patch.object(self.b.socket, 'gethostname', return_value='boxa-ops'), \
                patch.object(self.b.source, 'as_controller', side_effect=self.b.source.SourceError('fenced')) as guard, \
                patch.object(self.b, 'secure_read') as read, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.b.main(['fetch']), 1)
        guard.assert_called_once_with([self.b.source.GUARD, '--require-local-image-box', 'boxa']); read.assert_not_called()
        info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0, st_gid=0)
        with patch.object(self.b.os, 'geteuid', return_value=0), \
                patch.object(self.b.socket, 'gethostname', return_value='boxa-ops'), \
                patch.object(self.b.source, 'as_controller', return_value='{"role":"standby"}') as guard, \
                patch.object(self.b.Path, 'lstat', return_value=info):
            self.b.authority()
        guard.assert_called_once_with([self.b.source.GUARD, '--require-local-image-box', 'boxa'])

    def test_status_and_absent_token_do_not_contact_github(self):
        with patch.object(self.b, 'authority'), patch.object(self.b, 'transport') as transport, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.b.main(['status']), 0)
        self.assertFalse(json.loads(output.getvalue())['configured']); transport.assert_not_called()
        with patch.object(self.b, 'authority'), patch.object(self.b, 'transport') as transport, \
                contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(self.b.main(['fetch']), 1)
        self.assertIn('install-github-metadata-token', output.getvalue()); transport.assert_not_called()

    def test_proxy_and_direct_openers_ignore_ambient_proxy_configuration(self):
        with patch.object(self.b.urllib.request, 'ProxyHandler') as proxy, patch.object(self.b.urllib.request, 'build_opener'):
            self.b.transport(None); proxy.assert_called_with({})
            self.b.transport(12345); proxy.assert_called_with({'https':'http://127.0.0.1:12345'})

    def test_tls_trust_and_key_logging_do_not_follow_caller_environment(self):
        import ssl
        with patch.dict(os.environ, {'SSL_CERT_FILE':'/untrusted.pem', 'SSLKEYLOGFILE':str(self.root/'keys')}), \
                patch.object(self.b.urllib.request, 'HTTPSHandler') as handler, \
                patch.object(self.b.urllib.request, 'build_opener'):
            self.b.transport(None)
        context = handler.call_args.kwargs['context']
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIsNone(context.keylog_filename)
        self.assertFalse((self.root/'keys').exists())

    def test_source_and_proxy_arguments_are_bounded_before_provider_access(self):
        with patch.object(self.b, 'authority') as authority, contextlib.redirect_stderr(io.StringIO()):
            for argv in (['fetch', '--source', 'https://evil.invalid'], ['fetch', '--proxy-port', '65536']):
                with self.assertRaises(SystemExit): self.b.main(argv)
            authority.assert_not_called()

    def test_secure_read_refuses_unsafe_modes_and_symlinks(self):
        self.b.CREDENTIAL.write_text(json.dumps({'token': self.token})); self.b.CREDENTIAL.chmod(0o644)
        with self.assertRaises(self.b.source.SourceError): self.b.secure_read(self.b.CREDENTIAL, 2048)
        link = self.root / 'link'; link.symlink_to(self.b.CREDENTIAL)
        with self.assertRaises(OSError): self.b.secure_read(link, 2048)


class ClientTests(unittest.TestCase):
    def test_no_token_is_passed_or_read_and_loopback_transport_is_explicit(self):
        with patch.dict(os.environ, {'https_proxy': 'http://127.0.0.1:12345'}, clear=True), \
                patch.object(client.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"draft":false}', stderr='')) as run:
            self.assertEqual(client.fetch('codex-stable'), {'draft': False})
        self.assertEqual(run.call_args.args[0][-2:], ['--proxy-port', '12345'])
        self.assertNotIn('input', run.call_args.kwargs)

    def test_foreign_proxy_and_unknown_source_do_not_invoke_broker(self):
        with patch.object(client.subprocess, 'run') as run, patch.dict(os.environ, {'https_proxy':'http://evil.invalid'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'loopback'): client.fetch('codex-stable')
            with self.assertRaisesRegex(RuntimeError, 'unsupported'): client.fetch('other')
        run.assert_not_called()

    def test_direct_command_and_broker_error_propagation(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(client.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout='', stderr='retry after 2030-01-01 UTC')) as run:
            with self.assertRaisesRegex(RuntimeError, 'retry after'): client.fetch('codex-stable')
        self.assertNotIn('--proxy-port', run.call_args.args[0])

    def test_metadata_and_artifact_connections_are_separate(self):
        content = b'package'
        import hashlib
        release = {'draft':False, 'prerelease':False, 'tag_name':'rust-v1.2.3', 'assets':[
            {'name':codex_artifact.ASSET, 'size':len(content), 'digest':'sha256:'+hashlib.sha256(content).hexdigest(),
             'browser_download_url':'https://github.com/openai/codex/releases/download/rust-v1.2.3/'+codex_artifact.ASSET}]}
        with tempfile.TemporaryDirectory() as d, patch.object(client, 'fetch', return_value=release) as metadata, \
                patch.object(codex_artifact.urllib.request, 'urlopen', return_value=io.BytesIO(content)) as download:
            self.assertEqual(codex_artifact.resolve(d)['version'], '1.2.3')
        metadata.assert_called_once_with('codex-stable')
        self.assertEqual(download.call_args.args, (release['assets'][0]['browser_download_url'],))
        self.assertEqual(download.call_args.kwargs, {'timeout':30})

    def test_installer_uses_stdin_and_accepts_standby_without_active_dispatch(self):
        installer = load(ROOT / 'klokast-dev/bin/install-github-metadata-token', 'metadata_installer_test')
        token = 'github_pat_' + 'Z' * 40
        with patch.object(installer.getpass, 'getpass', return_value=token), \
                patch.object(installer.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(installer.main(['--controller','boxa-ops']), 0)
        self.assertIn('smith@boxa-ops', run.call_args.args[0])
        self.assertNotIn(token, ' '.join(run.call_args.args[0]))
        self.assertEqual(json.loads(run.call_args.kwargs['input']), {'token':token})


if __name__ == '__main__': unittest.main()
