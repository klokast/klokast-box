"""Versioned root command and bounded worker supervision for router updates."""
import argparse
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from router_copy_native import Copy
from router_dom0 import Adapter, acceptance
import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import Transaction, TransactionError

HELPER = '/usr/local/sbin/router-update-transaction'


def adapter(storage, operation):
    return Adapter(storage, operation, Copy())


def map_status(storage):
    """Project validated records only; never expose retained-state receipt contents."""
    pending = storage.pending()
    path = storage.base / 'accepted.json'
    accepted = storage.accepted() if path.exists() or path.is_symlink() else None
    if pending:
        if accepted is None:
            raise TransactionError('router map has a pending operation without an accepted assignment')
        storage.committed(pending['request'])
    def generation(checksum):
        if checksum is None:
            return None
        value = storage.generation(checksum)
        return {'generation_id':value['generation_id'], 'origin':value['origin'],
                'kernel_release':value['kernel_release'], 'record_sha256':checksum}
    result = {'kind':'klokast.router-map.v1', 'box':storage.box,
              'current':generation(accepted['current_sha256']) if accepted else None,
              'previous':generation(accepted['previous_sha256']) if accepted else None,
              'pending':None, 'state_copy':None}
    if pending:
        result['pending'] = {key:pending[key] for key in ('phase','candidate_started','old_started')}
        result['pending'].update(operation_id=pending['request']['operation_id'],
            old=generation(pending['request']['old_sha256']),
            candidate=generation(pending['request']['candidate_sha256']))
    operation = pending['request']['operation_id'] if pending else (
        accepted['operation_id'] if accepted and accepted['previous_sha256'] else None)
    if operation:
        host = adapter(storage, operation)
        if pending and host.request != pending['request'] or not pending and (
                host.request['candidate_sha256'] != accepted['current_sha256'] or
                host.request['old_sha256'] != accepted['previous_sha256']):
            raise TransactionError('router map operation differs from its protected pointers')
        if not pending and not storage.committed(host.request):
            raise TransactionError('router map accepted operation has no matching committed assignment')
        copies = {}
        for phase in ('forward','reverse'):
            try:
                for kind in ('result','private'):
                    slot = host.work / 'copy' / (phase + '.' + kind + '.slot')
                    records.parents(slot)
                    records.secure(slot, maximum=1024 * 1024)
                host.copy_backend.verify_receipt(host, phase)
            except FileNotFoundError:
                copies[phase] = 'absent'
            except (OSError, ValueError, TypeError, KeyError, TransactionError):
                copies[phase] = 'unverified'
            else:
                copies[phase] = 'complete'
        result['state_copy'] = {'operation_id':operation, **copies}
    # Do not join records across concurrent pointer publication.
    if storage.pending() != pending or (storage.accepted() if path.exists() or path.is_symlink() else None) != accepted:
        raise TransactionError('router map pointers changed during collection; collect fresh evidence')
    return result


def recover(storage, engine):
    pending = storage.pending()
    if pending is None:
        return 'no-pending-operation'
    if pending['request']['engine_commit'] != engine:
        raise TransactionError('router recovery must use the pending operation engine')
    host = adapter(storage, pending['request']['operation_id'])
    return Transaction(host.request, host, pending).recover()


def boot_assignment(storage):
    path = storage.base / 'accepted.json'
    if not path.exists() and not path.is_symlink():
        return 'unadopted'
    assignment = storage.accepted()
    generation = storage.generation(assignment['current_sha256'])
    host = native.Native()
    deadline = host.monotonic() + 120
    host.guard(storage.box, deadline=deadline)
    host.disk(generation['disk'], deadline=deadline)
    for item in generation['boot'].values():
        host.artifact(item, deadline=deadline)
    expected = native.literal_configuration(generations.configuration(generation))
    actual = native.literal_configuration(records.secure(Path('/etc/xen/router.cfg')).read_text())
    if generation['origin'] == 'legacy' and 'uuid' not in actual:
        actual['uuid'] = expected['uuid']
    if actual != expected:
        raise TransactionError('router boot definition differs from its accepted generation')
    link = Path('/etc/xen/auto/router.cfg')
    records.parents(link)
    if (not link.is_symlink() or link.lstat().st_uid != 0 or
            os.readlink(link) not in ('../router.cfg', '/etc/xen/router.cfg')):
        raise TransactionError('accepted router has no exact managed autostart link')
    # Reject an unexpected running router or another VM holding this disk.
    host.guest({'accepted': generation}, deadline=deadline)
    return 'accepted-assignment-verified'


def supervise(storage, operation, engine):
    """One local supervisor, bounded by the operation budgets; not a daemon.

    The worker owns the transaction lock. If it exits or is killed while an
    operation is pending, this parent obtains that lock and runs recovery.
    Ordinary controller/SSH loss cannot stop this local async command.
    """
    work = storage.operation(operation)
    request = records.read(work / 'request.json')
    from router_transaction import validate
    validate(request)
    if request['engine_commit'] != engine or request['box'] != storage.box or request['operation_id'] != operation:
        raise TransactionError('router supervisor engine or operation differs from its protected request')
    # O_EXCL prevents two supervisors from restarting the same operation.
    log = work / 'worker.log'
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        process = subprocess.Popen(['/usr/bin/python3', HELPER, 'worker', '--box', storage.box,
                                    '--operation-id', operation], stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=stream, start_new_session=True, close_fds=True)
    limit = 120 + request['cutover_seconds'] + request['recovery_seconds'] + 60
    result = wait_worker(process, limit)
    with storage.lock():
        pending = storage.pending()
        if pending is not None:
            if pending['request'] != request:
                raise TransactionError('another router operation is pending after worker exit')
            return recover(storage, engine)
        complete = work / 'complete.json'
        if complete.exists():
            value = records.read(complete)
            from router_transaction import validate_pending
            validate_pending(value, request)
            outcome = value['phase']
            if outcome not in ('accepted', 'rolled-back') or storage.committed(request) != (outcome == 'accepted'):
                raise TransactionError('router worker completion differs from the accepted assignment')
            return outcome
        raise TransactionError('router worker exited before arming; inspect the private worker log (exit ' + str(result) + ')')


def wait_worker(process, seconds):
    """Fence all worker children before reaping its PID or starting recovery."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        # WNOWAIT keeps the leader PID reserved after exit. Its process group
        # cannot be mistaken for a reused PID while orphaned xl commands stop.
        state = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if state is not None:
            break
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return process.wait(timeout=5)


def main(argv, engine):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check-storage', 'assignment-status', 'map-status', 'prepare-copy', 'run',
        'worker', 'recover', 'boot-recover', 'accept'))
    parser.add_argument('--box', required=True)
    parser.add_argument('--operation-id')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC %(levelname)s %(message)s')
    logging.Formatter.converter = time.gmtime
    storage = records.Records(args.box)
    native.Native().guard(args.box, deadline=time.monotonic() + 30)
    if args.action == 'check-storage':
        result = 'persistent-storage-verified'
    elif args.action == 'map-status':
        result = map_status(storage)
    elif args.action == 'assignment-status':
        pending = storage.pending()
        path = storage.base / 'accepted.json'
        accepted = storage.accepted() if path.exists() or path.is_symlink() else None
        result = {'adopted': accepted is not None, 'pending': pending is not None,
                  'assignment': accepted, 'operation': pending}
    elif args.action == 'run':
        result = supervise(storage, args.operation_id, engine)
    elif args.action == 'accept':
        # This command deliberately does not take the worker's lock. The
        # controller stages an exact proof only after full service verification.
        pending = storage.pending()
        if (pending is None or pending['phase'] != 'awaiting-acceptance' or
                pending['request']['operation_id'] != args.operation_id or pending['request']['engine_commit'] != engine):
            raise TransactionError('router is not awaiting controller acceptance for this engine and operation')
        work = storage.operation(args.operation_id)
        proof = acceptance(records.read(work / 'controller-proof.json'), pending['request'])
        if (work / 'acceptance.json').exists() or (work / 'acceptance.json').is_symlink():
            raise TransactionError('router controller acceptance was already published')
        records.write(work / 'acceptance.json', proof)
        result = 'controller-acceptance-published'
    else:
        with storage.lock():
            if args.action in ('boot-recover', 'recover'):
                result = recover(storage, engine)
                if result == 'no-pending-operation':
                    result = boot_assignment(storage)
            else:
                host = adapter(storage, args.operation_id)
                if host.request['engine_commit'] != engine:
                    raise TransactionError('router command must use its exact staged engine')
                if args.action == 'prepare-copy':
                    if storage.pending() is not None:
                        raise TransactionError('cannot prepare a router copy during a pending operation')
                    host.copy_backend.prepare(host, deadline=time.monotonic() + 180)
                    result = 'copy-capsule-preallocated'
                else:
                    result = Transaction(host.request, host).cutover()
    print(json.dumps({'kind':'klokast.router-command-result.v1', 'box':args.box, 'action':args.action,
                      'engine_commit':engine, 'result':result}, sort_keys=True))
    return 0
