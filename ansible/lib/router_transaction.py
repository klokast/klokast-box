"""Router-specific durable cutover order and current-state rollback.

The dom0 adapter owns authority checks, exact resource identities, atomic private
records, native copying, bounded Xen calls and boot recovery. This module never
selects a path, mounts a filesystem, runs a shell, or grants authority. Every
adapter action receives the fixed monotonic deadline for its budget.
"""
import re


class TransactionError(RuntimeError):
    pass


PHASES = frozenset(('armed', 'stopping-old', 'copying-forward', 'candidate-ready',
    'starting-candidate', 'checking-candidate', 'awaiting-enrollment',
    'stopping-candidate-for-finalization', 'finalizing-candidate',
    'restarting-candidate', 'checking-final-candidate', 'awaiting-acceptance', 'committing',
    'accepted', 'fencing-candidate', 'fencing-old', 'copying-reverse', 'old-ready',
    'starting-old', 'checking-old', 'rolled-back', 'recovery-failed'))


def validate(request):
    fields = {'kind', 'role', 'box', 'operation_id', 'engine_commit', 'policy_sha256',
              'accepted_sha256', 'old_sha256', 'candidate_sha256', 'cutover_seconds', 'recovery_seconds'}
    if (not isinstance(request, dict) or set(request) != fields or
            request['kind'] != 'klokast.router-transaction-request.v1' or request['role'] != 'router' or
            not isinstance(request['box'], str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', request['box']) or
            not isinstance(request['operation_id'], str) or not re.fullmatch('[0-9a-f]{24}', request['operation_id']) or
            not isinstance(request['engine_commit'], str) or not re.fullmatch('[0-9a-f]{40}', request['engine_commit']) or
            any(not isinstance(request[k], str) or not re.fullmatch('[0-9a-f]{64}', request[k]) for k in
                ('policy_sha256', 'accepted_sha256', 'old_sha256', 'candidate_sha256')) or
            request['old_sha256'] == request['candidate_sha256'] or
            any(type(request[k]) is not int or not 60 <= request[k] <= 1800 for k in ('cutover_seconds', 'recovery_seconds'))):
        raise TransactionError('router transaction requires exact generation, policy, engine, and bounded budgets')


def validate_pending(record, request):
    validate(request)
    if (not isinstance(record, dict) or set(record) != {'kind', 'request', 'phase', 'candidate_started', 'old_started', 'reason'} or
            record['kind'] != 'klokast.router-pending.v1' or record['request'] != request or
            record['phase'] not in PHASES or type(record['candidate_started']) is not bool or
            type(record['old_started']) is not bool or
            record['reason'] not in ('cutover', 'deadline-or-check', 'boot-recovery', 'recovery-action-failed', 'fencing-unconfirmed') or
            record['phase'] in ('armed', 'stopping-old', 'copying-forward', 'candidate-ready') and record['candidate_started'] or
            record['phase'] in ('starting-candidate', 'checking-candidate', 'awaiting-enrollment',
                                'stopping-candidate-for-finalization', 'finalizing-candidate',
                                'restarting-candidate', 'checking-final-candidate',
                                'awaiting-acceptance', 'committing', 'accepted',
                                'copying-reverse') and not record['candidate_started'] or
            record['old_started'] and record['phase'] not in ('fencing-candidate', 'old-ready', 'starting-old',
                'checking-old', 'rolled-back', 'recovery-failed')):
        raise TransactionError('router pending record is incomplete or contradicts its production-start marker')


class Transaction:
    """One bounded command. The adapter's persist operation must flush its record."""
    def __init__(self, request, adapter, pending=None):
        validate(request)
        self.request, self.adapter = request, adapter
        self.pending = pending
        if pending is not None:
            validate_pending(pending, request)

    def checkpoint(self, phase, *, started=None, old_started=None, reason=None):
        previous = self.pending or {'candidate_started': False, 'old_started': False, 'reason': 'cutover'}
        self.pending = {'kind': 'klokast.router-pending.v1', 'request': self.request,
            'phase': phase, 'candidate_started': previous['candidate_started'] if started is None else started,
            'old_started': previous['old_started'] if old_started is None else old_started,
            'reason': previous['reason'] if reason is None else reason}
        validate_pending(self.pending, self.request)
        self.adapter.persist(self.pending)

    def action(self, name, deadline, *arguments):
        if self.adapter.monotonic() >= deadline:
            raise TransactionError('router transaction exhausted its fixed action budget')
        result = getattr(self.adapter, name)(*arguments, deadline=deadline)
        if self.adapter.monotonic() >= deadline:
            raise TransactionError('router transaction action exceeded its fixed deadline')
        return result

    def cutover(self):
        if self.pending is not None:
            raise TransactionError('an existing router operation must use recovery, never restart cutover')
        # Includes approval, accepted identity, offline candidate checks,
        # rollback compatibility and reconstruction inputs. No candidate router
        # boot or pending record before this. Full service checks follow cutover.
        self.adapter.verify_prepared(self.request)
        deadline = self.adapter.monotonic() + self.request['cutover_seconds']
        try:
            self.checkpoint('armed')
            self.action('arm', deadline)
            self.checkpoint('stopping-old')
            self.action('stop', deadline, 'old')
            self.checkpoint('copying-forward')
            self.action('copy', deadline, 'old', 'candidate')
            self.checkpoint('candidate-ready')
            self.action('verify_copy', deadline, 'old', 'candidate')
            # This marker precedes xl create, including when creation returns an
            # error. An uncertain start always uses the candidate as latest state.
            self.checkpoint('starting-candidate', started=True)
            self.action('start', deadline, 'candidate')
            self.checkpoint('checking-candidate')
            self.action('check_local', deadline, 'candidate')
            self.checkpoint('awaiting-enrollment')
            if self.action('wait_enrollment', deadline) is not True:
                raise TransactionError('controller did not enroll the exact candidate before its deadline')
            self.checkpoint('stopping-candidate-for-finalization')
            self.action('stop', deadline, 'candidate')
            self.checkpoint('finalizing-candidate')
            self.action('finalize_candidate', deadline)
            self.checkpoint('restarting-candidate')
            self.action('start', deadline, 'candidate')
            self.checkpoint('checking-final-candidate')
            self.action('check_local', deadline, 'candidate')
            self.checkpoint('awaiting-acceptance')
            if self.action('wait_acceptance', deadline) is not True:
                raise TransactionError('controller did not accept the exact candidate before its deadline')
            self.checkpoint('committing')
            # The accepted pointer is atomic and durable. Boot recovery checks it
            # before deciding to copy state or boot either recorded generation.
            self.action('commit', deadline, 'candidate')
            self.checkpoint('accepted')
            self.action('disarm', deadline, 'candidate')
            self.adapter.finish('accepted')
            return 'accepted'
        except Exception:
            return self.recover(reason='deadline-or-check')

    def recover(self, *, reason='boot-recovery'):
        if self.pending is None:
            raise TransactionError('router recovery requires a protected pending operation')
        validate_pending(self.pending, self.request)
        if reason not in ('boot-recovery', 'deadline-or-check'):
            raise TransactionError('router recovery has an unsupported reason')
        if self.pending['phase'] == 'recovery-failed':
            raise TransactionError('router recovery previously failed; reconcile its fenced generations first')
        # Recovery gets its own complete budget, independent of candidate checks.
        deadline = self.adapter.monotonic() + self.request['recovery_seconds']
        try:
            self.action('verify_recovery', deadline, self.request)
            committed = self.action('committed', deadline)
            if type(committed) is not bool:
                raise TransactionError('router accepted assignment could not be established')
            if committed:
                self.checkpoint('accepted', started=True, reason=reason)
                self.action('stop', deadline, 'old')
                self.action('start', deadline, 'candidate')
                self.action('check_local', deadline, 'candidate')
                self.action('disarm', deadline, 'candidate')
                self.adapter.finish('accepted')
                return 'accepted'
            self.checkpoint('fencing-candidate', reason=reason)
            self.action('stop', deadline, 'candidate')
            if self.pending['candidate_started'] and not self.pending['old_started']:
                self.checkpoint('fencing-old')
                self.action('stop', deadline, 'old')
                self.checkpoint('copying-reverse')
                self.action('copy', deadline, 'candidate', 'old')
                self.action('verify_copy', deadline, 'candidate', 'old')
            self.checkpoint('old-ready')
            # Once rollback has booted the old OS, it can renew leases or keys.
            # A later reboot must not copy the now-older candidate state again.
            self.checkpoint('starting-old', old_started=True)
            self.action('start', deadline, 'old')
            self.checkpoint('checking-old')
            self.action('check_local', deadline, 'old')
            self.checkpoint('rolled-back')
            self.action('disarm', deadline, 'old')
            self.adapter.finish('rolled-back')
            return 'rolled-back'
        except Exception:
            # Fencing has a separate small stop reserve. Its adapter must retain
            # disabled autostart even if a live domain cannot be confirmed stopped.
            stop_deadline = self.adapter.monotonic() + 30
            fenced = False
            try:
                self.action('fence_all', stop_deadline)
                fenced = True
            except Exception:
                pass
            self.checkpoint('recovery-failed', reason='recovery-action-failed' if fenced else 'fencing-unconfirmed')
            message = ('both generations are fenced' if fenced else 'fencing is unconfirmed; use console recovery')
            raise TransactionError('router recovery failed; ' + message + '; inspect private evidence') from None
