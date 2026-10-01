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
import router_candidate_disk as candidate_disk
import router_cold_backup as cold_backup
import router_cold_disk as cold_disk
import router_cold_cycle as cold_cycle
import router_cold_identity as cold_identity
import router_cold_health as cold_health
import router_cold_recovery as cold_recovery
import router_cold_supervisor as cold_supervisor
from router_dom0 import Adapter, acceptance
import router_generations as generations
import router_generation_device as devices
import router_native as native
import router_records as records
import router_replacement_enrollment as enrollment
from router_transaction import Transaction, TransactionError

HELPER = '/usr/local/sbin/router-update-transaction'


def adapter(storage, operation):
    return Adapter(storage, operation, Copy())


def stage_cutover(storage, operation, engine):
    """Install only one qualified candidate record before a cutover grant."""
    if storage.cold_test() is not None:
        raise TransactionError('router replacement must wait until the supervised first-install test is restored')
    work = storage.operation(operation)
    request = records.read(work / 'transaction-request.json')
    from router_transaction import validate
    validate(request)
    if (request['box'] != storage.box or request['operation_id'] != operation or
            request['engine_commit'] != engine or storage.pending() is not None):
        raise TransactionError('router cutover staging selects another engine, box, or pending operation')
    accepted = storage.accepted()
    if (accepted['record_sha256'] != request['accepted_sha256'] or
            accepted['current_sha256'] != request['old_sha256']):
        raise TransactionError('router cutover staging differs from the accepted A generation')
    # Preserve the prior rollback generation before a later commit replaces
    # the accepted pointer. Cleanup must not guess it from generation names.
    original = work / 'original-assignment.json'
    if original.exists() or original.is_symlink():
        if records.assignment(records.read(original), storage.box) != accepted:
            raise TransactionError('router staged original assignment changed; reconcile the exact operation')
    else:
        records.write(original, accepted)
    old = storage.generation(request['old_sha256'])
    candidate = generations.generation(records.read(work / 'proposed-generation.json'),storage.box)
    generations.pair(old,candidate,request)
    record = storage.base / 'records' / (candidate['record_sha256'] + '.json')
    if record.exists() or record.is_symlink():
        if records.read(record) != candidate:
            raise TransactionError('router candidate generation changed after staging')
    else:
        records.write(record,candidate)
    for side,value in (('old',old),('candidate',candidate)):
        path = work / (side + '.cfg')
        content = generations.configuration(value)
        if path.exists() or path.is_symlink():
            if records.secure(path).read_text() != content:
                raise TransactionError('router staged Xen configuration changed for ' + side)
        else:
            records.atomic(path,content.encode())
    host = adapter(storage,operation)
    host.verify_qualifications()
    source,_ = host.enrollment_source()
    prior = devices.read(storage,old['record_sha256'])
    if prior is None:
        if accepted['previous_sha256'] is not None:
            raise TransactionError('retained A has no protected per-generation Tailnet device')
        installation = storage.installation()
        if installation is not None and installation['machine_id'] != source['old_machine_id']:
            raise TransactionError('first router installation has a different Tailnet device')
        hostname = storage.box+'-router'
    else:
        hostname = prior['hostname']
    devices.remember(storage,old['record_sha256'],source['old_machine_id'],hostname,
                     generations.digest(source))
    return {'kind':'klokast.router-cutover-staged.v1','box':storage.box,
            'operation_id':operation,'candidate_sha256':candidate['record_sha256'],
            'readiness_sha256':generations.digest(host.ready),
            'status':'records-qualified-no-cutover'}


def map_status(storage):
    """Project validated records only; never expose retained-state receipt contents."""
    pending = storage.pending()
    path = storage.base / 'accepted.json'
    accepted = storage.accepted() if path.exists() or path.is_symlink() else None
    if pending:
        if accepted is None:
            raise TransactionError('router map has a pending operation without an accepted assignment')
        storage.committed(pending['request'])
    def generation(checksum, *, pending_candidate=False):
        if checksum is None:
            return None
        value = storage.generation(checksum)
        device = devices.read(storage,checksum)
        machine_id = device['machine_id'] if device else None
        hostname = device['hostname'] if device else None
        if device is None and pending_candidate:
            hostname = generations.tailnet_hostname(storage.box,value['generation_id'])
        if (device is None and accepted is not None and
                checksum == accepted['current_sha256'] and
                accepted['policy_sha256'] == records.INITIAL_AUTHORITY_SHA256):
            machine_id = storage.installation()['machine_id']
            hostname = storage.box+'-router'
        return {'generation_id':value['generation_id'], 'origin':value['origin'],
                'kernel_release':value['kernel_release'], 'record_sha256':checksum,
                'machine_id':machine_id,'tailnet_hostname':hostname}
    result = {'kind':'klokast.router-map.v2', 'box':storage.box,
              'current':generation(accepted['current_sha256']) if accepted else None,
              'previous':generation(accepted['previous_sha256']) if accepted else None,
              'pending':None, 'state_copy':None}
    if pending:
        result['pending'] = {key:pending[key] for key in ('phase','candidate_started','old_started')}
        result['pending'].update(operation_id=pending['request']['operation_id'],
            old=generation(pending['request']['old_sha256']),
            candidate=generation(pending['request']['candidate_sha256'],pending_candidate=True))
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


def completion_status(storage, operation, engine):
    """Read protected completed A/B evidence, never infer completion from a worker."""
    from router_transaction import validate, validate_pending
    if storage.pending() is not None or storage.cold_test() is not None:
        raise TransactionError('router completion evidence is unavailable during a pending or cold test')
    work = storage.operation(operation)
    request = records.read(work / 'transaction-request.json')
    validate(request)
    if (request['box'] != storage.box or request['operation_id'] != operation or
            request['engine_commit'] != engine):
        raise TransactionError('router completion selects another engine, box, or operation')
    complete = records.read(work / 'complete.json')
    validate_pending(complete, request)
    if (complete['phase'] not in ('accepted', 'rolled-back') or
            records.read(work / 'latest.json') != complete):
        raise TransactionError('router completion does not match its final protected phase')
    target = work / 'completion-assignment.json'
    if not target.exists() and not target.is_symlink():
        raise TransactionError('router completion has no retained assignment; older evidence cannot qualify rollout')
    completed = records.assignment(records.read(target), storage.box)
    accepted = complete['phase'] == 'accepted'
    if accepted:
        if (completed['current_sha256'] != request['candidate_sha256'] or
                completed['previous_sha256'] != request['old_sha256'] or
                any(completed[key] != request[key] for key in ('operation_id', 'engine_commit', 'policy_sha256'))):
            raise TransactionError('router completion contradicts the accepted candidate assignment')
    elif (completed['record_sha256'] != request['accepted_sha256'] or
            completed['current_sha256'] != request['old_sha256'] or complete['old_started'] is not True):
        raise TransactionError('router rollback completion lacks its restored original assignment')
    backend = adapter(storage, operation)
    backend.verify_qualifications()
    proof = None
    if accepted:
        proof = backend.acceptance_proof(records.read(work / 'acceptance.json'))
        if completed != records.accepted_candidate(request, proof['evidence_sha256']):
            raise TransactionError('router completion assignment differs from full-service acceptance')
    copy_receipts = {}
    if complete['candidate_started']:
        copy_receipts['forward'] = backend.copy_backend.verify_receipt(backend, 'forward')
        if not accepted:
            copy_receipts['reverse'] = backend.copy_backend.verify_receipt(backend, 'reverse')
        if any(not generations.matches('[0-9a-f]{64}', checksum) for checksum in copy_receipts.values()):
            raise TransactionError('router completion lacks complete native state-copy receipts')
    state_change_observed = None
    if complete['candidate_started'] and not accepted:
        lease_hashes = [backend.copy_backend.read_slot(work / 'copy' / (phase + '.private.slot'))
            ['files']['var/lib/misc/dnsmasq.leases']['sha256'] for phase in ('forward', 'reverse')]
        state_change_observed = lease_hashes[0] != lease_hashes[1]
    identities = {side: devices.read(storage, request[key]) for side, key in
                  (('old', 'old_sha256'), ('candidate', 'candidate_sha256'))}
    return generations.seal({'kind': 'klokast.router-completed-operation.v2',
        'box': storage.box, 'operation_id': operation, 'engine_commit': engine,
        'request': request, 'completion_sha256': generations.digest(complete),
        'assignment': completed, 'current_assignment': storage.accepted(),
        'outcome': complete['phase'], 'candidate_started': complete['candidate_started'],
        'old_started': complete['old_started'], 'reason': complete['reason'],
        'copy_receipts': copy_receipts, 'state_change_observed': state_change_observed, 'devices': identities,
        'acceptance_sha256': generations.digest(proof) if proof is not None else None})


def cleanup_plan(storage, operation, engine):
    """Derive exact obsolete resources from native completion; delete nothing.

    The caller holds the record lock. This plan cannot replace a fresh service
    check, device retirement, or native LV/backend checks before deletion.
    """
    completion = completion_status(storage, operation, engine)
    request = completion['request']
    current = completion['assignment']
    if completion['current_assignment'] != current:
        raise TransactionError('router cleanup must reconcile later accepted work first')
    work = storage.operation(operation)
    original_path = work / 'original-assignment.json'
    if not original_path.exists() and not original_path.is_symlink():
        raise TransactionError('router cleanup lacks its protected original assignment; do not infer old resources')
    original = records.assignment(records.read(original_path), storage.box)
    if (original['record_sha256'] != request['accepted_sha256'] or
            original['current_sha256'] != request['old_sha256']):
        raise TransactionError('router cleanup original assignment differs from its native request')
    retained = [value for value in (current['current_sha256'], current['previous_sha256']) if value]
    obsolete = (original['previous_sha256'] if completion['outcome'] == 'accepted'
                else request['candidate_sha256'])
    if obsolete in retained:
        raise TransactionError('router cleanup target is a current or previous accepted generation')
    def resource(checksum):
        generation = storage.generation(checksum)
        device = devices.read(storage, checksum)
        return {'generation': generation, 'device': device}
    keep = [resource(checksum) for checksum in retained]
    retire = [resource(obsolete)] if obsolete else []
    # Generation records are immutable, but independent records must also
    # describe independent disks and device registrations.
    resources = keep + retire
    for field in ('path', 'uuid'):
        if len({value['generation']['disk'][field] for value in resources}) != len(resources):
            raise TransactionError('router cleanup generation disks overlap retained resources')
    identities = [value['device']['machine_id'] for value in resources if value['device'] is not None]
    if len(set(identities)) != len(identities):
        raise TransactionError('router cleanup device is shared with another retained generation')
    if (completion['outcome'] == 'rolled-back' and completion['candidate_started'] and
            retire[0]['device'] is None):
        raise TransactionError('router cleanup started candidate has no protected device identity')
    plan = generations.seal({'kind': 'klokast.router-cleanup-plan.v1', 'box': storage.box,
        'operation_id': operation, 'engine_commit': engine, 'outcome': completion['outcome'],
        'completion_sha256': completion['record_sha256'],
        'assignment_sha256': current['record_sha256'],
        'original_assignment_sha256': original['record_sha256'], 'keep': keep, 'retire': retire,
        'status': 'planned-no-retirement', 'retirement_authorized': False})
    target = work / 'cleanup-plan.json'
    if target.exists() or target.is_symlink():
        if records.read(target) != plan:
            raise TransactionError('router cleanup plan changed; retain resources for reconciliation')
    else:
        records.write(target, plan)
    return plan


def cleanup_authorization(value, plan, storage, now):
    """Validate one controller-staged narrow retirement grant, not arbitrary paths."""
    fields = {'kind', 'plan_sha256', 'assignment_sha256', 'service_sha256',
              'device', 'granted_at', 'expires_at'}
    if (not isinstance(value, dict) or set(value) != fields or
            value['kind'] != 'klokast.router-cleanup-authorization.v1' or
            value['plan_sha256'] != plan['record_sha256'] or
            value['assignment_sha256'] != plan['assignment_sha256'] or
            not generations.matches('[0-9a-f]{64}', value['service_sha256']) or
            any(type(value[key]) is not int for key in ('granted_at', 'expires_at')) or
            not value['granted_at'] <= now < value['expires_at'] <= value['granted_at'] + 600):
        raise TransactionError('router cleanup grant is stale or differs from its exact plan and service check')
    receipt = value['device']
    if (not isinstance(receipt, dict) or set(receipt) !=
            {'generation_sha256', 'machine_id', 'provider_id', 'status'}):
        raise TransactionError('router cleanup lacks exact device retirement evidence')
    target = plan['retire'][0] if plan['retire'] else None
    if target is None:
        if receipt != dict.fromkeys(('generation_sha256', 'machine_id', 'provider_id'), None) | {'status': 'no-target'}:
            raise TransactionError('router cleanup empty plan has unexpected device authority')
    else:
        generation = target['generation']
        device = target['device']
        if receipt['generation_sha256'] != generation['record_sha256']:
            raise TransactionError('router cleanup device evidence selects another obsolete generation')
        if device is not None:
            if (receipt['machine_id'] != device['machine_id'] or receipt['status'] != 'absent' or
                    receipt['provider_id'] is not None and
                    not generations.matches('[A-Za-z0-9_-]{1,128}', receipt['provider_id'])):
                raise TransactionError('router cleanup has no exact revoked device absence proof')
        else:
            work = storage.operation(plan['operation_id'])
            complete = records.read(work / 'complete.json')
            if (plan['outcome'] != 'rolled-back' or complete['candidate_started'] is not False or
                    receipt['machine_id'] is not None or receipt['provider_id'] is not None or
                    receipt['status'] != 'no-device' or
                    any((work / name).exists() or (work / name).is_symlink() for name in
                        ('enrollment-attempt.json', 'enrollment-result.json', 'controller-enrollment.json'))):
                raise TransactionError('router cleanup cannot infer remote absence from missing device metadata')
    return value


def retire_completed(storage, operation, engine, token, *, host=None):
    """Retire one root-selected obsolete disk/artifacts with durable retry fences.

    The caller holds the record lock. The controller has verified current
    services and exact device absence before staging the short grant. Retain
    generation, completion and copy metadata so recovery proof remains readable.
    """
    if not generations.matches('[0-9a-f]{12}', token):
        raise TransactionError('router cleanup requires its exact short grant selector')
    plan = cleanup_plan(storage, operation, engine)
    work = storage.operation(operation)
    grant = cleanup_authorization(records.read(work / ('cleanup-grant-' + token + '.json')),
                                  plan, storage, time.time())
    host = host or native.Native()
    deadline = host.monotonic() + min(240, grant['expires_at'] - time.time())
    boot_assignment(storage, require_running=True)
    # Verify all retained disks; the previous router stays offline.
    kept = {str(index): item['generation'] for index, item in enumerate(plan['keep'])}
    live = host.guest(kept, deadline=deadline)
    if live is None or live[0] != '0':
        raise TransactionError('router cleanup has no exact running current and offline previous generation')
    target = plan['retire'][0]['generation'] if plan['retire'] else None
    progress_path, result_path = work / 'cleanup-progress.json', work / 'cleanup-complete.json'
    progress = None
    if progress_path.exists() or progress_path.is_symlink():
        progress = records.read(progress_path)
        if (not isinstance(progress, dict) or set(progress) != {'kind', 'plan_sha256', 'phase'} or
                progress['kind'] != 'klokast.router-cleanup-progress.v1' or
                progress['plan_sha256'] != plan['record_sha256'] or
                progress['phase'] not in ('disk-removing', 'disk-removed', 'boot-removing', 'complete')):
            raise TransactionError('router cleanup progress changed; preserve exact resources')
    def save(phase):
        logging.info('Router cleanup box=%s operation=%s phase=%s', storage.box, operation, phase)
        records.write(progress_path, {'kind': 'klokast.router-cleanup-progress.v1',
            'plan_sha256': plan['record_sha256'], 'phase': phase})
    def fresh():
        cleanup_authorization(grant, plan, storage, time.time())
    def row():
        rows = candidate_disk.inventory()
        if any(item['lv_uuid'] == target['disk']['uuid'] and item['lv_path'] != target['disk']['path']
               for item in rows):
            raise TransactionError('router obsolete LV UUID moved to another path; preserve it for reconciliation')
        return next((item for item in rows if item['lv_path'] == target['disk']['path']), None)
    if target is not None:
        paths = {item['path'] for value in kept.values() for item in value['boot'].values()}
        if any(item['path'] in paths for item in target['boot'].values()):
            raise TransactionError('router cleanup boot artifact is shared with a retained generation')
        observed = row()
        if observed is not None:
            if progress is not None and progress['phase'] != 'disk-removing':
                raise TransactionError('router obsolete disk reappeared after recorded removal')
            if target['origin'] == 'template':
                candidate_disk.validate_row(observed, target['generation_id'], target['disk']['uuid'])
            host.disk(target['disk'], deadline=deadline)
            host.detached([target['disk']['path']], deadline=deadline)
            for item in target['boot'].values():
                host.artifact(item, deadline=deadline)
            fresh()
            save('disk-removing')
            native.command(['/sbin/lvremove', '--yes', target['disk']['path']],
                           deadline, maximum_seconds=60)
            if row() is not None:
                raise TransactionError('router obsolete disk remains after exact retirement')
        elif progress is None:
            raise TransactionError('router obsolete disk disappeared without a protected retirement intent')
        # An interrupted lvremove is reconciled only with the durable intent.
        if progress is None or progress['phase'] in ('disk-removing', 'disk-removed'):
            save('disk-removed')
            for item in target['boot'].values():
                host.artifact(item, deadline=deadline)
            save('boot-removing')
        for item in target['boot'].values():
            path = Path(item['path'])
            records.parents(path)
            if path.exists() or path.is_symlink():
                fresh()
                host.artifact(item, deadline=deadline)
                path.unlink()
                records.syncdir(path.parent)
        if row() is not None or any(Path(item['path']).exists() or Path(item['path']).is_symlink()
                                   for item in target['boot'].values()):
            raise TransactionError('router obsolete resources remain after cleanup')
    result = generations.seal({'kind': 'klokast.router-cleanup-complete.v1',
        'box': storage.box, 'operation_id': operation, 'engine_commit': engine,
        'plan_sha256': plan['record_sha256'], 'assignment_sha256': plan['assignment_sha256'],
        'generation_sha256': target['record_sha256'] if target else None,
        'disk': target['disk'] if target else None, 'device': grant['device'],
        'status': 'exact-resources-retired'})
    if result_path.exists() or result_path.is_symlink():
        if records.read(result_path) != result:
            raise TransactionError('router cleanup completed result changed; reconcile its exact device evidence')
    else:
        records.write(result_path, result)
    save('complete')
    return result


def supervise_cleanup(storage, operation, engine, token):
    """Fence the worker's command group before collecting its retirement proof."""
    if not generations.matches('[0-9a-f]{12}', token):
        raise TransactionError('router cleanup supervisor needs its exact grant selector')
    work = storage.operation(operation)
    # The worker takes the record lock. Never hold it across Popen/wait.
    log = work / ('cleanup-worker-' + token + '.log')
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        records.syncdir(work)
        process = subprocess.Popen(['/usr/bin/python3', HELPER, 'cleanup-worker', '--box', storage.box,
            '--operation-id', operation, '--cleanup-token', token], stdin=subprocess.DEVNULL,
            stdout=stream, stderr=stream, start_new_session=True, close_fds=True)
    code = wait_worker(process, 360)
    with storage.lock():
        if not (work / 'cleanup-complete.json').exists():
            raise TransactionError('router cleanup worker has no protected completion; reconcile its exact intent (exit ' + str(code) + ')')
        # Recheck current/previous and exact resource absence after all worker
        # children have been fenced. This is the same idempotent primitive.
        return retire_completed(storage, operation, engine, token)


def rollout_pair(forward, rollback, box, engine):
    """Require a complete native update followed by a started-candidate rollback."""
    from router_transaction import validate
    expected_fields = {'kind', 'box', 'operation_id', 'engine_commit', 'request',
        'completion_sha256', 'assignment', 'current_assignment', 'outcome', 'candidate_started',
        'old_started', 'reason', 'copy_receipts', 'state_change_observed', 'devices', 'acceptance_sha256', 'record_sha256'}
    for value in (forward, rollback):
        generations.check_seal(value)
        if (set(value) != expected_fields or value['kind'] != 'klokast.router-completed-operation.v2' or
                value['box'] != box or value['engine_commit'] != engine):
            raise TransactionError('router rollout proof has different target or engine')
        validate(value['request'])
        if any(value['request'][key] != value[key] for key in ('box', 'operation_id', 'engine_commit')):
            raise TransactionError('router rollout proof selects another native request')
        records.assignment(value['assignment'], box)
        records.assignment(value['current_assignment'], box)
        if (not generations.matches('[0-9a-f]{64}', value['completion_sha256']) or
                not isinstance(value['copy_receipts'], dict) or
                any(not generations.matches('[0-9a-f]{64}', item) for item in value['copy_receipts'].values()) or
                not isinstance(value['devices'], dict) or set(value['devices']) != {'old', 'candidate'}):
            raise TransactionError('router rollout proof has incomplete native receipt or identity evidence')
        for side, key in (('old', 'old_sha256'), ('candidate', 'candidate_sha256')):
            if value['devices'][side] is None:
                raise TransactionError('router rollout proof requires enrolled distinct generations')
            devices.validate(value['devices'][side], box, value['request'][key])
    f, r = forward['request'], rollback['request']
    if (forward['outcome'] != 'accepted' or forward['candidate_started'] is not True or
            forward['old_started'] is not False or forward['state_change_observed'] is not None or
            set(forward['copy_receipts']) != {'forward'} or
            not generations.matches('[0-9a-f]{64}', forward['acceptance_sha256']) or
            rollback['outcome'] != 'rolled-back' or rollback['candidate_started'] is not True or
            rollback['old_started'] is not True or rollback['state_change_observed'] is not True or
            rollback['reason'] != 'deadline-or-check' or
            set(rollback['copy_receipts']) != {'forward', 'reverse'} or rollback['acceptance_sha256'] is not None):
        raise TransactionError('router rollout requires full acceptance and a failed started-candidate rollback')
    accepted = forward['assignment']
    if (f['operation_id'] == r['operation_id'] or f['policy_sha256'] != r['policy_sha256'] or
            accepted['current_sha256'] != f['candidate_sha256'] or accepted['previous_sha256'] != f['old_sha256'] or
            any(accepted[key] != f[key] for key in ('operation_id', 'engine_commit', 'policy_sha256')) or
            r['old_sha256'] != f['candidate_sha256'] or r['accepted_sha256'] != accepted['record_sha256'] or
            rollback['assignment'] != accepted or
            forward['current_assignment'] != accepted or rollback['current_assignment'] != accepted):
        raise TransactionError('router rollout operations do not prove the accepted generation and its latest-state restoration')
    identities = [forward['devices'][side]['machine_id'] for side in ('old', 'candidate')]
    identities.append(rollback['devices']['candidate']['machine_id'])
    if (len(set(identities)) != 3 or
            forward['devices']['candidate'] != rollback['devices']['old']):
        raise TransactionError('router rollout generations reuse or change protected Tailnet identities')
    return f['policy_sha256']


def qualify_rollout(storage, forward_operation, rollback_operation, engine):
    """Record readiness only from two checked completed native operations."""
    forward = completion_status(storage, forward_operation, engine)
    rollback = completion_status(storage, rollback_operation, engine)
    policy_sha256 = rollout_pair(forward, rollback, storage.box, engine)
    boot = native.recovery_boot_chain(engine)
    if storage.accepted() != forward['assignment']:
        raise TransactionError('router rollout accepted assignment changed during qualification')
    value = generations.seal({'kind': 'klokast.router-rollout-readiness.v1', 'box': storage.box,
        'engine_commit': engine, 'policy_sha256': policy_sha256,
        'forward': forward, 'rollback': rollback, 'boot_chain_sha256': generations.digest(boot)})
    path = storage.base / 'rollout-ready.json'
    # This record grants no operation. Native proof may be renewed, but only
    # through this checked action. Controller reports are never imported.
    records.write(path, value)
    return {'kind': value['kind'], 'box': storage.box, 'engine_commit': engine,
        'policy_sha256': policy_sha256, 'record_sha256': value['record_sha256'],
        'status': 'native-forward-and-rollback-qualified', 'replacement_authorized': False}


def rollout_status(storage, engine):
    """Recheck protected per-box proof and persisted code before scheduled use."""
    result = {'kind': 'klokast.router-rollout-status.v1', 'box': storage.box,
        'engine_commit': engine, 'ready': False, 'policy_sha256': None, 'readiness_sha256': None}
    path = storage.base / 'rollout-ready.json'
    if not path.exists() and not path.is_symlink():
        return {**result, 'reason': 'no-native-forward-and-rollback-proof'}
    if storage.pending() is not None or storage.cold_test() is not None:
        return {**result, 'reason': 'pending-or-cold-test'}
    value = records.read(path)
    generations.check_seal(value)
    if (set(value) != {'kind', 'box', 'engine_commit', 'policy_sha256', 'forward', 'rollback',
                      'boot_chain_sha256', 'record_sha256'} or
            value['kind'] != 'klokast.router-rollout-readiness.v1' or value['box'] != storage.box):
        raise TransactionError('router protected rollout readiness has an invalid contract')
    if value['engine_commit'] != engine:
        return {**result, 'reason': 'tested-engine-changed'}
    if (rollout_pair(value['forward'], value['rollback'], storage.box, engine) != value['policy_sha256'] or
            generations.digest(native.recovery_boot_chain(engine)) != value['boot_chain_sha256']):
        return {**result, 'reason': 'tested-policy-or-recovery-code-changed'}
    return {**result, 'ready': True, 'reason': 'native-forward-and-rollback-qualified',
        'policy_sha256': value['policy_sha256'], 'readiness_sha256': value['record_sha256']}


def signal_enrollment(storage, operation, engine):
    """Publish only B's checked result while the worker waits on its lock."""
    pending = storage.pending()
    if (pending is None or pending['phase'] != 'awaiting-enrollment' or
            pending['request']['operation_id'] != operation or
            pending['request']['engine_commit'] != engine):
        raise TransactionError('router is not awaiting enrollment for this engine and operation')
    work = storage.operation(operation)
    intent = enrollment.validate_attempt(records.read(work / 'enrollment-attempt.json'),pending['request'])
    value = enrollment.result(records.read(work / 'controller-enrollment.json'),pending['request'],intent)
    pair = {side:storage.generation(pending['request'][key]) for side,key in
            (('old','old_sha256'),('candidate','candidate_sha256'))}
    current = native.Native().guest(pair,deadline=time.monotonic()+30)
    if current is None or current[0] != 'candidate':
        raise TransactionError('candidate is not running for its enrollment signal')
    devices.remember(storage,pair['candidate']['record_sha256'],value['machine_id'],
                     value['hostname'],generations.digest(value))
    destination = work / 'enrollment-result.json'
    if destination.exists() or destination.is_symlink():
        if records.read(destination) != value:
            raise TransactionError('router enrollment result changed after publication')
    else:
        records.write(destination,value)
    return 'exact-enrollment-published'


def candidate_status(storage, operation, engine):
    """Report B's exact post-cleanup Xen assignment while the worker waits."""
    pending=storage.pending()
    if (pending is None or pending['phase'] not in
            ('checking-final-candidate','awaiting-acceptance') or
            pending['request']['operation_id'] != operation or
            pending['request']['engine_commit'] != engine or
            pending['candidate_started'] is not True):
        raise TransactionError('router is not awaiting final B service verification')
    request=pending['request']
    work=storage.operation(operation)
    enrolled=enrollment.result(records.read(work/'enrollment-result.json'),request,
        records.read(work/'enrollment-attempt.json'))
    finalized=records.read(work/'finalization/result.json')
    if (finalized.get('kind') != 'klokast.router-replacement-finalization-result.v1' or
            finalized.get('success') is not True or
            finalized.get('machine_id') != enrolled['machine_id']):
        raise TransactionError('router final B has no matching offline cleanup result')
    pair={side:storage.generation(request[key]) for side,key in
          (('old','old_sha256'),('candidate','candidate_sha256'))}
    host=native.Native()
    deadline=time.monotonic()+30
    host.guard(storage.box,deadline=deadline)
    current=host.guest(pair,deadline=deadline)
    if current is None or current[0] != 'candidate':
        raise TransactionError('router final B is not the exact running Xen guest')
    host.disk(pair['candidate']['disk'],deadline=deadline)
    for item in pair['candidate']['boot'].values():
        host.artifact(item,deadline=deadline)
    return {'kind':'klokast.router-replacement-candidate-status.v1',
        'box':storage.box,'operation_id':operation,
        'request_sha256':generations.digest(request),
        'candidate_sha256':request['candidate_sha256'],
        'enrollment_sha256':generations.digest(enrolled),
        'finalization_sha256':generations.digest(finalized),
        'machine_id':enrolled['machine_id'],'xen_uuid':pair['candidate']['xen']['uuid'],
        'disk':pair['candidate']['disk'],'status':'running-final'}


def provisioning_status(storage):
    """Read protected router pointers before any provisioning allocation."""
    pending = storage.pending()
    path = storage.base / 'accepted.json'
    accepted = storage.accepted() if path.exists() or path.is_symlink() else None
    initial = storage.installation()
    if pending is not None and initial is not None:
        raise TransactionError('router provisioning found overlapping replacement and first-install records')
    if accepted is not None and initial is not None and initial['stage'] != 'verified':
        raise TransactionError('accepted router has an incomplete first-install record')
    return {'kind':'klokast.router-provisioning-status.v1', 'box':storage.box,
            'assignment':accepted, 'pending':pending, 'installation':initial}


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
    request = records.read(work / 'transaction-request.json')
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


def supervise_cold(storage, operation, engine):
    """Fence a crashed cold worker and return the original without a controller."""
    if storage.box != 'k001':
        raise TransactionError('supervised cold recovery is limited to K001')
    bundle = cold_backup.Bundle(storage, operation, engine)
    cycle = cold_cycle.Cycle(bundle)
    request = cycle.request.verify()
    cold_supervisor.authorization(records.read(bundle.directory / 'outage-authorization.json'), request)
    # Keep this single-use launch record even if Popen or result collection
    # fails. An uncertain launch must not start a second worker on retry.
    log = bundle.directory / 'supervisor-worker.log'
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        records.syncdir(log.parent)
        process = subprocess.Popen(['/usr/bin/python3', HELPER, 'cold-worker',
            '--box', storage.box, '--operation-id', operation], stdin=subprocess.DEVNULL,
            stdout=stream, stderr=stream, start_new_session=True, close_fds=True)
    code = wait_worker(process, cold_cycle.WINDOW_SECONDS + 900)
    if storage.cold_test() is not None or cycle.result.exists() or cycle.result.is_symlink():
        # wait_worker has killed and reaped the entire child command group.
        # With a marker this is recovery only; it never reopens the window.
        logging.info('Cold worker exited with code %s; reconcile original return', code)
        return cycle.run()
    raise TransactionError('cold worker exited before arming; inspect its private log (exit ' + str(code) + ')')


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
    parser.add_argument('action', choices=('check-storage', 'assignment-status', 'map-status', 'accepted-manifest', 'accepted-source', 'completion-status', 'cleanup-plan', 'retire-completed', 'cleanup-worker', 'qualify-rollout', 'rollout-status', 'verify-recovery-chain',
        'provisioning-status', 'verify-boot-assignment', 'adopt-baseline', 'stage-cutover', 'prepare-copy', 'run', 'worker', 'signal-enrollment', 'candidate-status', 'recover', 'boot-recover', 'accept', 'cold-capture-metadata', 'cold-allocate-backup', 'cold-abort-prepared', 'cold-request-stage', 'cold-run', 'cold-worker', 'cold-status', 'cold-signal-return', 'cold-stage-identity', 'cold-baseline-capture', 'cold-baseline-status', 'cold-baseline-verify-restored', 'cold-health-stage', 'cold-health-clear', 'cold-test-device-status'))
    parser.add_argument('--box', required=True)
    parser.add_argument('--operation-id')
    parser.add_argument('--rollback-operation-id')
    parser.add_argument('--cleanup-token')
    args = parser.parse_args(argv)
    if args.rollback_operation_id is not None and args.action != 'qualify-rollout':
        raise TransactionError('rollback proof selector is only valid for rollout qualification')
    if (args.cleanup_token is not None) != (args.action in ('retire-completed', 'cleanup-worker')):
        raise TransactionError('cleanup grant selector is required only for exact retirement')
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
    elif args.action == 'verify-recovery-chain':
        result = native.recovery_boot_chain(engine)
    elif args.action in ('qualify-rollout', 'rollout-status'):
        with storage.lock():
            result = (qualify_rollout(storage, args.operation_id, args.rollback_operation_id, engine)
                      if args.action == 'qualify-rollout' else rollout_status(storage, engine))
    elif args.action in ('completion-status', 'cleanup-plan'):
        with storage.lock():
            result = (completion_status(storage, args.operation_id, engine)
                      if args.action == 'completion-status' else cleanup_plan(storage, args.operation_id, engine))
    elif args.action == 'retire-completed':
        result = supervise_cleanup(storage, args.operation_id, engine, args.cleanup_token)
    elif args.action == 'cleanup-worker':
        with storage.lock():
            result = retire_completed(storage, args.operation_id, engine, args.cleanup_token)
    elif args.action == 'provisioning-status':
        with storage.lock():
            result = provisioning_status(storage)
    elif args.action == 'assignment-status':
        pending = storage.pending()
        path = storage.base / 'accepted.json'
        accepted = storage.accepted() if path.exists() or path.is_symlink() else None
        result = {'adopted': accepted is not None, 'pending': pending is not None,
                  'assignment': accepted, 'operation': pending}
    elif args.action == 'run':
        result = supervise(storage, args.operation_id, engine)
    elif args.action == 'signal-enrollment':
        result = signal_enrollment(storage,args.operation_id,engine)
    elif args.action == 'candidate-status':
        result = candidate_status(storage,args.operation_id,engine)
    elif args.action == 'accept':
        # This command deliberately does not take the worker's lock. The
        # controller stages an exact proof only after full service verification.
        pending = storage.pending()
        if (pending is None or pending['phase'] != 'awaiting-acceptance' or
                pending['request']['operation_id'] != args.operation_id or pending['request']['engine_commit'] != engine):
            raise TransactionError('router is not awaiting controller acceptance for this engine and operation')
        work = storage.operation(args.operation_id)
        proof = adapter(storage,args.operation_id).acceptance_proof(
            records.read(work / 'controller-proof.json'))
        if (work / 'acceptance.json').exists() or (work / 'acceptance.json').is_symlink():
            raise TransactionError('router controller acceptance was already published')
        records.write(work / 'acceptance.json', proof)
        result = 'controller-acceptance-published'
    elif args.action == 'cold-stage-identity':
        if args.box != 'k001':
            raise TransactionError('supervised cold recovery is limited to K001')
        bundle = cold_backup.Bundle(storage, args.operation_id, engine)
        result = cold_identity.Identity(bundle).stage(
            records.read(bundle.directory / 'original-identity-input.json'))
    elif args.action == 'cold-abort-prepared':
        if args.box != 'k001' or not generations.matches('[0-9a-f]{24}', args.operation_id):
            raise TransactionError('cold prepared abort needs one exact K001 operation')
        manifest = records.read(storage.base / 'cold-backups' / args.operation_id / 'manifest.json')
        generations.check_seal(manifest)
        source_engine = manifest.get('engine_commit')
        if (manifest.get('kind') != 'klokast.router-cold-metadata.v1' or
                manifest.get('box') != args.box or
                manifest.get('operation_id') != args.operation_id or
                not generations.matches('[0-9a-f]{40}', source_engine)):
            raise TransactionError('cold prepared abort has no exact protected source engine')
        bundle = cold_backup.Bundle(storage, args.operation_id, source_engine)
        result = cold_disk.DiskBackup(bundle).abort_prepared(engine)
    elif args.action in ('cold-capture-metadata', 'cold-allocate-backup', 'cold-request-stage',
                         'cold-signal-return', 'cold-run', 'cold-worker', 'cold-status'):
        if args.box != 'k001':
            raise TransactionError('supervised cold recovery is limited to K001')
        bundle = cold_backup.Bundle(storage, args.operation_id, engine)
        if args.action == 'cold-capture-metadata':
            result = bundle.capture(storage.accepted()['current_sha256'])
        elif args.action == 'cold-allocate-backup':
            result = cold_disk.DiskBackup(bundle).allocate()
        elif args.action == 'cold-signal-return':
            result = cold_cycle.Cycle(bundle).signal_return()
        elif args.action == 'cold-run':
            result = supervise_cold(storage, args.operation_id, engine)
        elif args.action == 'cold-worker':
            result = cold_cycle.Cycle(bundle).run()
        elif args.action == 'cold-status':
            result = cold_cycle.Cycle(bundle).status()
        else:
            result = cold_supervisor.Request(bundle).stage(
                records.read(bundle.directory / 'supervised-request-input.json'))
    elif args.action in ('cold-health-stage', 'cold-health-clear', 'cold-test-device-status'):
        if args.box != 'k001':
            raise TransactionError('supervised cold recovery is limited to K001')
        bundle = cold_backup.Bundle(storage, args.operation_id, engine)
        health = cold_health.Health(bundle)
        boot_check = lambda: boot_assignment(storage, require_running=True)
        if args.action == 'cold-test-device-status':
            if storage.cold_test() is not None:
                raise TransactionError('cold test device cleanup waits for completed original recovery')
            completion = health.clear_fence(boot_check)
            result = cold_test_state.TestState(bundle).cleanup_source(completion)
        else:
            result = (health.stage(boot_check) if args.action == 'cold-health-stage' else
                      health.clear_fence(boot_check))
    elif args.action in ('cold-baseline-capture', 'cold-baseline-status',
                         'cold-baseline-verify-restored'):
        if args.box != 'k001':
            raise TransactionError('supervised cold recovery is limited to K001')
        baseline = cold_recovery.Baseline(cold_backup.Bundle(storage, args.operation_id, engine))
        if args.action == 'cold-baseline-capture':
            result = baseline.capture()
        elif args.action == 'cold-baseline-verify-restored':
            result = baseline.verify_restored()
        else:
            with storage.lock():
                result = baseline.verify()
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
            elif args.action == 'stage-cutover':
                result = stage_cutover(storage,args.operation_id,engine)
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
