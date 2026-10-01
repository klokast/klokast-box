"""Exact new router LV allocation and retirement; no filesystem or boot authority.

Callers hold the installation lock, validate their lifecycle authority, and keep
private records on dom0. Qualification uses the same fresh clone primitive.
A partial allocation never permits guessing an LV identity or formatting it.
"""
import json
from contextlib import nullcontext
from pathlib import Path
import time

import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import TransactionError
from xen_build_runtime import checksum, safe_file

BYTES = 2147483648


def selection(operation):
    if not generations.matches('[0-9a-f]{24}', operation):
        raise TransactionError('router candidate disk needs one exact operation ID')
    return '/dev/vg0/routergen_' + operation, 'routergen_' + operation


def inventory():
    value = json.loads(native.command(['/sbin/lvs', '--reportformat', 'json', '--units', 'b', '--nosuffix',
        '-o', 'lv_path,lv_uuid,lv_size,lv_attr,origin,lv_tags'], time.monotonic() + 30), object_pairs_hook=records.unique)
    rows = [row for report in value['report'] for row in report['lv']]
    if any(not isinstance(row, dict) or set(row) != {'lv_path','lv_uuid','lv_size','lv_attr','origin','lv_tags'}
           for row in rows) or len({row['lv_path'] for row in rows}) != len(rows):
        raise TransactionError('router candidate LV inventory is incomplete or ambiguous')
    return [{k:v.strip() for k,v in row.items()} for row in rows]


def observed(operation, *, identity=None):
    path, _ = selection(operation)
    rows = inventory()
    if identity is not None and any(row['lv_uuid'] == identity and row['lv_path'] != path for row in rows):
        raise TransactionError('router candidate LV UUID moved to another path; preserve it for reconciliation')
    return next((row for row in rows if row['lv_path'] == path), None)


def validate_row(row, operation, identity):
    path, tag = selection(operation)
    if (not isinstance(row, dict) or row.get('lv_path') != path or row.get('lv_uuid') != identity or
            not generations.matches('[A-Za-z0-9-]{1,64}', identity) or row.get('lv_size') != str(BYTES) or
            row.get('origin') != '' or not row.get('lv_attr', '').startswith('-wi-a') or row.get('lv_tags') != tag):
        raise TransactionError('router candidate LV UUID, ownership tag, size, or independent allocation changed')
    return {'path':path, 'uuid':identity, 'bytes':BYTES}


def record(work, operation):
    value = records.read(Path(work) / 'candidate-disk.json')
    if (not isinstance(value, dict) or set(value) != {'kind','operation_id','path','tag','uuid','stage','template_sha256'} or
            value['kind'] != 'klokast.router-candidate-disk.v1' or value['operation_id'] != operation or
            (value['path'], value['tag']) != selection(operation) or
            not generations.matches('[0-9a-f]{64}', value['template_sha256']) or
            value['stage'] not in ('planned','aborted','allocated','cloned','retiring','retired') or
            (value['stage'] in ('planned','aborted') and value['uuid'] is not None) or
            (value['stage'] not in ('planned','aborted') and
             not generations.matches('[A-Za-z0-9-]{1,64}', value['uuid']))):
        raise TransactionError('router candidate disk record has an invalid identity or stage')
    return value


def verify(work, operation, *, detached=True):
    value = record(work, operation)
    if value['stage'] not in ('allocated','cloned','retiring'):
        raise TransactionError('router candidate disk has no completed allocation record')
    disk = validate_row(observed(operation, identity=value['uuid']), operation, value['uuid'])
    host = native.Native()
    deadline = time.monotonic() + 30
    host.disk(disk, deadline=deadline)
    if detached:
        host.wait_detached([disk['path']], deadline=deadline)
    return disk


def template_source(source, expected):
    source = Path(source)
    if (not isinstance(expected, dict) or set(expected) != {'bytes','sha256'} or expected['bytes'] != BYTES or
            not generations.matches('[0-9a-f]{64}', expected['sha256'])):
        raise TransactionError('router candidate requires the exact qualified template size and hash')
    records.parents(source)
    safe_file(source, BYTES)
    if source.stat().st_size != BYTES or checksum(source) != expected['sha256']:
        raise TransactionError('router candidate template bytes differ before allocation')
    return source


def allocate(work, operation, expected):
    """Internal allocation; the authorized caller has checked all references."""
    path, tag = selection(operation)
    value = {'kind':'klokast.router-candidate-disk.v1', 'operation_id':operation, 'path':path,
             'tag':tag, 'uuid':None, 'stage':'planned', 'template_sha256':expected['sha256']}
    records.write(work / 'candidate-disk.json', value)
    native.command(['/sbin/lvcreate', '--size', '2G', '--name', tag, '--addtag', tag,
                    '--wipesignatures', 'y', '--yes', 'vg0'],
                   time.monotonic() + 120, maximum_seconds=120, lvm_diagnostic=True)
    row = observed(operation)
    disk = validate_row(row, operation, row.get('lv_uuid') if isinstance(row, dict) else None)
    records.write(work / 'candidate-disk.json', {**value, 'uuid':disk['uuid'], 'stage':'allocated'})
    return disk


def clone(work, operation, source, expected):
    """Internal copy into a recorded, detached, never-prepared allocation."""
    value = record(work, operation)
    if value['stage'] != 'allocated' or value['template_sha256'] != expected['sha256']:
        raise TransactionError('router clone requires its exact unfinished allocation')
    if any((work / name).exists() or (work / name).is_symlink() for name in (
            'preparation.json', 'preparation-result.json', 'result.slot', 'prepare.cfg')):
        raise TransactionError('router clone cannot overwrite a disk whose preparation has started')
    disk = verify(work, operation)
    native.command(['/bin/dd', 'if='+str(source), 'of='+disk['path'], 'bs=4M', 'count=512', 'conv=notrunc,fsync'],
                   time.monotonic() + 180, maximum_seconds=180)
    verify(work, operation)
    if checksum(Path(disk['path']), BYTES) != expected['sha256']:
        raise TransactionError('router candidate cloned bytes differ; retain the recorded disk')
    records.write(work / 'candidate-disk.json', {**value,'stage':'cloned'})
    return disk


def create(work, operation, source, expected, *, box):
    """Clone only authenticated opaque bytes into a newly created, recorded LV."""
    work = Path(work)
    path, _ = selection(operation)
    records.parents(work / 'candidate-disk.json')
    if (work / 'candidate-disk.json').exists() or (work / 'candidate-disk.json').is_symlink() or observed(operation):
        raise TransactionError('router candidate allocation already exists; reconcile its exact record')
    refuse_referenced_disk(box, operation, {'path':path, 'uuid':None})
    source = template_source(source, expected)
    allocate(work, operation, expected)
    return clone(work, operation, source, expected)


def initial_clone(work, operation, source, expected, storage):
    """Resume only the exact planned first installation, under its local lock.

    Persist the installation fence before calling this function. Record the
    allocated UUID before copying. An unrecorded LV is never adopted or reset.
    """
    work = Path(work)
    installation = storage.installation()
    records.initial_installation.validate(installation, storage.box)
    if installation['operation_id'] != operation or installation['stage'] not in ('planned', 'allocated'):
        raise TransactionError('initial clone requires its exact pre-preparation installation record')
    source = template_source(source, expected)
    path = work / 'candidate-disk.json'
    if not path.exists() and not path.is_symlink():
        if installation['stage'] != 'planned' or observed(operation) is not None:
            raise TransactionError('initial clone has an unrecorded disk; reconcile its allocation')
        disk = allocate(work, operation, expected)
    else:
        value = record(work, operation)
        if value['template_sha256'] != expected['sha256']:
            raise TransactionError('initial clone has an incomplete or different allocation identity')
        if value['stage'] == 'planned' and installation['stage'] == 'planned' and observed(operation) is None:
            disk = allocate(work, operation, expected)
        elif value['stage'] in ('allocated', 'cloned'):
            disk = verify(work, operation)
        else:
            raise TransactionError('initial clone has an incomplete or different allocation identity')
    if installation['stage'] == 'planned':
        installation = storage.record_installation(generations.seal({
            **{key:value for key,value in installation.items() if key != 'record_sha256'},
            'disk':disk, 'stage':'allocated'}))
    if installation['disk'] != disk:
        raise TransactionError('initial clone disk differs from its recorded installation')
    if record(work, operation)['stage'] == 'cloned':
        return disk  # Never reset a completed clone, including a prepared one.
    return clone(work, operation, source, expected)


def replacement_clone(work, operation, source, expected, storage, old_sha256):
    """Resume only a recorded candidate for the current accepted router.

    The caller holds the router record lock and has checked replacement
    authority. This function cannot adopt an LV without a recorded UUID.
    """
    work = Path(work)
    if storage.cold_test() is not None:
        raise TransactionError('router replacement preparation cannot run during a supervised first-install test')
    if (storage.pending() is not None or
            storage.accepted()['current_sha256'] != old_sha256 or
            not generations.matches('[0-9a-f]{64}', old_sha256)):
        raise TransactionError('replacement clone requires its unchanged accepted router')
    source = template_source(source, expected)
    path = work / 'candidate-disk.json'
    if not path.exists() and not path.is_symlink():
        if observed(operation) is not None:
            raise TransactionError('replacement clone found an unrecorded candidate LV')
        selected, _ = selection(operation)
        refuse_referenced_disk(storage.box, operation, {'path':selected, 'uuid':None})
        disk = allocate(work, operation, expected)
    else:
        value = record(work, operation)
        if value['template_sha256'] != expected['sha256']:
            raise TransactionError('replacement clone selects a different template')
        refuse_referenced_disk(storage.box, operation,
            {'path':value['path'], 'uuid':value['uuid']})
        if value['stage'] == 'planned':
            if observed(operation) is not None:
                raise TransactionError('replacement clone has an unrecorded LV identity')
            disk = allocate(work, operation, expected)
        elif value['stage'] in ('allocated', 'cloned'):
            disk = verify(work, operation)
        else:
            raise TransactionError('replacement clone has an unsupported disk stage')
    if record(work, operation)['stage'] == 'cloned':
        return disk
    return clone(work, operation, source, expected)


def refuse_referenced_disk(box, operation, disk):
    """A diagnostic disk must never retire a recorded installation or generation."""
    if not generations.matches('[a-z0-9][a-z0-9-]{0,30}', box):
        raise TransactionError('router candidate retirement requires an exact box')
    base = records.BASE
    if not base.exists() and not base.is_symlink():
        return
    storage = records.Records(box)
    installation = storage.installation()
    if installation and (installation['operation_id'] == operation or
            installation['disk']['path'] == disk['path'] or
            disk['uuid'] is not None and installation['disk']['uuid'] == disk['uuid']):
        raise TransactionError('router candidate disk belongs to a recorded first installation')
    pending = storage.pending()
    if pending and pending['request']['operation_id'] == operation:
        raise TransactionError('router candidate belongs to a pending production operation')
    checksums = set()
    accepted = base / 'accepted.json'
    if accepted.exists() or accepted.is_symlink():
        assignment = storage.accepted()
        checksums.update(v for v in (assignment['current_sha256'], assignment['previous_sha256']) if v)
    if pending:
        checksums.update((pending['request']['old_sha256'], pending['request']['candidate_sha256']))
    for checksum in checksums:
        generation = storage.generation(checksum)
        if (generation['disk']['path'] == disk['path'] or
                generation['disk']['uuid'] == disk['uuid']):
            raise TransactionError('router candidate disk is referenced by an accepted or pending generation')


def retire(work, operation, *, box, inspected_uuid=None):
    """Retire only the exact recorded disk after native mount/backend checks."""
    if not generations.matches('[a-z0-9][a-z0-9-]{0,30}', box):
        raise TransactionError('router candidate retirement requires an exact box')
    base = records.BASE
    if base.exists() or base.is_symlink():
        context = records.Records(box).lock()
    else:
        context = nullcontext()
    with context:
        return _retire_locked(work, operation, box=box, inspected_uuid=inspected_uuid)


def _retire_locked(work, operation, *, box, inspected_uuid):
    """The generation reference check and removal share the local record lock."""
    work = Path(work)
    value = record(work, operation)
    row = observed(operation, identity=value['uuid'])
    if value['stage'] in ('planned','aborted') and row is None:
        refuse_referenced_disk(box, operation, {'path':value['path'], 'uuid':None})
        if value['stage'] == 'planned':
            records.write(work / 'candidate-disk.json', {**value,'stage':'aborted'})
        return 0
    if value['stage'] == 'aborted':
        raise TransactionError('an aborted candidate LV appeared after exact absence was recorded')
    if value['stage'] in ('retired','retiring') and row is None:
        refuse_referenced_disk(box, operation, {'path':value['path'], 'uuid':value['uuid']})
        records.write(work / 'candidate-disk.json', {**value,'stage':'retired'})
        return 0
    if value['stage'] == 'retired':
        raise TransactionError('a router candidate LV reappeared after recorded retirement')
    if value['stage'] == 'planned':
        if inspected_uuid is None:
            raise TransactionError('interrupted candidate allocation needs an explicitly inspected LV UUID')
        validate_row(row, operation, inspected_uuid)
        value.update(uuid=inspected_uuid, stage='allocated')
        records.write(work / 'candidate-disk.json', value)
    elif inspected_uuid is not None and inspected_uuid != value['uuid']:
        raise TransactionError('router candidate retirement UUID differs from the recorded allocation')
    disk = verify(work, operation)
    refuse_referenced_disk(box, operation, disk)
    records.write(work / 'candidate-disk.json', {**value,'stage':'retiring'})
    native.command(['/sbin/lvremove','--yes',disk['path']], time.monotonic() + 60, maximum_seconds=60)
    if observed(operation, identity=disk['uuid']) is not None:
        raise TransactionError('router candidate LV remains after retirement')
    records.write(work / 'candidate-disk.json', {**value,'stage':'retired'})
    return BYTES
