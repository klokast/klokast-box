"""Start only the retained, prepared first router under a fresh controller grant."""
import hashlib
import os
from pathlib import Path
import time

import router_candidate_preparation as preparation
import router_candidate_disk as disks
import router_generations as generations
import router_initial_installation as installation
import router_native as native
import router_records as records
import router_updates
from router_transaction import TransactionError

TEMPLATES = Path('/mnt/dom0_data/klokast-router-templates')
MAXIMUM = {'kernel':32 * 1024 * 1024, 'initramfs':128 * 1024 * 1024}


def authority(value, grant, source, prepared, release, current, box, operation, engine, now):
    """The boot grant binds a recorded preparation and one complete Xen identity."""
    installation.validate(current, box)
    router_updates.verify_seal(release)
    if (not isinstance(value, dict) or set(value) != {'kind','box','operation_id','engine_commit',
            'registry_sha256','source_request_sha256','preparation_sha256','selection_sha256',
            'release_sha256','xen'} or value['kind'] != 'klokast.router-initial-boot-request.v1' or
            value['box'] != box or value['operation_id'] != operation or value['engine_commit'] != engine or
            any(not generations.matches('[0-9a-f]{64}', value[key]) for key in (
                'registry_sha256','source_request_sha256','preparation_sha256','selection_sha256',
                'release_sha256')) or
            not isinstance(source, dict) or source.get('kind') != 'klokast.router-initial-preparation.v1' or
            source.get('box') != box or source.get('operation_id') != operation or
            source.get('engine_commit') != engine or
            value['source_request_sha256'] != generations.digest(source) or
            value['registry_sha256'] != source.get('registry_sha256') or
            value['selection_sha256'] != source.get('selection_sha256') or
            value['release_sha256'] != source.get('release_sha256') or
            value['preparation_sha256'] != generations.digest(prepared) or
            current['operation_id'] != operation or current['engine_commit'] != engine or
            current['stage'] != 'prepared' or current['preparation_sha256'] != value['preparation_sha256'] or
            current['selection_sha256'] != value['selection_sha256'] or
            current['release_sha256'] != value['release_sha256'] or
            release.get('receipt_sha256') != value['release_sha256'] or
            release.get('inputs', {}).get('inputs_sha256') != source.get('inputs_sha256')):
        raise TransactionError('initial boot differs from the exact prepared installation')
    generations.xen_identity(value['xen'])
    if (not isinstance(grant, dict) or set(grant) != {'kind','engine_commit','request_sha256',
            'granted_at','expires_at'} or grant['kind'] != 'klokast.router-initial-boot-grant.v1' or
            grant['engine_commit'] != engine or grant['request_sha256'] != generations.digest(value) or
            any(type(grant[key]) is not int for key in ('granted_at','expires_at')) or
            not grant['granted_at'] <= now < grant['expires_at'] <= grant['granted_at'] + 300):
        raise TransactionError('initial boot grant is stale or selects different inputs')
    return value


def artifact(source, target, expected, maximum):
    """Copy once to a private generation path; a retry checks the same bytes."""
    records.parents(source)
    records.secure(source, maximum=maximum)
    size = source.stat().st_size
    if not 0 < size <= maximum or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise TransactionError('initial router template boot artifact differs from its release')
    if target.exists() or target.is_symlink():
        records.parents(target)
        records.secure(target, maximum=maximum)
        if target.stat().st_size != size or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise TransactionError('initial router versioned boot artifact changed; retain the disk')
    else:
        records.atomic(target, source.read_bytes())
    return {'path':str(target), 'sha256':expected, 'bytes':size}


def boot_files(storage, source, release, operation):
    template = TEMPLATES / source['template_operation']
    records.parents(template)
    records.secure(template, directory=True)
    directory = storage.base / 'generations' / operation
    if not directory.exists() and not directory.is_symlink():
        directory.mkdir(mode=0o700)
        records.syncdir(directory.parent)
    records.secure(directory, directory=True)
    result = {}
    for name in ('kernel','initramfs'):
        expected = release['artifacts'].get(name)
        if not generations.matches('[0-9a-f]{64}', expected):
            raise TransactionError('initial router release lacks exact boot artifact hashes')
        result[name] = artifact(template / name, directory / name, expected, MAXIMUM[name])
    return result


def execute(storage, operation, engine, *, xen=Path('/etc/xen')):
    """Recover a recorded running guest, or start exactly one prepared clone."""
    work = storage.operation(operation)
    host = native.Native()
    host.guard(storage.box, deadline=time.monotonic() + 30)
    with storage.lock():
        source = records.read(work / 'request.json')
        job = records.read(work / 'candidate-job.json')
        prepared = records.read(work / 'preparation-result.json')
        release = records.read(work / 'release.json')
        profile = records.read(work / 'profile.json')
        value = records.read(work / 'initial-boot-request.json')
        grant = records.read(work / 'initial-boot-grant.json')
        current = storage.installation()
        authority(value, grant, source, prepared, release, current,
                  storage.box, operation, engine, time.time())
        router_updates.validate_release(release, profile, engine)
        preparation.validate_result(prepared, source, job)
        if (storage.pending() is not None or (storage.base / 'accepted.json').exists() or
                (storage.base / 'accepted.json').is_symlink() or
                any(path.exists() or path.is_symlink() for path in (
                    xen / 'router.cfg', xen / 'auto/router.cfg'))):
            raise TransactionError('initial boot cannot replace an assigned router')
        if (work / 'initial-enrollment-intent.json').exists() or (
                work / 'initial-enrollment-intent.json').is_symlink():
            raise TransactionError('initial boot must reconcile a started enrollment before retry')
        disk = disks.verify(work, operation, detached=False)
        if disk != current['disk'] or disks.record(work, operation)['stage'] != 'cloned':
            raise TransactionError('initial boot disk differs from its prepared clone')
        boot = boot_files(storage, source, release, operation)
        content = generations.initial_configuration(value['xen'], disk, boot)
        config = work / 'initial-router.cfg'
        if config.exists() or config.is_symlink():
            if records.secure(config).read_text() != content:
                raise TransactionError('initial router boot definition changed; retain the disk')
        else:
            records.atomic(config, content.encode())
        intent = {'kind':'klokast.router-initial-boot-intent.v1', 'box':storage.box,
                  'operation_id':operation, 'request_sha256':generations.digest(value),
                  'disk':disk, 'boot':boot, 'config_sha256':hashlib.sha256(content.encode()).hexdigest()}
        intent_path = work / 'initial-boot-intent.json'
        if intent_path.exists() or intent_path.is_symlink():
            if records.read(intent_path) != intent:
                raise TransactionError('initial boot intent changed; retain the disk')
        else:
            records.write(intent_path, intent)
        deadline = time.monotonic() + 90
        expected = native.literal_configuration(content)
        live = host.initial_guest(disk, expected, deadline=deadline)
        if live is None:
            # No router autostart definition is installed before acceptance.
            host.bridges(value['xen'])
            host.detached([disk['path']], deadline=deadline)
            for item in boot.values():
                host.artifact(item, deadline=deadline)
            authority(value, grant, source, prepared, release, current,
                      storage.box, operation, engine, time.time())
            native.command(['/usr/sbin/xl','create',config], deadline, maximum_seconds=60)
            live = host.initial_guest(disk, expected, deadline=deadline)
            if live is None:
                raise TransactionError('initial router did not start with its exact recorded assignment')
        result = {'kind':'klokast.router-initial-boot-result.v1', 'box':storage.box,
                  'operation_id':operation, 'status':'running-first-contact',
                  'request_sha256':generations.digest(value), 'intent_sha256':generations.digest(intent),
                  'xen_uuid':value['xen']['uuid'], 'disk':disk, 'boot':boot,
                  'domain_id':live['domid']}
        records.write(work / 'initial-boot-result.json', result)
        return result
