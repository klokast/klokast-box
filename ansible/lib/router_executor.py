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


def accepted_manifest(storage):
    """Expose only the installed package and file identities of the current generation."""
    if storage.pending() is not None:
        raise TransactionError('router accepted manifest is unavailable during a pending operation')
    assignment = storage.accepted()
    current = storage.generation(assignment['current_sha256'])
    return {'kind':'klokast.router-accepted-manifest.v1', 'box':storage.box,
            'generation_sha256':current['record_sha256'], 'kernel_release':current['kernel_release'],
            'origin':current['origin'], 'tailscale':current.get('tailscale'),
            'packages':current['packages'], 'configuration_files':current['configuration_files']}


def accepted_source(storage):
    """Read one protected current generation for controller check input."""
    if storage.pending() is not None:
        raise TransactionError('router check source is unavailable during a pending operation')
    assignment = storage.accepted()
    return {'kind':'klokast.router-accepted-source.v1', 'box':storage.box,
            'assignment':assignment, 'generation':storage.generation(assignment['current_sha256'])}


def recover(storage, engine):
    pending = storage.pending()
    if pending is None:
        return 'no-pending-operation'
    if pending['request']['engine_commit'] != engine:
        raise TransactionError('router recovery must use the pending operation engine')
    host = adapter(storage, pending['request']['operation_id'])
    return Transaction(host.request, host, pending).recover()


def boot_assignment(storage, *, require_running=False, recover_initial=False,
                    xen=Path('/etc/xen')):
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
    config = xen / 'router.cfg'
    link = xen / 'auto/router.cfg'
    records.parents(link)
    missing_config = not config.exists() and not config.is_symlink()
    missing_link = not link.exists() and not link.is_symlink()
    initial = assignment['policy_sha256'] == records.INITIAL_AUTHORITY_SHA256
    if recover_initial and initial and (missing_config or missing_link):
        if not missing_config and native.literal_configuration(records.secure(config).read_text()) != expected:
            raise TransactionError('accepted first router Xen definition changed before recovery')
        if not missing_link and (not link.is_symlink() or link.lstat().st_uid != records.ROOT_UID or
                os.readlink(link) != '../router.cfg'):
            raise TransactionError('accepted first router autostart link changed before recovery')
        # The verified installation and accepted pointer select one generation.
        # This path only finishes their interrupted Xen persistence before
        # xendomains starts; it cannot adopt an unaccepted disk.
        host.guest({'accepted':generation}, deadline=deadline)
        if missing_config:
            records.atomic(config,generations.configuration(generation).encode())
        if missing_link:
            link.symlink_to('../router.cfg')
            records.syncdir(link.parent)
        native.command(['/usr/sbin/lbu','commit','-d'],deadline,maximum_seconds=120)
    actual = native.literal_configuration(records.secure(config).read_text())
    if generation['origin'] == 'legacy' and 'uuid' not in actual:
        actual['uuid'] = expected['uuid']
    if actual != expected:
        raise TransactionError('router boot definition differs from its accepted generation')
    if (not link.is_symlink() or link.lstat().st_uid != records.ROOT_UID or
            os.readlink(link) not in ('../router.cfg', '/etc/xen/router.cfg')):
        raise TransactionError('accepted router has no exact managed autostart link')
    # Reject an unexpected running router or another VM holding this disk.
    live = host.guest({'accepted': generation}, deadline=deadline)
    if require_running and (live is None or live[0] != 'accepted'):
        raise TransactionError('accepted router generation is not running')
    return 'accepted-assignment-verified'


def baseline_grant(value, record, engine, now):
    if (not isinstance(value, dict) or set(value) != {'kind', 'box', 'operation_id',
            'engine_commit', 'generation_sha256', 'inspection_sha256', 'granted_at', 'expires_at'} or
            value['kind'] != 'klokast.router-baseline-grant.v1' or
            value['box'] != record['box'] or value['operation_id'] != record['generation_id'] or
            value['engine_commit'] != engine or value['generation_sha256'] != record['record_sha256'] or
            value['inspection_sha256'] != record['evidence_sha256'] or
            type(value['granted_at']) is not int or type(value['expires_at']) is not int or
            not value['granted_at'] <= now < value['expires_at'] <= value['granted_at'] + 300):
        raise TransactionError('supervised router baseline grant is stale or targets different inspection evidence')
    return value


def adopt_baseline(storage, operation, engine, *, xen=Path('/etc/xen')):
    if not generations.matches('[0-9a-f]{24}', operation):
        raise TransactionError('router baseline adoption needs one exact operation')
    work = storage.operation(operation)
    record = generations.generation(records.read(work / 'generation.json'), storage.box)
    if record['origin'] != 'legacy' or record['generation_id'] != operation or record['engine_commit'] != engine:
        raise TransactionError('router baseline record differs from this operation or activated engine')
    grant = baseline_grant(records.read(work / 'authorization.json'), record, engine, time.time())
    if storage.pending() is not None or (storage.base / 'accepted.json').exists() or (storage.base / 'accepted.json').is_symlink():
        raise TransactionError('router baseline adoption requires no accepted or pending generation')
    host = native.Native()
    deadline = host.monotonic() + 90
    host.disk(record['disk'], deadline=deadline)
    for item in record['boot'].values():
        host.artifact(item, deadline=deadline)
    xen = Path(xen)
    expected = native.literal_configuration(generations.configuration(record))
    actual = native.literal_configuration(records.secure(xen / 'router.cfg').read_text())
    if 'uuid' not in actual:
        actual['uuid'] = expected['uuid']
    if actual != expected:
        raise TransactionError('legacy router Xen definition changed after supervised inspection')
    link = xen / 'auto/router.cfg'
    records.parents(link)
    if (not link.is_symlink() or link.lstat().st_uid != records.ROOT_UID or
            os.readlink(link) not in ('../router.cfg', str(xen / 'router.cfg'))):
        raise TransactionError('legacy router autostart link differs from managed boot state')
    live = host.guest({'legacy':record}, deadline=deadline)
    if live is None or live[0] != 'legacy':
        raise TransactionError('legacy router is not running with its exact inspected Xen identity')
    baseline_grant(grant, record, engine, time.time())
    accepted = storage.adopt(record)
    records.write(work / 'adoption.json', {'kind':'klokast.router-baseline-adoption.v1',
        'generation_sha256':record['record_sha256'], 'assignment_sha256':accepted['record_sha256']})
    return {'status':'baseline-adopted', 'generation_sha256':record['record_sha256'],
            'assignment_sha256':accepted['record_sha256']}


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
    parser.add_argument('action', choices=('check-storage', 'assignment-status', 'map-status', 'accepted-manifest', 'accepted-source',
        'verify-boot-assignment', 'adopt-baseline', 'prepare-copy', 'run', 'worker', 'recover', 'boot-recover', 'accept'))
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
    elif args.action == 'accepted-manifest':
        with storage.lock():
            result = accepted_manifest(storage)
    elif args.action == 'accepted-source':
        with storage.lock():
            result = accepted_source(storage)
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
            if args.action == 'adopt-baseline':
                result = adopt_baseline(storage, args.operation_id, engine)
            elif args.action == 'verify-boot-assignment':
                if storage.pending() is not None:
                    raise TransactionError('cannot verify a router provisioning rerun during a pending operation')
                if not (storage.base / 'accepted.json').exists():
                    raise TransactionError('router provisioning rerun has no accepted assignment')
                result = boot_assignment(storage, require_running=True)
            elif args.action in ('boot-recover', 'recover'):
                result = recover(storage, engine)
                if result == 'no-pending-operation':
                    result = boot_assignment(storage,recover_initial=args.action == 'boot-recover')
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
