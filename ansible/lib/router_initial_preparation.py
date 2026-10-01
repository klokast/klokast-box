"""Retain one initial router clone under an exact active-controller grant.

This command stops at prepared. It has no production boot, enrollment, or
acceptance action. The caller holds the controller installation lock; execute
also holds the dom0 router lock through allocation and networkless preparation.
"""
from pathlib import Path
import time

import router_candidate
import router_candidate_disk as disks
import router_candidate_preparation as preparation
import router_generations as generations
import router_native
import router_records as records
import router_updates
from router_preparation_assets import template, bootstrap
from router_transaction import TransactionError
from xen_build_runtime import domain


def authority(value, job, release, profile, selection, grant, box, operation, engine, now):
    fields = {'kind', 'box', 'mode', 'operation_id', 'engine_commit', 'inputs_sha256',
              'source_operation',
              'template_operation', 'template_sha256', 'job_sha256', 'bootstrap',
              'selection_sha256', 'release_sha256', 'registry_sha256'}
    if (not isinstance(value, dict) or set(value) != fields or
            value['kind'] != 'klokast.router-initial-preparation.v1' or
            value['box'] != box or value['operation_id'] != operation or
            value['mode'] != 'initial-install' or value['engine_commit'] != engine or
            not generations.matches('[a-z0-9][a-z0-9-]{0,30}', box) or
            not generations.matches('[0-9a-f]{24}', operation) or
            not generations.matches('[0-9a-f]{40}', engine) or
            not generations.matches('[0-9a-f]{24}', value['source_operation']) or
            not generations.matches('[0-9a-f]{24}', value['template_operation']) or
            any(not generations.matches('[0-9a-f]{64}', value[key]) for key in (
                'inputs_sha256', 'template_sha256', 'job_sha256', 'selection_sha256',
                'release_sha256', 'registry_sha256'))):
        raise TransactionError('initial preparation has an invalid target or approved input identity')
    router_candidate.validate(job)
    router_updates.validate_release(release, profile, engine)
    router_updates.verify_seal(selection)
    if (any(job[key] != value[key] for key in (
            'box', 'operation_id', 'mode', 'engine_commit', 'inputs_sha256')) or
            value['job_sha256'] != generations.digest(job) or
            release['receipt_sha256'] != value['release_sha256'] or
            release['inputs']['inputs_sha256'] != value['inputs_sha256'] or
            selection.get('kind') != 'klokast.router-bootstrap-input-selection.v1' or
            selection.get('receipt_sha256') != value['selection_sha256'] or
            selection.get('engine_commit') != engine or
            selection.get('inputs_sha256') != value['inputs_sha256'] or
            selection.get('replacement_authorized') is not False or
            job['kernel_release'] != release['kernel_release'] or
            job['runtime_packages'] != release['runtime_packages'] or
            job['personalization']['packages'] != {
                item['name']:item['version'] for item in release['inputs']['packages']}):
        raise TransactionError('initial preparation differs from its selected release or job')
    if (not isinstance(grant, dict) or set(grant) != {
            'kind', 'engine_commit', 'request_sha256', 'granted_at', 'expires_at'} or
            grant['kind'] != 'klokast.router-initial-preparation-grant.v1' or
            grant['engine_commit'] != engine or grant['request_sha256'] != generations.digest(value) or
            any(type(grant[key]) is not int for key in ('granted_at', 'expires_at')) or
            not grant['granted_at'] <= now < grant['expires_at'] <= grant['granted_at'] + 900):
        raise TransactionError('initial preparation grant is stale or selects different inputs')
    return value


def fresh_target(storage, operation, *, xen=Path('/etc/xen')):
    """Refuse observed old resources; absence of an assignment is insufficient."""
    storage.initial_window(operation)
    if storage.pending() is not None or any(path.exists() or path.is_symlink() for path in (
            storage.base / 'accepted.json', xen / 'router.cfg', xen / 'auto/router.cfg')):
        raise TransactionError('initial preparation cannot replace an assigned or configured router')
    if domain('router') is not None:
        raise TransactionError('initial preparation cannot replace a running router')
    installation = storage.installation()
    selected = disks.selection(operation)[0]
    for row in disks.inventory():
        if row['lv_path'] == '/dev/vg0/lv_router' or (
                row['lv_path'].startswith('/dev/vg0/routergen_') or
                any(tag.startswith('routergen_') for tag in row['lv_tags'].split(','))):
            if installation is None or row['lv_path'] != selected:
                raise TransactionError('initial preparation found an existing unassigned router disk')
    return installation


def execute(storage, operation, engine, *, xen=Path('/etc/xen')):
    """Prepare a retained disk, or resume this same pre-enrollment operation."""
    work = storage.operation(operation)
    value, job, release, profile, selection, grant = (
        records.read(work / name) for name in (
            'request.json', 'candidate-job.json', 'release.json', 'profile.json',
            'selection.json', 'authorization.json'))
    authority(value, job, release, profile, selection, grant, storage.box, operation, engine, time.time())
    router_native.Native().guard(storage.box, deadline=time.monotonic() + 30)
    source, expected = template(value, release)
    bootstrap(work, value)
    with storage.lock():
        current = fresh_target(storage, operation, xen=xen)
        planned = {'kind':'klokast.router-initial-installation.v1', 'box':storage.box, 'role':'router',
            'operation_id':operation, 'engine_commit':engine,
            'selection_sha256':value['selection_sha256'], 'release_sha256':value['release_sha256'],
            'disk':{'path':disks.selection(operation)[0], 'uuid':None, 'bytes':disks.BYTES},
            'stage':'planned', 'preparation_sha256':None, 'enrollment_sha256':None,
            'machine_id':None, 'generation_sha256':None}
        if current is not None and (any(current[key] != planned[key] for key in (
                'box', 'role', 'operation_id', 'engine_commit', 'selection_sha256', 'release_sha256')) or
                current['stage'] not in ('planned', 'allocated', 'prepared')):
            raise TransactionError('initial preparation must not change or repeat an enrolled installation')
        # The controller grant must still be live after potentially slow hashes.
        authority(value, job, release, profile, selection, grant, storage.box, operation, engine, time.time())
        if current is None:
            current = storage.record_installation(generations.seal(planned))
        if current['stage'] != 'prepared':
            disk = disks.initial_clone(work, operation, source, expected, storage)
        else:
            disk = disks.verify(work, operation)
        current = storage.installation()
        if current['disk'] != disk:
            raise TransactionError('initial preparation disk differs from its durable installation')
        result = preparation.prepare(work, value, job, disk)
        component = {key:release['inputs']['tailscale'][key] for key in (
            'version', 'sha256', 'tailscale_sha256', 'tailscaled_sha256', 'openrc_sha256')}
        if result['prepared']['tailscale'] != component:
            raise TransactionError('initial preparation Tailscale binaries differ from the selected release')
        result_hash = generations.digest(result)
        if current['stage'] == 'prepared':
            if current['preparation_sha256'] != result_hash:
                raise TransactionError('initial preparation differs from its retained completion record')
        else:
            storage.record_installation(generations.seal({
                **{key:value for key,value in current.items() if key != 'record_sha256'},
                'stage':'prepared', 'preparation_sha256':result_hash}))
        return {'kind':'klokast.router-initial-preparation-result.v1', 'box':storage.box,
                'operation_id':operation, 'status':'initial-prepared', 'router_started':False,
                'installation':storage.installation()}
