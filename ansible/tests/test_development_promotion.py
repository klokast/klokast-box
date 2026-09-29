#!/usr/bin/env python3
"""Development publication preserves exact trees, source fencing, and recovery."""
import argparse
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_engine_promotion as engine_tests
from test_engine_promotion import load_module, NEW_COMMIT


class DevelopmentCandidateTest(unittest.TestCase):
    def test_generated_candidate_passes_existing_bidirectional_validator(self):
        fixture = engine_tests.EnginePromotionTest()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.mod, self.args, self.checkout = fixture.mod, fixture.args, fixture.checkout
        self.git = fixture.git
        self.old_build, self.new_build = fixture.old_build, fixture.new_build
        self.args.new_engine_commit = NEW_COMMIT
        self.args.schema_transition = self.mod.SCHEMA_TRANSITION_LEGACY_TO_CURRENT
        before = self.git(self.checkout, 'rev-parse', 'HEAD')
        envelope = self.mod.development_candidate(self.args)
        self.mod.validate_candidate_tree(self.args, envelope, self.old_build, self.new_build)
        self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), before)
        self.assertEqual(self.git(self.checkout, 'status', '--porcelain'), '')


class DevelopmentPublicationTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.operation = Path(self.temp.name)
        self.args = argparse.Namespace(repo_root='unused', new_engine_commit=NEW_COMMIT)
        self.receipt = {'private_base_commit': 'a'*40, 'private_base_tree': 'b'*40,
                        'candidate_tree': 'c'*40, 'new_engine_commit': NEW_COMMIT,
                        'receipt_sha256': 'd'*64,
                        'expires_at': self.mod.format_utc(self.mod.now_utc()+dt.timedelta(minutes=10))}
        self.envelope = {'candidate_instance_json': '{}\n', 'candidate_lock_json': '{}\n'}
        self.calls = []
        self.current = self.receipt['private_base_commit']
        self.wrong_tree = False
        self.disconnect = False
        self.conflict = False
        for name in ('require_development_mode', 'require_public_checkout', 'audit'):
            patcher = mock.patch.object(self.mod, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(self.mod, 'github_request', side_effect=self.api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def api(self, token, method, path, body=None, expected=(200,)):
        self.calls.append((method, path, body))
        if method == 'GET':
            return {'object': {'sha': self.current}}
        if path.endswith('/trees'):
            return {'sha': 'f'*40 if self.wrong_tree else self.receipt['candidate_tree']}
        if path.endswith('/commits'):
            return {'sha': 'e'*40, 'tree': {'sha': self.receipt['candidate_tree']},
                    'parents': [{'sha': self.receipt['private_base_commit']}]}
        self.assertEqual(method, 'PATCH')
        self.assertEqual(body, {'sha': 'e'*40, 'force': False})
        # The recovery record must exist before publication can become visible.
        self.assertEqual(json.loads((self.operation/'publication.json').read_text())['commit'], 'e'*40)
        if self.conflict:
            raise self.mod.InstanceAuthorityError('non-fast-forward')
        self.current = body['sha']
        if self.disconnect:
            raise self.mod.InstanceAuthorityError('response lost')
        return {'object': {'sha': self.current}}

    def publish(self):
        return self.mod.development_publish(self.args, 'secret', {'repository': 'org/klokast-instance'},
                                            self.envelope, self.receipt, self.operation)

    def test_publishes_exact_tree_with_one_parent_and_no_force(self):
        self.assertEqual(self.publish(), 'e'*40)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.calls[1][2]['base_tree'], self.receipt['private_base_tree'])
        self.assertEqual([x['path'] for x in self.calls[1][2]['tree']],
                         ['klokast-instance.json', 'klokast.lock.json'])

    def test_changed_main_refuses_before_writes(self):
        self.current = 'f'*40
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, 'main changed'):
            self.publish()
        self.assertEqual(len(self.calls), 1)

    def test_wrong_remote_tree_refuses_before_commit(self):
        self.wrong_tree = True
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, 'tree differs'):
            self.publish()
        self.assertEqual(len(self.calls), 2)

    def test_expiration_refuses_before_writes(self):
        self.receipt['expires_at'] = self.mod.format_utc(self.mod.now_utc()-dt.timedelta(seconds=1))
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, 'expired'):
            self.publish()
        self.assertEqual(len(self.calls), 1)

    def test_lost_patch_response_resumes_without_second_write_even_after_expiry(self):
        self.disconnect = True
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, 'response lost'):
            self.publish()
        self.calls.clear()
        self.receipt['expires_at'] = self.mod.format_utc(self.mod.now_utc()-dt.timedelta(seconds=1))
        self.assertEqual(self.publish(), 'e'*40)
        self.assertEqual(len(self.calls), 1)

    def test_concurrent_commit_refuses_and_retains_record(self):
        self.conflict = True
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, 'non-fast-forward'):
            self.publish()
        self.assertTrue((self.operation/'publication.json').is_file())


class DevelopmentCredentialTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.state = {'repository': 'org/klokast-instance', 'repository_id': 42}
        self.app = mock.Mock()
        self.integration = mock.Mock()
        self.integration.get_app_installation.return_value.permissions = {'contents':'write', 'metadata':'read'}
        self.integration.create_jwt.return_value = 'jwt'
        self.app.integration.return_value = (self.integration, 10)
        self.responses = [
            {'token':'secret', 'permissions': {'contents':'write', 'metadata':'read'}},
            {'total_count':1, 'repositories':[{'id':42, 'full_name':'org/klokast-instance', 'private':True}]},
            {},
        ]

    def token(self):
        with mock.patch.object(self.mod, 'require_development_mode'), mock.patch.object(
            self.mod.Path, 'lstat', return_value=mock.Mock(st_uid=0, st_mode=0o700)
        ), mock.patch.object(self.mod.stat, 'S_ISREG', return_value=True), mock.patch.object(
            self.mod.stat, 'S_ISDIR', return_value=True
        ), mock.patch.object(self.mod, 'GithubApp', return_value=self.app), mock.patch.object(
            self.mod, 'github_request', side_effect=self.responses
        ) as api:
            try:
                return self.mod.development_token(self.state)
            finally:
                self.calls = api.call_args_list

    def test_token_request_selects_exact_repository_and_permissions(self):
        self.assertEqual(self.token(), 'secret')
        self.assertEqual(self.calls[0].args[3], {'repository_ids':[42], 'permissions':{'contents':'write','metadata':'read'}})

    def test_unrelated_app_authority_is_rejected_before_mint(self):
        self.integration.get_app_installation.return_value.permissions['administration'] = 'write'
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, 'only Contents'):
            self.token()
        self.assertEqual(self.calls, [])

    def test_wrong_repository_is_rejected_and_token_revoked(self):
        self.responses[1]['repositories'][0]['id'] = 99
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, 'registered private repository'):
            self.token()
        self.assertEqual(self.calls[-1].args, ('secret','DELETE','/installation/token'))

    def test_extra_repository_is_rejected_and_token_revoked(self):
        self.responses[1]['total_count'] = 2
        with self.assertRaisesRegex(self.mod.InstanceAuthorityError, 'registered private repository'):
            self.token()
        self.assertEqual(self.calls[-1].args, ('secret','DELETE','/installation/token'))


if __name__ == '__main__':
    unittest.main()
