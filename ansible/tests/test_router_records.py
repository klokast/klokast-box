"""Durable files decide recovery across interrupted publication and acceptance."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_generations as g
import router_records as r
from router_transaction import TransactionError
from test_router_generations import generation
from test_router_transaction import request


class RecordsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        # Test files belong to the test runner. Production always requires root,
        # including every ancestor; the temp tree is an isolated test fixture.
        for name, value in (('ROOT_UID', os.geteuid()), ('parents', lambda path: None)):
            patch = mock.patch.object(r, name, value)
            patch.start(); self.addCleanup(patch.stop)
        for name in ('records', 'generations', 'operations'):
            (self.base / name).mkdir(mode=0o700)
        self.records = r.Records('boxa', self.base)
        self.old, self.new = generation('legacy'), generation()
        for value in (self.old, self.new):
            r.write(self.base / 'records' / (value['record_sha256'] + '.json'), value)
        accepted = g.seal({'kind': 'klokast.router-assignment.v1', 'box': 'boxa', 'role': 'router',
            'current_sha256': self.old['record_sha256'], 'previous_sha256': None,
            'operation_id': '9' * 24, 'engine_commit': '9' * 40,
            'policy_sha256': '9' * 64, 'evidence_sha256': '8' * 64})
        r.write(self.base / 'accepted.json', accepted)
        self.request = {**request(), 'engine_commit': self.new['engine_commit'], 'accepted_sha256': accepted['record_sha256'],
                        'old_sha256': self.old['record_sha256'], 'candidate_sha256': self.new['record_sha256']}
        (self.base / 'operations' / self.request['operation_id']).mkdir(mode=0o700)
        self.pending = {'kind': 'klokast.router-pending.v1', 'request': self.request, 'phase': 'armed',
                        'candidate_started': False, 'old_started': False, 'reason': 'cutover'}

    def test_atomic_assignment_wins_over_stale_precommit_pending(self):
        pending = {**self.pending, 'phase': 'committing', 'candidate_started': True}
        self.records.persist(pending)
        self.assertFalse(self.records.committed(self.request))
        self.records.commit(self.request, '7' * 64)
        reboot = r.Records('boxa', self.base)
        self.assertEqual(reboot.pending()['phase'], 'committing')
        self.assertTrue(reboot.committed(self.request))
        self.assertEqual(reboot.accepted()['previous_sha256'], self.old['record_sha256'])
        reboot.persist({**pending, 'phase': 'accepted'})
        reboot.finish(self.request, 'accepted')
        self.assertIsNone(reboot.pending())
        self.assertTrue((self.base / 'operations' / self.request['operation_id'] / 'complete.json').is_file())

    def test_partial_write_and_orphan_temporary_do_not_replace_accepted_assignment(self):
        before = self.records.accepted()
        (self.base / '.accepted.json-orphan').write_text('partial')
        with mock.patch.object(r.os, 'replace', side_effect=OSError('simulated power loss')):
            with self.assertRaises(OSError):
                self.records.commit(self.request, '7' * 64)
        self.assertEqual(self.records.accepted(), before)
        self.records.commit(self.request, '7' * 64)
        self.assertTrue(self.records.committed(self.request))

    def test_wrong_box_stale_request_and_unrelated_assignment_refuse_recovery(self):
        self.records.persist(self.pending)
        changed = copy.deepcopy(self.pending)
        changed['request']['operation_id'] = '0' * 24
        with self.assertRaises(TransactionError):
            self.records.persist(changed)
        for change in ({'accepted_sha256': '0' * 64}, {'old_sha256': '0' * 64}):
            with self.assertRaises(TransactionError):
                self.records.committed({**self.request, **change})
        changed = copy.deepcopy(self.pending)
        changed['request']['box'] = 'another-box'
        with self.assertRaises(TransactionError):
            self.records.persist(changed)

    def test_corrupt_or_aliased_records_never_look_absent(self):
        pending = self.base / 'pending.json'
        pending.symlink_to(self.base / 'missing')
        with self.assertRaises(TransactionError):
            self.records.pending()
        pending.unlink()
        pending.write_text('{"request": {}, "request": {}}')
        with self.assertRaises(TransactionError):
            self.records.pending()
        pending.unlink()
        pending.hardlink_to(self.base / 'accepted.json')
        with self.assertRaises(TransactionError):
            self.records.pending()

    def test_lock_excludes_a_second_open_file_description(self):
        with self.records.lock():
            with self.assertRaises(TransactionError):
                with r.Records('boxa', self.base).lock():
                    self.fail('second router executor acquired a held lock')
        with self.records.lock():
            pass

    def test_finish_cannot_erase_an_incomplete_operation(self):
        self.records.persist(self.pending)
        with self.assertRaises(TransactionError):
            self.records.finish(self.request, 'rolled-back')
        self.assertEqual(self.records.pending(), self.pending)
        self.records.persist({**self.pending, 'phase': 'rolled-back', 'old_started': True})
        self.records.finish(self.request, 'rolled-back')
        self.assertFalse(self.records.committed(self.request))

    def test_supervised_baseline_publication_is_single_use_and_durable(self):
        (self.base / 'accepted.json').unlink()
        with self.records.lock():
            accepted = self.records.adopt(self.old)
        self.assertEqual(accepted['current_sha256'], self.old['record_sha256'])
        self.assertIsNone(accepted['previous_sha256'])
        self.assertEqual(accepted['policy_sha256'], r.BASELINE_AUTHORITY_SHA256)
        self.assertEqual(r.Records('boxa', self.base).accepted(), accepted)
        with self.records.lock(), self.assertRaisesRegex(TransactionError, 'already has'):
            self.records.adopt(self.old)

    def test_baseline_publication_refuses_pending_operation(self):
        (self.base / 'accepted.json').unlink()
        self.records.persist(self.pending)
        with self.records.lock(), self.assertRaisesRegex(TransactionError, 'no pending'):
            self.records.adopt(self.old)
        self.assertFalse((self.base / 'accepted.json').exists())

    def test_initial_publication_is_exact_idempotent_and_rejects_legacy_or_pending(self):
        (self.base / 'accepted.json').unlink()
        with self.records.lock():
            accepted = self.records.accept_initial(self.new)
            self.assertEqual(self.records.accept_initial(self.new), accepted)
        self.assertEqual(accepted['policy_sha256'], r.INITIAL_AUTHORITY_SHA256)
        self.assertEqual(accepted['current_sha256'], self.new['record_sha256'])
        self.assertIsNone(accepted['previous_sha256'])
        with self.records.lock(), self.assertRaisesRegex(TransactionError, 'one template'):
            self.records.accept_initial(self.old)
        other = copy.deepcopy(self.new)
        other['evidence_sha256'] = '0' * 64
        other.pop('record_sha256')
        other = g.seal(other)
        with self.records.lock(), self.assertRaisesRegex(TransactionError, 'different accepted'):
            self.records.accept_initial(other)
        (self.base / 'accepted.json').unlink()
        self.records.persist(self.pending)
        with self.records.lock(), self.assertRaisesRegex(TransactionError, 'no pending'):
            self.records.accept_initial(self.new)


if __name__ == '__main__':
    unittest.main()
