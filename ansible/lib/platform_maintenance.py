"""Routine development maintenance from Instance policy and native health checks."""
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import platform_source as source
import platform_maintenance_native as native
import platform_updates

ROOT = Path('/var/lib/klokast/updates/executor')
DISCOVERY = Path('/var/lib/klokast/updates/discovery')
LOCK = Path('/var/lib/klokast/updates/operation.lock')
OPERATION = re.compile(r'[0-9a-f]{24}')
HASH = re.compile(r'[0-9a-f]{64}')


def write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', prefix='.record-', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(native.canonical(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def operation_lock():
    info = LOCK.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise source.SourceError('installation lock directory is unsafe')
    fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise source.SourceError('installation lock is unsafe')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def policy_status():
    view = source.snapshot()
    policy = view['instance'].get('vm-updates')
    paused = source.read_json(ROOT / 'pause.json').get('paused') is True if (ROOT / 'pause.json').exists() else False
    context = source.digest({'instance': view['instance_sha256'], 'policy': policy, 'implementation': view['implementation']})
    return {'kind': 'klokast.vm-update-policy-source.v1', 'policy': policy,
            'policy_sha256': source.digest(policy), 'context_sha256': context,
            'engine_commit': view['implementation']['commit'],
            'private_commit': view['rendered']['repository'].get('head_commit', ''),
            'instance_sha256': view['instance_sha256'], 'paused': paused,
            'replacement_executor_available': readiness(context)}


def readiness(context):
    path = ROOT / 'replacement-ready.json'
    if not path.exists(): return False
    value = source.read_json(path)
    if value.get('context_sha256') != context: return False
    try:
        pointer = source.read_json(DISCOVERY / 'automatic.json')
        if pointer.get('operation_id') != value['candidate_id']: return False
        for box, operation in value['operations'].items():
            if source.digest(native.vm_update_recovery_evidence(box, operation)) != value['evidence'][box]: return False
    except (source.SourceError, OSError, ValueError, KeyError):
        return False
    return bool(value.get('operations'))


def schedule_status():
    value = policy_status()
    return {'kind': 'klokast.vm-update-schedule.v1', 'policy': value['policy'],
            'enabled': bool(value['policy'] and value['policy'].get('enabled') and not value['paused']),
            'replacement_ready': value['replacement_executor_available']}


def require_policy():
    value = policy_status()
    if not value['policy'] or value['policy'].get('enabled') is not True or value['paused']:
        raise source.SourceError('maintenance requires enabled Instance policy and an unpaused controller')
    return value


def release_evidence(operation, policy):
    if not OPERATION.fullmatch(operation): raise source.SourceError('invalid candidate operation ID')
    pointer = source.read_json(DISCOVERY / 'automatic.json')
    selection = pointer['selection']
    expected = [box + '-' + role for box, role in source.targets(policy['policy'])]
    if (pointer.get('operation_id') != operation or selection.get('targets') != expected or
            selection.get('context_sha256') != policy['context_sha256'] or
            selection.get('engine_commit') != policy['engine_commit']):
        raise source.SourceError('candidate is outside current Instance maintenance policy')
    root = DISCOVERY / 'builds' / operation
    inputs, candidate, release = (source.read_json(root / name) for name in ('inputs.json', 'candidate.json', 'release-evidence.json'))
    boot = candidate.get('boot_test', {})
    platform_updates.validate_no_application_release(release, inputs, candidate,
        boot.get('openrc_test', {}), boot.get('personalized_test', {}), boot.get('maintenance_restore', {}))
    if (release['release_sha256'] != pointer['release_sha256'] or release['engine_commit'] != policy['engine_commit'] or
            candidate.get('operation_id') != operation or candidate.get('box') != selection.get('build_box')):
        raise source.SourceError('candidate does not match the selected tested release')
    return {'selection': selection, 'release': release, 'build_operation_id': operation,
            'candidate_sha256': source.digest(candidate)}


def import_release(operation):
    policy = require_policy()
    evidence = release_evidence(operation, policy)
    record = {'kind': 'klokast.vm-protected-release.v1', **evidence, 'context_sha256': policy['context_sha256']}
    record['record_sha256'] = source.digest(record)
    path = ROOT / 'releases' / policy['context_sha256'] / (evidence['release']['release_sha256'] + '.json')
    existed = path.exists()
    if existed and source.read_json(path) != record: raise source.SourceError('stored candidate conflicts with current artifacts')
    if not existed: write(path, record)
    return {'kind': record['kind'], 'record_sha256': record['record_sha256'],
            'release_sha256': record['release']['release_sha256'], 'build_operation_id': operation,
            'result': 'unchanged' if existed else 'imported'}


def check_release(checksum, box):
    if not HASH.fullmatch(checksum): raise source.SourceError('invalid release checksum')
    policy = require_policy()
    record = source.read_json(ROOT / 'releases' / policy['context_sha256'] / (checksum + '.json'))
    evidence = release_evidence(record['build_operation_id'], policy)
    if any(record.get(key) != value for key, value in evidence.items()): raise source.SourceError('stored candidate has changed')
    if box not in {host.rsplit('-', 1)[0] for host in evidence['selection']['targets']}:
        raise source.SourceError('candidate target is outside current policy')
    path = DISCOVERY / 'builds' / record['build_operation_id'] / 'candidate.json'
    raw = path.read_bytes()
    expected = {'candidate.json': {'sha256': __import__('hashlib').sha256(raw).hexdigest(), 'bytes': len(raw)}, **record['release']['artifacts']}
    if native.vm_update_release_dom0_files(box, record['build_operation_id']) != expected:
        raise source.SourceError('published candidate bytes have changed')
    return {'kind': 'klokast.vm-release-availability.v1', 'box': box,
            'release_sha256': checksum, 'record_sha256': record['record_sha256'], 'available': True}


def ready(operations):
    policy = require_policy()
    pointer = source.read_json(DISCOVERY / 'automatic.json')
    release = release_evidence(pointer['operation_id'], policy)
    boxes = {box for box, role in source.targets(policy['policy'])}
    if set(operations) != boxes: raise source.SourceError('recovery tests must cover every selected box')
    evidence = {}
    files = source.REPO / 'ansible/roles/vm-update-recovery/files'
    helper = hashlib.sha256((files / 'vm-update-transaction').read_bytes()).hexdigest()
    service = hashlib.sha256((files / 'klokast-vm-update-recovery.openrc').read_bytes()).hexdigest()
    harness = hashlib.sha256((files / 'vm-update-recovery-test').read_bytes()).hexdigest()
    for box, operation in operations.items():
        if not OPERATION.fullmatch(operation): raise source.SourceError('invalid recovery test operation')
        observed = native.vm_update_recovery_evidence(box, operation)
        result = observed['result']
        stages = {item.get('stage') for item in result.get('tests', []) if isinstance(item, dict)}
        if (result.get('kind') != 'klokast.vm-recovery-test.v1' or result.get('operation_id') != operation or
                result.get('production_replacement') is not False or
                not {'watchdog-expiry', 'controller-loss', 'generation-chain'} <= stages or
                observed.get('helper_sha256') != helper or observed.get('service_sha256') != service or
                result.get('transaction_sha256') != helper or result.get('harness_sha256') != harness or
                result.get('success') is not True or result.get('candidate_id') != pointer['operation_id'] or
                result.get('candidate_sha256') != release['candidate_sha256'] or any(result.get(key) is not True for key in
                ('watchdog_tested', 'process_restart_tested', 'controller_loss_tested', 'generation_chain_tested', 'runtime_reconciliation_tested'))):
            raise source.SourceError('native recovery tests are incomplete or failed')
        evidence[box] = source.digest(observed)
    write(ROOT / 'replacement-ready.json', {'context_sha256': policy['context_sha256'],
          'candidate_id': pointer['operation_id'], 'operations': operations, 'evidence': evidence})
    return {'replacement_ready': True}


def adopt(box, role):
    policy = require_policy()
    if (box, role) not in source.targets(policy['policy']): raise source.SourceError('adoption target is outside current supported policy')
    path = ROOT / 'adoptions' / (box + '-' + role + '.json')
    if path.exists():
        previous = source.read_json(path)
        request = previous['request']
        if request.get('box') != box or request.get('role') != role:
            raise source.SourceError('recorded adoption target differs')
        assignment = native.vm_adoption_assignment_verified(request)
        write(path, {'request': request, 'stage': 'complete', 'assignment': assignment})
        return {'result': 'already-adopted', 'box': box, 'role': role}
    native.vm_adoption_run_controller('scan')
    report = native.vm_adoption_run_controller('adopt', 'prepare', '--box', box, '--role', role, '--json', '--qualification-only')
    if (report.get('classification_complete') is not True or report.get('machine_inputs_approved') is not True or
            report.get('source_match') is not True or report.get('summary', {}).get('unresolved') != 0 or
            report.get('cleanup_items') or report.get('intent', {}).get('eligible') is not True or
            report.get('intent', {}).get('workloads') or report.get('intent', {}).get('datasets')):
        raise source.SourceError('adoption requires fresh complete accounting without workloads or retained data')
    old = source.read_json(DISCOVERY / 'source-audits' / (report['source_evidence_sha256'] + '.json'))
    if source.digest(old) != report['source_evidence_sha256'] or old.get('runtime') != 'running':
        raise source.SourceError('observed source changed before adoption')
    request = {'kind': 'klokast.vm-adoption.v1', 'operation_id': secrets.token_hex(12), 'box': box, 'role': role,
               'engine_commit': policy['engine_commit'], 'policy_sha256': policy['policy_sha256'],
               'qualification_sha256': report['report_sha256'], 'source_evidence_sha256': report['source_evidence_sha256'],
               'old_uuid': old['vm_uuid'], 'old_config_sha256': old['configuration_sha256'],
               'old_disks': old['disks'], 'old_artifacts': old['artifacts'], 'autostart': True}
    if policy_status() != policy: raise source.SourceError('Instance policy changed before adoption')
    write(path, {'request': request, 'stage': 'pending'})
    state = native.vm_adoption_remote(box, ['adopt-source', '--operation-id', request['operation_id']], input_text=native.canonical(request) + '\n')
    if state.get('stage') != 'adopted' or state.get('request_sha256') != source.digest(request):
        raise source.SourceError('dom0 did not record the expected old generation; reconcile the pending request')
    assignment = native.vm_adoption_assignment_verified(request)
    write(path, {'request': request, 'stage': 'complete', 'assignment': assignment})
    return {'result': 'adopted', 'box': box, 'role': role}
