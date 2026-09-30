"""Replacement preparation keeps its accepted-router and grant fences."""
import copy
import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_replacement_preparation as replacement
from router_transaction import TransactionError
from test_router_updates import ENGINE, PROFILE, release
import test_router_candidate as candidate_fixture


class ReplacementPreparationTests(unittest.TestCase):
    def setUp(self):
        fixture = candidate_fixture.CandidateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.job = copy.deepcopy(fixture.job)
        self.release = release()
        self.accepted = {'box':'boxa','role':'router','generation':'b'*64,
                         'legacy':{'record_sha256':'b'*64}}
        self.binding = {'kind':'klokast.router-check-source.v1','box':'boxa',
                        'report_sha256':'d'*64,'source_operation':'e'*24,
                        'inputs_sha256':self.job['inputs_sha256'],'engine_commit':ENGINE}
        self.report = {'policy_sha256':'f'*64,'report_sha256':'d'*64}
        self.request = {'kind':'klokast.router-replacement-preparation.v1','box':'boxa',
            'mode':'replacement','operation_id':self.job['operation_id'],'engine_commit':ENGINE,
            'inputs_sha256':self.job['inputs_sha256'],'source_operation':'e'*24,
            'check_operation':'a'*24,'template_operation':'c'*24,'template_sha256':'d'*64,
            'job_sha256':replacement.generations.digest(self.job),
            'bootstrap':{name:{'bytes':1,'sha256':'e'*64} for name in ('kernel','initramfs')},
            'release_sha256':self.release['receipt_sha256'],'registry_sha256':'f'*64,
            'old_sha256':'b'*64,'accepted_assignment_sha256':'a'*64,
            'policy_sha256':'f'*64,'report_sha256':'d'*64,
            'source_binding_sha256':replacement.generations.digest(self.binding)}
        self.grant = {'kind':'klokast.router-replacement-preparation-grant.v1',
            'engine_commit':ENGINE,'request_sha256':replacement.generations.digest(self.request),
            'accepted_assignment_sha256':'a'*64,'policy_sha256':'f'*64,
            'granted_at':1000,'expires_at':1900}

    def authorize(self, now=1001):
        return replacement.authority(self.request,self.job,self.release,PROFILE,
            self.binding,self.report,{},self.accepted,self.grant,'boxa',
            self.job['operation_id'],ENGINE,now)

    def test_grant_and_checked_source_bind_exact_replacement(self):
        with patch.object(replacement.router_updates,'require_checked_source',
                          return_value='e'*24) as checked:
            self.assertEqual(self.authorize(),self.request)
            self.assertIsInstance(checked.call_args.kwargs['now'],dt.datetime)
            for field, changed in (('request_sha256','0'*64),
                                   ('accepted_assignment_sha256','0'*64),
                                   ('policy_sha256','0'*64),('expires_at',1001)):
                grant = self.grant
                self.grant = {**grant,field:changed}
                with self.subTest(field=field), self.assertRaisesRegex(TransactionError,'grant is stale'):
                    self.authorize()
                self.grant = grant
            checked.assert_called()

    def test_changed_accepted_or_source_binding_refuses(self):
        with patch.object(replacement.router_updates,'require_checked_source',
                          return_value='e'*24):
            accepted = self.accepted
            self.accepted = {**accepted,'generation':'0'*64}
            with self.assertRaisesRegex(TransactionError,'selected release or check'):
                self.authorize()
            self.accepted = accepted
            binding = self.binding
            self.binding = {**binding,'source_operation':'0'*24}
            with self.assertRaisesRegex(TransactionError,'selected release or check'):
                self.authorize()
            self.binding = binding

    def test_pending_or_changed_assignment_refuses_before_native_probe(self):
        storage = Mock(box='boxa')
        storage.pending.return_value = {'kind':'pending'}
        with self.assertRaisesRegex(TransactionError,'pending cutover'):
            replacement.accepted_runtime(storage,self.request,self.accepted,PROFILE)
        storage.pending.return_value = None
        storage.accepted.return_value = {'record_sha256':'0'*64,'current_sha256':'b'*64}
        with self.assertRaisesRegex(TransactionError,'assignment changed'):
            replacement.accepted_runtime(storage,self.request,self.accepted,PROFILE)
        storage.generation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
