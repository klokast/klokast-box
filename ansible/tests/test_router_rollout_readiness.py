"""Per-box readiness requires real completion contracts and distinct identities."""
import copy
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_executor as executor
import router_generations as generations
import router_generation_device as devices
import router_records as records
from router_transaction import TransactionError


def proofs():
    engine, box, policy = 'a' * 40, 'boxa', 'b' * 64
    forward = {'kind': 'klokast.router-transaction-request.v1', 'role': 'router', 'box': box,
        'operation_id': 'a' * 24, 'engine_commit': engine, 'policy_sha256': policy,
        'accepted_sha256': '0' * 64, 'old_sha256': '1' * 64, 'candidate_sha256': '2' * 64,
        'cutover_seconds': 1800, 'recovery_seconds': 900}
    original = generations.seal({'kind': 'klokast.router-assignment.v1', 'box': box, 'role': 'router',
        'current_sha256': '1'*64, 'previous_sha256': None, 'operation_id': 'c'*24,
        'engine_commit': engine, 'policy_sha256': policy, 'evidence_sha256': 'd'*64})
    forward['accepted_sha256'] = original['record_sha256']
    assignment = records.accepted_candidate(forward, '3' * 64)
    rollback = {**forward, 'operation_id': 'b' * 24, 'candidate_sha256': '4' * 64}
    def device(generation, machine, hostname):
        return generations.seal({'kind': 'klokast.router-generation-device.v1', 'box': box,
            'generation_sha256': generation, 'machine_id': machine, 'hostname': hostname,
            'source_sha256': '5' * 64})
    old = device('1' * 64, 'nOriginalRouter', box + '-router')
    accepted = device('2' * 64, 'nAcceptedRouter', box + '-router-' + 'a' * 24)
    rejected = device('4' * 64, 'nRejectedRouter', box + '-router-' + 'b' * 24)
    base = {'kind': 'klokast.router-completed-operation.v2', 'box': box, 'engine_commit': engine,
        'completion_sha256': '6' * 64, 'assignment': assignment, 'current_assignment': assignment,
        'candidate_started': True}
    f = generations.seal({**base, 'operation_id': forward['operation_id'], 'request': forward,
        'outcome': 'accepted', 'old_started': False, 'state_change_observed': None,
        'reason': 'cutover', 'copy_receipts': {'forward': '7' * 64},
        'devices': {'old': old, 'candidate': accepted}, 'acceptance_sha256': '8' * 64})
    r = generations.seal({**base, 'operation_id': rollback['operation_id'], 'request': rollback,
        'assignment': original, 'outcome': 'rolled-back', 'old_started': True, 'state_change_observed': True,
        'reason': 'deadline-or-check', 'copy_receipts': {'forward': '9' * 64, 'reverse': 'a' * 64},
        'devices': {'old': old, 'candidate': rejected}, 'acceptance_sha256': None})
    return f, r


def reseal(value):
    value = copy.deepcopy(value)
    value.pop('record_sha256', None)
    return generations.seal(value)


class RolloutReadinessTests(unittest.TestCase):
    def setUp(self):
        self.forward, self.rollback = proofs()
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.base = Path(root.name)
        self.storage = SimpleNamespace(box='boxa', base=self.base,
            accepted=lambda: self.forward['assignment'], pending=lambda: None, cold_test=lambda: None)
        for name, value in (('ROOT_UID', os.geteuid()), ('parents', lambda path: None)):
            patch = mock.patch.object(records, name, value)
            patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(executor, 'completion_status', side_effect=lambda storage, operation, engine:
            self.forward if operation == 'a' * 24 else self.rollback)
        self.completed = patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(executor.native, 'recovery_boot_chain', return_value={'engine': 'a' * 40, 'bytes': 'b' * 64})
        self.boot = patch.start(); self.addCleanup(patch.stop)

    def qualify(self):
        return executor.qualify_rollout(self.storage, 'a' * 24, 'b' * 24, 'a' * 40)

    def status(self, engine='a' * 40):
        return executor.rollout_status(self.storage, engine)

    def test_missing_proof_is_closed_and_native_pair_qualifies_only_its_box(self):
        self.assertFalse(self.status()['ready'])
        result = self.qualify()
        self.assertFalse(result['replacement_authorized'])
        self.assertTrue(self.status()['ready'])
        self.assertEqual(self.status()['policy_sha256'], self.forward['request']['policy_sha256'])
        with self.assertRaises(RuntimeError):
            executor.rollout_pair(self.forward, self.rollback, 'other', 'a' * 40)

    def test_candidate_not_started_unchanged_state_missing_reverse_or_unenrolled_refuse(self):
        for field, value in (('candidate_started', False), ('state_change_observed', False),
                ('copy_receipts', {'forward': '9' * 64}), ('reason', 'boot-recovery'),
                ('devices', {'old': self.rollback['devices']['old'], 'candidate': None})):
            with self.subTest(field=field):
                changed = reseal({**self.rollback, field: value})
                with self.assertRaises(RuntimeError):
                    executor.rollout_pair(self.forward, changed, 'boxa', 'a' * 40)
        self.assertFalse((self.base / 'rollout-ready.json').exists())

    def test_wrong_policy_generation_or_accepted_assignment_cannot_be_combined(self):
        for field, value in (('policy_sha256', 'c' * 64), ('old_sha256', '5' * 64),
                             ('accepted_sha256', '6' * 64)):
            with self.subTest(field=field):
                changed = reseal({**self.rollback, 'request': {**self.rollback['request'], field: value}})
                with self.assertRaises(RuntimeError):
                    executor.rollout_pair(self.forward, changed, 'boxa', 'a' * 40)

    def test_rollback_after_success_cannot_replace_cross_version_recovery_proof(self):
        changed = copy.deepcopy(self.rollback)
        changed['request']['old_sha256'] = self.forward['request']['candidate_sha256']
        changed['request']['accepted_sha256'] = self.forward['assignment']['record_sha256']
        changed['assignment'] = self.forward['assignment']
        changed['devices']['old'] = self.forward['devices']['candidate']
        with self.assertRaisesRegex(TransactionError, 'restoration of the original'):
            executor.rollout_pair(self.forward, reseal(changed), 'boxa', 'a'*40)

    def test_reused_generation_identity_refuses_even_with_valid_checksums(self):
        changed = copy.deepcopy(self.rollback)
        changed['devices']['candidate'] = reseal({**changed['devices']['candidate'],
            'machine_id': self.forward['devices']['old']['machine_id']})
        with self.assertRaisesRegex(TransactionError, 'Tailnet identities'):
            executor.rollout_pair(self.forward, reseal(changed), 'boxa', 'a' * 40)

    def test_changed_engine_code_pending_or_corrupt_cache_closes_readiness(self):
        self.qualify()
        self.assertFalse(self.status('c' * 40)['ready'])
        self.boot.return_value = {'engine': 'a' * 40, 'bytes': 'd' * 64}
        self.assertFalse(self.status()['ready'])
        self.storage.pending = lambda: {'active': True}
        self.assertFalse(self.status()['ready'])
        self.storage.pending = lambda: None
        path = self.base / 'rollout-ready.json'
        value = records.read(path)
        records.write(path, {**value, 'policy_sha256': 'd' * 64})
        with self.assertRaises(RuntimeError):
            self.status()

    def test_readiness_survives_raw_copy_cleanup_but_rechecks_the_boot_chain(self):
        self.qualify()
        self.completed.side_effect = AssertionError('historical raw copy slots are no longer required')
        self.assertTrue(self.status()['ready'])
        self.assertGreaterEqual(self.boot.call_count, 2)

    def test_unpersisted_chain_or_changed_accepted_assignment_cannot_publish(self):
        self.boot.side_effect = TransactionError('missing persisted chain')
        with self.assertRaisesRegex(TransactionError, 'persisted chain'):
            self.qualify()
        self.assertFalse((self.base / 'rollout-ready.json').exists())
        self.boot.side_effect = None
        self.storage.accepted = lambda: {}
        with self.assertRaisesRegex(TransactionError, 'changed during qualification'):
            self.qualify()
        self.assertFalse((self.base / 'rollout-ready.json').exists())


if __name__ == '__main__':
    unittest.main()
