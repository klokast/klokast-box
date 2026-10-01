"""Completed router proof must come from final protected A/B records."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_executor as executor
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_dom0 as dom0_tests


class CompletionTests(unittest.TestCase):
    def setUp(self):
        dom0_tests.Dom0Tests.setUp(self)
        self.accept = lambda: dom0_tests.Dom0Tests.accept(self)
        patch = mock.patch.object(executor, 'adapter', return_value=self.adapter)
        patch.start(); self.addCleanup(patch.stop)
        self.copy.verify_receipt = mock.Mock(side_effect=lambda backend, phase:
            '1' * 64 if phase == 'forward' else '2' * 64)

    def complete(self, outcome='accepted', started=True):
        if outcome == 'accepted':
            self.accept()
            proof = records.read(self.work / 'acceptance.json')
            self.records.commit(self.request, proof['evidence_sha256'])
        pending = {'kind': 'klokast.router-pending.v1', 'request': self.request,
            'phase': outcome, 'candidate_started': started, 'old_started': outcome == 'rolled-back',
            'reason': 'cutover' if outcome == 'accepted' else 'deadline-or-check'}
        self.records.persist(pending)
        self.records.finish(self.request, outcome)
        return self.read()

    def read(self):
        return executor.completion_status(self.records, self.request['operation_id'], self.request['engine_commit'])

    def test_accepted_proof_binds_archived_assignment_service_and_forward_copy(self):
        result = self.complete()
        self.assertEqual(result['outcome'], 'accepted')
        self.assertEqual(result['assignment'], self.records.accepted())
        self.assertEqual(result['copy_receipts'], {'forward': '1' * 64})
        self.assertEqual(result['acceptance_sha256'], generations.digest(records.read(self.work / 'acceptance.json')))
        generations.check_seal(result)
        self.copy.verify_receipt.assert_called_once_with(self.adapter, 'forward')

    def test_started_candidate_rollback_requires_both_copy_receipts_and_original_assignment(self):
        result = self.complete('rolled-back')
        self.assertEqual(result['assignment']['record_sha256'], self.request['accepted_sha256'])
        self.assertEqual(result['copy_receipts'], {'forward': '1' * 64, 'reverse': '2' * 64})
        self.assertTrue(result['candidate_started'])
        self.assertTrue(result['old_started'])
        self.assertIsNone(result['acceptance_sha256'])

    def test_rollback_before_candidate_start_is_explicit_and_has_no_copy_proof(self):
        result = self.complete('rolled-back', started=False)
        self.assertEqual(result['copy_receipts'], {})
        self.assertFalse(result['candidate_started'])
        self.copy.verify_receipt.assert_not_called()

    def test_assignment_remains_archived_after_a_later_current_pointer(self):
        original = self.complete()
        changed = {key: value for key, value in original['assignment'].items() if key != 'record_sha256'}
        changed['evidence_sha256'] = '5' * 64
        records.write(self.base / 'accepted.json', generations.seal(changed))
        result = self.read()
        self.assertEqual(result['assignment'], original['assignment'])
        self.assertNotEqual(result['current_assignment'], original['assignment'])

    def test_pending_changed_final_phase_missing_snapshot_and_wrong_engine_refuse(self):
        self.complete()
        with self.assertRaisesRegex(TransactionError, 'engine'):
            executor.completion_status(self.records, self.request['operation_id'], '0' * 40)
        latest = records.read(self.work / 'latest.json')
        records.write(self.work / 'latest.json', {**latest, 'reason': 'boot-recovery'})
        with self.assertRaisesRegex(TransactionError, 'final protected phase'):
            self.read()
        records.write(self.work / 'latest.json', latest)
        records.write(self.base / 'pending.json', latest)
        with self.assertRaisesRegex(TransactionError, 'pending'):
            self.read()
        (self.base / 'pending.json').unlink()
        (self.work / 'completion-assignment.json').unlink()
        with self.assertRaisesRegex(TransactionError, 'older evidence'):
            self.read()

    def test_corrupt_assignment_and_copy_failure_cannot_yield_readiness_evidence(self):
        self.complete()
        self.copy.verify_receipt.side_effect = TransactionError('missing native receipt')
        with self.assertRaisesRegex(TransactionError, 'native receipt'):
            self.read()
        self.copy.verify_receipt.side_effect = lambda *args: None
        with self.assertRaisesRegex(TransactionError, 'state-copy receipts'):
            self.read()
        value = records.read(self.work / 'completion-assignment.json')
        records.write(self.work / 'completion-assignment.json', {**value, 'evidence_sha256': '0' * 64})
        with self.assertRaises(RuntimeError):
            self.read()

    def test_completion_retry_cannot_overwrite_a_different_archived_assignment(self):
        self.complete()
        latest = records.read(self.work / 'latest.json')
        self.records.persist(latest)
        value = records.read(self.work / 'completion-assignment.json')
        changed = {key: item for key, item in value.items() if key != 'record_sha256'}
        changed['evidence_sha256'] = '0' * 64
        records.write(self.work / 'completion-assignment.json', generations.seal(changed))
        with self.assertRaisesRegex(TransactionError, 'changed during recovery'):
            self.records.finish(self.request, 'accepted')
        self.assertIsNotNone(self.records.pending())

    def test_interrupted_finish_retains_snapshot_and_pending_then_retries_exactly(self):
        self.accept()
        self.records.commit(self.request, records.read(self.work / 'acceptance.json')['evidence_sha256'])
        pending = {'kind': 'klokast.router-pending.v1', 'request': self.request,
            'phase': 'accepted', 'candidate_started': True, 'old_started': False, 'reason': 'cutover'}
        self.records.persist(pending)
        original = records.write
        def interrupted(path, value):
            if path.name == 'complete.json':
                raise OSError('simulated power loss')
            original(path, value)
        with mock.patch.object(records, 'write', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.records.finish(self.request, 'accepted')
        self.assertTrue((self.work / 'completion-assignment.json').is_file())
        self.assertIsNotNone(self.records.pending())
        self.records.finish(self.request, 'accepted')
        self.assertIsNone(self.records.pending())
        self.assertEqual(self.read()['outcome'], 'accepted')


if __name__ == '__main__':
    unittest.main()
