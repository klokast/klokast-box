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


def observed(operation):
    path, _ = selection(operation)
    return next((row for row in inventory() if row['lv_path'] == path), None)


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
    disk = validate_row(observed(operation), operation, value['uuid'])
    host = native.Native()
    deadline = time.monotonic() + 30
    host.disk(disk, deadline=deadline)
    if detached:
        host.wait_detached([disk['path']], deadline=deadline)
    return disk


def create(work, operation, source, expected):
    """Clone only authenticated opaque bytes into a newly created, recorded LV."""
    work, source = Path(work), Path(source)
    path, tag = selection(operation)
    records.parents(work / 'candidate-disk.json')
    if (work / 'candidate-disk.json').exists() or (work / 'candidate-disk.json').is_symlink() or observed(operation):
        raise TransactionError('router candidate allocation already exists; reconcile its exact record')
    if (not isinstance(expected, dict) or set(expected) != {'bytes','sha256'} or expected['bytes'] != BYTES or
            not generations.matches('[0-9a-f]{64}', expected['sha256'])):
        raise TransactionError('router candidate requires the exact qualified template size and hash')
    records.parents(source)
    safe_file(source, BYTES)
    if source.stat().st_size != BYTES or checksum(source) != expected['sha256']:
        raise TransactionError('router candidate template bytes differ before allocation')
    value = {'kind':'klokast.router-candidate-disk.v1', 'operation_id':operation, 'path':path,
             'tag':tag, 'uuid':None, 'stage':'planned', 'template_sha256':expected['sha256']}
    records.write(work / 'candidate-disk.json', value)
    native.command(['/sbin/lvcreate', '--size', '2G', '--name', tag, '--addtag', tag,
                    '--wipesignatures', 'y', '--yes', 'vg0'],
                   time.monotonic() + 120, maximum_seconds=120, lvm_diagnostic=True)
    row = observed(operation)
    disk = validate_row(row, operation, row.get('lv_uuid') if isinstance(row, dict) else None)
    value.update(uuid=disk['uuid'], stage='allocated')
    records.write(work / 'candidate-disk.json', value)
    verify(work, operation)
    native.command(['/bin/dd', 'if='+str(source), 'of='+path, 'bs=4M', 'count=512', 'conv=notrunc,fsync'],
                   time.monotonic() + 180, maximum_seconds=180)
    verify(work, operation)
    if checksum(Path(path), BYTES) != expected['sha256']:
        raise TransactionError('router candidate cloned bytes differ; retain the recorded disk')
    records.write(work / 'candidate-disk.json', {**value,'stage':'cloned'})
    return disk


def refuse_referenced_disk(box, operation, disk):
    """A diagnostic disk must never be retired after it becomes a generation."""
    if not generations.matches('[a-z0-9][a-z0-9-]{0,30}', box):
        raise TransactionError('router candidate retirement requires an exact box')
    base = records.BASE
    if not base.exists() and not base.is_symlink():
        return
    storage = records.Records(box)
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
    row = observed(operation)
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
    if observed(operation) is not None:
        raise TransactionError('router candidate LV remains after retirement')
    records.write(work / 'candidate-disk.json', {**value,'stage':'retired'})
    return BYTES
