"""Crash boundaries must never boot partial or stale router state."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from router_transaction import Transaction, TransactionError, validate_pending


class PowerLoss(BaseException):
    pass


def request():
    return {'kind': 'klokast.router-transaction-request.v1', 'role': 'router', 'box': 'boxa',
            'operation_id': 'a' * 24, 'engine_commit': 'b' * 40, 'policy_sha256': 'c' * 64,
            'accepted_sha256': 'd' * 64, 'old_sha256': 'e' * 64, 'candidate_sha256': 'f' * 64,
            'cutover_seconds': 180, 'recovery_seconds': 120}


class Adapter:
    def __init__(self, *, accept=True, crash_phase=None, crash_action=None, copy_failure=False):
        self.time = 1000
        self.accept = accept
        self.crash_phase, self.crash_action = crash_phase, crash_action
        self.copy_failure = copy_failure
        self.pending = None
        self.accepted = False
        self.live = {'old'}
        self.autostart = 'old'
        self.state = {'old': 'original-key-and-lease', 'candidate': None}
        self.receipts = set()
        self.events = []
        self.finished = None
        self.acceptance_deadlines = []

    def monotonic(self):
        return self.time

    def event(self, name):
        self.events.append(name)
        assert len(self.live) <= 1, 'two router identities are live'
        if name == self.crash_action:
            self.crash_action = None
            raise PowerLoss()

    def persist(self, record):
        self.pending = copy.deepcopy(record)
        self.event('persist:' + record['phase'])
        if record['phase'] == self.crash_phase:
            self.crash_phase = None
            raise PowerLoss()

    def verify_prepared(self, value):
        self.event('verified-prepared')

    def arm(self, *, deadline):
        self.autostart = None
        self.event('armed')

    def stop(self, generation, *, deadline):
        self.live.discard(generation)
        self.event('stopped:' + generation)

    def copy(self, source, target, *, deadline):
        assert not self.live, 'a router is live during copying'
        self.state[target] = 'partial'
        self.event('partial:' + source + ':' + target)
        if self.copy_failure and source == 'candidate':
            raise TransactionError('synthetic latest-state corruption')
        assert self.state[source] not in (None, 'partial')
        self.state[target] = self.state[source]
        self.receipts.add((source, target))
        self.event('copied:' + source + ':' + target)

    def verify_copy(self, source, target, *, deadline):
        assert (source, target) in self.receipts
        assert self.state[source] == self.state[target]
        self.event('verified-copy:' + source + ':' + target)

    def start(self, generation, *, deadline):
        assert self.state[generation] not in (None, 'partial')
        assert not self.live - {generation}
        if generation == 'candidate':
            assert self.pending['candidate_started']
            self.state[generation] = 'latest-key-and-lease'
        self.live.add(generation)
        self.event('started:' + generation)

    def check_local(self, generation, *, deadline):
        assert self.live == {generation}
        self.event('checked:' + generation)

    def wait_acceptance(self, *, deadline):
        self.acceptance_deadlines.append(deadline)
        self.event('wait-acceptance')
        if not self.accept:
            self.time = deadline
        return self.accept

    def commit(self, generation, *, deadline):
        assert generation == 'candidate' and self.live == {'candidate'}
        self.accepted = True
        self.event('committed')

    def committed(self, *, deadline):
        return self.accepted

    def verify_recovery(self, value, *, deadline):
        self.event('verified-recovery')

    def disarm(self, generation, *, deadline):
        self.autostart = generation
        self.event('disarmed:' + generation)

    def finish(self, result):
        self.finished = result
        self.event('finished:' + result)

    def fence_all(self, *, deadline):
        self.live.clear()
        self.autostart = None
        self.event('fenced-all')

    def power_cycle(self):
        # Boot recovery runs before ordinary Xen autostart.
        self.live.clear()
        self.receipts = set(self.receipts)


class TransactionTests(unittest.TestCase):
    def test_acceptance_preserves_one_live_generation_and_selects_autostart(self):
        host = Adapter()
        self.assertEqual(Transaction(request(), host).cutover(), 'accepted')
        self.assertEqual(host.live, {'candidate'})
        self.assertEqual(host.autostart, 'candidate')
        self.assertLess(host.events.index('persist:starting-candidate'), host.events.index('started:candidate'))
        self.assertLess(host.events.index('verified-copy:old:candidate'), host.events.index('started:candidate'))

    def test_no_acceptance_uses_latest_state_and_separate_recovery_budget(self):
        host = Adapter(accept=False)
        self.assertEqual(Transaction(request(), host).cutover(), 'rolled-back')
        self.assertEqual(host.live, {'old'})
        self.assertEqual(host.state['old'], 'latest-key-and-lease')
        self.assertEqual(host.acceptance_deadlines, [1180])
        self.assertEqual(host.autostart, 'old')

    def test_power_loss_at_every_cutover_record_recovers_without_guessing(self):
        phases = ('armed', 'stopping-old', 'copying-forward', 'candidate-ready', 'starting-candidate',
                  'checking-candidate', 'awaiting-acceptance', 'committing', 'accepted')
        for phase in phases:
            with self.subTest(phase=phase):
                host = Adapter(crash_phase=phase)
                with self.assertRaises(PowerLoss):
                    Transaction(request(), host).cutover()
                host.power_cycle()
                outcome = Transaction(request(), host, host.pending).recover()
                selected = 'candidate' if host.accepted else 'old'
                self.assertEqual(host.live, {selected})
                self.assertEqual(outcome, 'accepted' if host.accepted else 'rolled-back')
                self.assertNotEqual(host.state[selected], 'partial')
                if phase in ('checking-candidate', 'awaiting-acceptance', 'committing'):
                    self.assertEqual(host.state['old'], 'latest-key-and-lease')

    def test_power_loss_during_forward_copy_keeps_original_state(self):
        host = Adapter(crash_action='partial:old:candidate')
        with self.assertRaises(PowerLoss):
            Transaction(request(), host).cutover()
        host.power_cycle()
        self.assertEqual(Transaction(request(), host, host.pending).recover(), 'rolled-back')
        self.assertEqual(host.state['old'], 'original-key-and-lease')
        self.assertNotIn('started:candidate', host.events)

    def test_power_loss_during_reverse_copy_repeats_from_latest_candidate(self):
        host = Adapter(accept=False, crash_action='partial:candidate:old')
        with self.assertRaises(PowerLoss):
            Transaction(request(), host).cutover()
        self.assertEqual(host.state['old'], 'partial')
        host.power_cycle()
        self.assertEqual(Transaction(request(), host, host.pending).recover(), 'rolled-back')
        self.assertEqual(host.state['old'], 'latest-key-and-lease')
        self.assertEqual(host.live, {'old'})

    def test_commit_crash_selects_candidate_despite_pending_committing_record(self):
        host = Adapter(crash_action='committed')
        with self.assertRaises(PowerLoss):
            Transaction(request(), host).cutover()
        self.assertEqual(host.pending['phase'], 'committing')
        host.power_cycle()
        self.assertEqual(Transaction(request(), host, host.pending).recover(), 'accepted')
        self.assertNotIn('copied:candidate:old', host.events)

    def test_power_loss_after_old_restart_preserves_its_latest_writes(self):
        host = Adapter(accept=False, crash_action='started:old')
        with self.assertRaises(PowerLoss):
            Transaction(request(), host).cutover()
        self.assertTrue(host.pending['old_started'])
        host.state['old'] = 'new-writes-after-rollback-boot'
        previous_copies = host.events.count('copied:candidate:old')
        host.power_cycle()
        self.assertEqual(Transaction(request(), host, host.pending).recover(), 'rolled-back')
        self.assertEqual(host.state['old'], 'new-writes-after-rollback-boot')
        self.assertEqual(host.events.count('copied:candidate:old'), previous_copies)

    def test_unreadable_latest_state_fences_both_and_never_claims_rollback(self):
        host = Adapter(accept=False, copy_failure=True)
        with self.assertRaisesRegex(TransactionError, 'both generations are fenced'):
            Transaction(request(), host).cutover()
        self.assertEqual(host.live, set())
        self.assertIsNone(host.autostart)
        self.assertEqual(host.pending['phase'], 'recovery-failed')
        self.assertIsNone(host.finished)
        self.assertNotIn('started:old', host.events)
        with self.assertRaisesRegex(TransactionError, 'previously failed'):
            Transaction(request(), host, host.pending).recover()

    def test_wrong_role_equal_generations_and_bad_budget_refuse_before_adapter(self):
        for field, wrong in (('role','dmz'), ('candidate_sha256','e'*64), ('cutover_seconds',True),
                              ('recovery_seconds',0), ('operation_id','other')):
            host = Adapter()
            with self.subTest(field=field), self.assertRaises(TransactionError):
                Transaction({**request(),field:wrong}, host).cutover()
            self.assertEqual(host.events, [])

    def test_marker_contradiction_and_foreign_pending_cannot_recover(self):
        host = Adapter(crash_phase='copying-forward')
        with self.assertRaises(PowerLoss):
            Transaction(request(), host).cutover()
        invalid = copy.deepcopy(host.pending)
        invalid['candidate_started'] = True
        with self.assertRaises(TransactionError):
            validate_pending(invalid, request())
        invalid = copy.deepcopy(host.pending)
        invalid['request']['box'] = 'boxb'
        with self.assertRaises(TransactionError):
            Transaction(request(), host, invalid).recover()


if __name__ == '__main__':
    unittest.main()
