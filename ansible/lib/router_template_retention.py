"""Protect router template references under diagnostic and production locks.

This reader never selects a disk, grants a cutover, or changes an assignment.
The caller owns the template build lock before entering guard().
"""
from contextlib import contextmanager
import fcntl
import os
import stat
from pathlib import Path

import router_generations as generations
import router_records as records
import router_transaction as transaction

COMPATIBILITY = Path('/mnt/dom0_data/klokast-router-compatibility')
MAX_OPERATIONS = 4096


def exists(path):
    return path.exists() or path.is_symlink()


def directories(path, *, ignore=()):
    if not exists(path):
        return []
    records.secure(path, directory=True)
    result = sorted(child for child in path.iterdir() if child.name not in ignore)
    if len(result) > MAX_OPERATIONS:
        raise transaction.TransactionError('router template reference inventory exceeds its bound')
    for child in result:
        if not generations.matches('[0-9a-f]{24}', child.name):
            raise transaction.TransactionError('router template reference inventory has an unknown operation')
        records.secure(child, directory=True)
    return result


def request_reference(value, box, operation):
    modes = {'klokast.router-initial-preparation.v1':'initial-install',
             'klokast.router-replacement-preparation.v1':'replacement'}
    if (not isinstance(value, dict) or value.get('kind') not in modes or
            value.get('mode') != modes[value['kind']] or value.get('box') != box or
            value.get('operation_id') != operation or
            not generations.matches('[0-9a-f]{40}', value.get('engine_commit')) or
            not generations.matches('[0-9a-f]{24}', value.get('template_operation')) or
            not generations.matches('[0-9a-f]{64}', value.get('template_sha256'))):
        raise transaction.TransactionError('router template preparation reference is invalid: '+operation)
    return value['template_operation']


def sealed(path, kind):
    value = records.read(path)
    generations.check_seal(value)
    if value.get('kind') != kind:
        raise transaction.TransactionError('router template reference has another cleanup record kind')
    return value


def preparation_cleaned(work, request):
    target = work / 'preparation-cleanup-complete.json'
    if not exists(target):
        return False
    result = sealed(target, 'klokast.router-preparation-cleanup-complete.v1')
    plan = sealed(work/'preparation-cleanup-plan.json', 'klokast.router-preparation-cleanup-plan.v1')
    progress = sealed(work/'preparation-cleanup-progress.json', 'klokast.router-preparation-cleanup-progress.v1')
    if (any(result.get(key) != request[key] for key in ('box','operation_id','engine_commit')) or
            result.get('status') != 'unused-resources-retired' or
            result.get('plan_sha256') != plan['record_sha256'] or
            result.get('progress_sha256') != progress['record_sha256'] or
            result.get('assignment_sha256') != plan.get('assignment_sha256') or
            any(plan.get(key) != request[key] for key in ('box','operation_id','engine_commit')) or
            plan.get('request_sha256') != generations.digest(request) or
            progress.get('plan_sha256') != plan['record_sha256'] or progress.get('phase') != 'complete' or
            progress.get('inflight') is not None or not isinstance(plan.get('files'),list) or
            progress.get('removed') != [item['path'] for item in plan['files']]):
        raise transaction.TransactionError('router template preparation cleanup proof changed')
    return True


def replacement_cleaned(work, request):
    target = work / 'cleanup-complete.json'
    if not exists(target):
        return False
    result = sealed(target, 'klokast.router-cleanup-complete.v2')
    plan = sealed(work/'cleanup-plan.json', 'klokast.router-cleanup-plan.v1')
    pending_request = records.read(work/'transaction-request.json')
    transaction.validate(pending_request)
    progress = records.read(work/'cleanup-progress.json')
    copy = sealed(work/'copy/retirement.json','klokast.router-copy-retirement.v1')
    complete = records.read(work/'complete.json')
    transaction.validate_pending(complete,pending_request)
    if (any(result.get(key) != request[key] or pending_request[key] != request[key]
                for key in ('box','operation_id','engine_commit')) or
            result.get('status') != 'exact-resources-retired' or
            result.get('plan_sha256') != plan['record_sha256'] or
            result.get('assignment_sha256') != plan.get('assignment_sha256') or
            any(plan.get(key) != request[key] for key in ('box','operation_id','engine_commit')) or
            progress != {'kind':'klokast.router-cleanup-progress.v1',
                'plan_sha256':plan['record_sha256'],'phase':'complete'} or
            result.get('copy_retirement_sha256') != copy['record_sha256'] or
            copy.get('phase') != 'retired' or copy.get('inflight') is not None or
            copy.get('request_sha256') != generations.digest(pending_request) or
            not isinstance(copy.get('files'),dict) or not isinstance(copy.get('removed'),list) or
            set(copy['removed']) != set(copy['files']) or len(copy['removed']) != len(copy['files']) or
            complete['phase'] not in ('accepted','rolled-back') or
            records.read(work/'latest.json') != complete):
        raise transaction.TransactionError('router template replacement cleanup proof changed')
    return True


def historical_compatibility_cleaned(work, request):
    target = work/'historical-cleanup-complete.json'
    if not exists(target):
        return False
    result = records.read(target)
    lifecycle = records.read(work/'lifecycle.json')
    snapshot = records.read(work/'snapshot.json')
    candidate = records.read(work/'candidate-disk.json') if exists(work/'candidate-disk.json') else None
    artifact = records.read(work/'artifact-cleanup-complete.json')
    plan = records.read(work/'artifact-cleanup-plan.json')
    progress = records.read(work/'artifact-cleanup-progress.json')
    if (result.get('kind') != 'klokast.router-compatibility-historical-cleanup.v1' or
            any(result.get(key) != request[key] for key in ('box','operation_id','engine_commit')) or
            result.get('status') != 'historical-artifacts-retired' or
            any(result.get(key+'_sha256') != generations.digest(value) for key,value in
                (('request',request),('lifecycle',lifecycle),('snapshot',snapshot),('candidate',candidate),('artifact_cleanup',artifact))) or
            lifecycle.get('stage') != 'cleaned' or snapshot.get('stage') != 'retired' or
            candidate is not None and candidate.get('stage') != 'retired' or
            artifact.get('kind') != 'klokast.router-compatibility-artifact-cleanup.v1' or
            any(artifact.get(key) != request[key] or plan.get(key) != request[key]
                for key in ('box','operation_id','engine_commit')) or
            artifact.get('status') != 'boot-files-retired' or
            artifact.get('plan_sha256') != generations.digest(plan) or
            artifact.get('progress_sha256') != generations.digest(progress) or
            plan.get('kind') != 'klokast.router-compatibility-artifact-plan.v1' or
            plan.get('request_sha256') != generations.digest(request) or
            not isinstance(plan.get('files'),list) or
            progress.get('kind') != 'klokast.router-compatibility-artifact-progress.v1' or
            progress.get('plan_sha256') != generations.digest(plan) or progress.get('inflight') is not None or
            progress.get('removed') != [item['name'] for item in plan['files']]):
        raise transaction.TransactionError('historical compatibility template cleanup proof changed')
    return True


def references(storage):
    """Called with storage.lock held; malformed or unfinished authority stays."""
    if storage.cold_test() is not None:
        raise transaction.TransactionError('template retirement is forbidden during a supervised cold test')
    accepted_path = storage.base/'accepted.json'
    accepted = storage.accepted() if exists(accepted_path) else None
    pending = storage.pending()
    installation = storage.installation()
    retained = set()
    def keep_generation(checksum):
        value = storage.generation(checksum)
        if value['origin'] == 'template':
            retained.add(value['template_operation'])
    if accepted:
        for key in ('current_sha256','previous_sha256'):
            if accepted[key] is not None:
                keep_generation(accepted[key])
    if pending:
        if accepted is None:
            raise transaction.TransactionError('router template references have pending work without an accepted assignment')
        storage.committed(pending['request'])
        for key in ('old_sha256','candidate_sha256'):
            keep_generation(pending['request'][key])
    for work in directories(storage.base/'operations'):
        path = work/'request.json'
        if not exists(path):
            if any(exists(work/name) for name in ('candidate-disk.json','preparation.json','proposed-generation.json')):
                raise transaction.TransactionError('router preparation resources lack their template reference')
            continue
        request = records.read(path)
        template = request_reference(request,storage.box,work.name)
        verified_initial = (request['mode'] == 'initial-install' and installation is not None and
            installation['operation_id'] == work.name and installation['stage'] == 'verified')
        if verified_initial:
            value = storage.generation(installation['generation_sha256'])
            records.initial_installation.matches_generation(installation,value,storage.box)
            if (value['template_operation'] != template or value['release_sha256'] != request.get('release_sha256') or
                    value['engine_commit'] != request['engine_commit']):
                raise transaction.TransactionError('verified initial router has another template reference')
        elif not preparation_cleaned(work,request) and not replacement_cleaned(work,request):
            retained.add(template)
    if installation is not None and installation['stage'] != 'verified':
        work = storage.operation(installation['operation_id'])
        retained.add(request_reference(records.read(work/'request.json'),storage.box,work.name))
    # A staged cold request reserves a specific first-install template even
    # before it arms the cold fence. An exact pre-outage abort releases it.
    for work in directories(storage.base/'cold-backups'):
        path = work/'supervised-request.json'
        if not exists(path):
            continue
        value = records.read(path); generations.check_seal(value)
        pointer = records.initial_installation.provision_pointer(
            value.get('initial_provision'),storage.box,value.get('engine_commit'))
        if value.get('box') != storage.box or value.get('operation_id') != work.name:
            raise transaction.TransactionError('cold router template reservation targets another operation')
        abort = work/'prepared-abort-completion.json'
        if exists(abort):
            result = sealed(abort,'klokast.router-cold-prepared-abort.v1')
            intent = sealed(work/'prepared-abort-intent.json','klokast.router-cold-prepared-abort-intent.v1')
            if (result.get('status') != 'retired' or result.get('box') != storage.box or
                    result.get('operation_id') != work.name or result.get('intent_sha256') != intent['record_sha256'] or
                    intent.get('box') != storage.box or intent.get('operation_id') != work.name or
                    result.get('source_engine_commit') != value.get('engine_commit')):
                raise transaction.TransactionError('cold router template abort proof changed')
        else:
            retained.add(pointer['template_operation'])
    for work in directories(COMPATIBILITY,ignore={'test.lock'}):
        path = work/'request.json'
        if not exists(path):
            if any(exists(work/name) for name in ('lifecycle.json','candidate-disk.json','snapshot.json')):
                raise transaction.TransactionError('router compatibility resources lack their template reference')
            continue
        request = records.read(path)
        template = request.get('template')
        if (request.get('kind') not in ('klokast.router-compatibility-host.v1','klokast.router-compatibility-host.v2') or
                request.get('role') != 'router' or request.get('box') != storage.box or
                not generations.matches('[0-9a-f]{40}',request.get('engine_commit')) or
                request.get('operation_id') != work.name or not isinstance(template,dict) or
                not generations.matches('[0-9a-f]{24}',template.get('operation')) or
                not generations.matches('[0-9a-f]{64}',template.get('sha256'))):
            raise transaction.TransactionError('router compatibility template reference is invalid: '+work.name)
        if historical_compatibility_cleaned(work,request):
            continue
        if request['kind'] == 'klokast.router-compatibility-host.v1':
            # A legacy cleaned lifecycle is not the new complete resource proof.
            # Retain this exact template until legacy cleanup is reconciled.
            retained.add(template['operation'])
            continue
        target = work/'cleanup-complete.v2.json'
        if exists(target):
            complete = records.read(target)
            disk = records.read(work/'cleanup-complete.json')
            artifact = records.read(work/'artifact-cleanup-complete.json')
            if (complete.get('kind') != 'klokast.router-compatibility-cleanup.v2' or
                    any(complete.get(key) != request[key] for key in ('box','operation_id','engine_commit')) or
                    complete.get('status') != 'unused-resources-retired' or
                    complete.get('disk_cleanup_sha256') != generations.digest(disk) or
                    complete.get('artifact_cleanup_sha256') != generations.digest(artifact) or
                    disk.get('kind') != 'klokast.router-compatibility-cleanup.v1' or
                    artifact.get('kind') != 'klokast.router-compatibility-artifact-cleanup.v1' or
                    any(disk.get(key) != request[key] or artifact.get(key) != request[key]
                        for key in ('box','operation_id','engine_commit')) or
                    disk.get('status') != 'unused-disks-retired' or artifact.get('status') != 'boot-files-retired'):
                raise transaction.TransactionError('router compatibility template cleanup proof changed')
        else:
            retained.add(template['operation'])
    return {'kind':'klokast.router-template-references.v1','box':storage.box,
            'assignment_sha256':accepted['record_sha256'] if accepted else None,
            'templates':sorted(retained)}


@contextmanager
def guard(box, operation):
    """Lock order: template build, compatibility tests, production records."""
    if not generations.matches('[0-9a-f]{24}',operation):
        raise transaction.TransactionError('template retirement requires an exact operation')
    # Qualification clones a template outside the production record lock.
    # Its fixed diagnostic lock must stay held throughout collection.
    records.parents(COMPATIBILITY)
    COMPATIBILITY.mkdir(mode=0o700,exist_ok=True)
    records.secure(COMPATIBILITY,directory=True)
    descriptor = os.open(COMPATIBILITY/'test.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != records.ROOT_UID or
                info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise transaction.TransactionError('router compatibility lock is unsafe')
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        storage = records.Records(box,records.BASE)
        with storage.lock():
            value = references(storage)
            if operation in value['templates']:
                raise transaction.TransactionError('router template is retained by current, rollback or unfinished work')
            def fresh():
                current = references(storage)
                if current != value or operation in current['templates']:
                    raise transaction.TransactionError('router template references changed during retirement')
            yield value,fresh
    finally:
        os.close(descriptor)
