"""Fence a single first-router Tailnet enrollment before any key is minted."""
import secrets
import time

import router_generations as generations
import router_initial_installation as installation
import router_native as native
import router_records as records
from router_transaction import TransactionError


def authority(grant, boot_request, boot_intent, current, box, operation, engine, now):
    installation.validate(current, box)
    if (not isinstance(boot_request, dict) or boot_request.get('box') != box or
            boot_request.get('operation_id') != operation or boot_request.get('engine_commit') != engine or
            not isinstance(boot_intent, dict) or boot_intent.get('box') != box or
            boot_intent.get('operation_id') != operation or
            boot_intent.get('request_sha256') != generations.digest(boot_request) or
            boot_intent.get('disk') != current['disk'] or current['stage'] not in ('prepared','enrolled') or
            current['operation_id'] != operation or current['engine_commit'] != engine or
            current['preparation_sha256'] != boot_request.get('preparation_sha256')):
        raise TransactionError('first enrollment lacks its exact running prepared router')
    if (not isinstance(grant, dict) or set(grant) != {'kind','box','operation_id','engine_commit',
            'boot_request_sha256','boot_intent_sha256','granted_at','expires_at'} or
            grant['kind'] != 'klokast.router-initial-enrollment-grant.v1' or
            grant['box'] != box or grant['operation_id'] != operation or
            grant['engine_commit'] != engine or
            grant['boot_request_sha256'] != generations.digest(boot_request) or
            grant['boot_intent_sha256'] != generations.digest(boot_intent) or
            any(type(grant[key]) is not int for key in ('granted_at','expires_at')) or
            not grant['granted_at'] <= now < grant['expires_at'] <= grant['granted_at'] + 300):
        raise TransactionError('first enrollment grant is stale or selects another guest')


def begin(storage, operation, engine):
    """Record one enrollment attempt; a retry never authorizes another key."""
    work = storage.operation(operation)
    host = native.Native()
    host.guard(storage.box, deadline=time.monotonic() + 30)
    with storage.lock():
        current = storage.installation()
        boot_request = records.read(work / 'initial-boot-request.json')
        boot_intent = records.read(work / 'initial-boot-intent.json')
        grant = records.read(work / 'initial-enrollment-grant.json')
        authority(grant, boot_request, boot_intent, current,
                  storage.box, operation, engine, time.time())
        if (storage.pending() is not None or (storage.base / 'accepted.json').exists() or
                (storage.base / 'accepted.json').is_symlink()):
            raise TransactionError('first enrollment cannot replace an assigned router')
        disk = current['disk']
        config = native.literal_configuration(
            generations.initial_configuration(boot_request['xen'], disk, boot_intent['boot']))
        live = host.initial_guest(disk, config, deadline=time.monotonic() + 30)
        if live is None:
            raise TransactionError('first enrollment requires the exact running router guest')
        identity = {'kind':'klokast.router-initial-enrollment-intent.v1','box':storage.box,
                    'operation_id':operation,'engine_commit':engine,
                    'boot_request_sha256':generations.digest(boot_request),
                    'boot_intent_sha256':generations.digest(boot_intent),
                    'xen_uuid':boot_request['xen']['uuid'],'disk':disk}
        path = work / 'initial-enrollment-intent.json'
        if path.exists() or path.is_symlink():
            existing = records.read(path)
            if (not isinstance(existing, dict) or
                    {key:value for key,value in existing.items() if key not in ('attempt','created_at')} != identity or
                    not generations.matches('[0-9a-f]{24}',existing.get('attempt')) or
                    type(existing.get('created_at')) is not int):
                raise TransactionError('first enrollment intent changed; reconcile the recorded attempt')
            intent, mint_permitted = existing, False
        else:
            if current['stage'] != 'prepared':
                raise TransactionError('recorded enrollment lacks its exact original intent')
            intent = {**identity, 'attempt':secrets.token_hex(12), 'created_at':int(time.time())}
            records.write(path,intent)
            mint_permitted = True
        result = {'kind':'klokast.router-initial-enrollment-begin.v1','box':storage.box,
                  'operation_id':operation,'attempt':intent['attempt'],
                  'intent_sha256':generations.digest(intent),
                  'xen_uuid':intent['xen_uuid'],'disk':disk,
                  'mint_permitted':mint_permitted}
        records.write(work / 'initial-enrollment-begin.json',result)
        return result


def finish(storage, operation, engine):
    """Record one verified guest identity; later offline work still must pass."""
    work = storage.operation(operation)
    host = native.Native()
    host.guard(storage.box, deadline=time.monotonic() + 30)
    with storage.lock():
        current = storage.installation()
        installation.validate(current, storage.box)
        boot_request = records.read(work / 'initial-boot-request.json')
        boot_intent = records.read(work / 'initial-boot-intent.json')
        intent = records.read(work / 'initial-enrollment-intent.json')
        guest = records.read(work / 'initial-enrollment-guest.json')
        grant = records.read(work / 'initial-enrollment-finish-grant.json')
        if (not isinstance(guest, dict) or set(guest) != {'kind','box','operation_id','attempt',
                'machine_id','hostname','tags','ssh','state_sha256'} or
                guest['kind'] != 'klokast.router-initial-enrollment-guest.v1' or
                guest['box'] != storage.box or guest['operation_id'] != operation or
                guest['attempt'] != intent.get('attempt') or
                guest['hostname'] != storage.box + '-router' or guest['tags'] != ['tag:vm'] or
                guest['ssh'] is not True or
                not generations.matches('[A-Za-z0-9_-]{1,128}',guest['machine_id']) or
                not generations.matches('[0-9a-f]{64}',guest['state_sha256']) or
                boot_request.get('kind') != 'klokast.router-initial-boot-request.v1' or
                boot_request.get('box') != storage.box or boot_request.get('operation_id') != operation or
                boot_request.get('engine_commit') != engine or
                boot_intent.get('kind') != 'klokast.router-initial-boot-intent.v1' or
                boot_intent.get('disk') != current['disk'] or
                intent.get('kind') != 'klokast.router-initial-enrollment-intent.v1' or
                intent.get('box') != storage.box or intent.get('operation_id') != operation or
                intent.get('engine_commit') != engine or
                intent.get('boot_intent_sha256') != generations.digest(boot_intent) or
                intent.get('boot_request_sha256') != generations.digest(boot_request) or
                intent.get('xen_uuid') != boot_request.get('xen',{}).get('uuid') or
                intent.get('disk') != current['disk'] or
                current['operation_id'] != operation or current['engine_commit'] != engine or
                current['stage'] not in ('prepared','enrolled')):
            raise TransactionError('first enrollment result differs from its exact boot and attempt')
        if (not isinstance(grant, dict) or set(grant) != {'kind','box','operation_id',
                'engine_commit','intent_sha256','guest_sha256','granted_at','expires_at'} or
                grant['kind'] != 'klokast.router-initial-enrollment-finish-grant.v1' or
                grant['box'] != storage.box or grant['operation_id'] != operation or
                grant['engine_commit'] != engine or
                grant['intent_sha256'] != generations.digest(intent) or
                grant['guest_sha256'] != generations.digest(guest) or
                any(type(grant[key]) is not int for key in ('granted_at','expires_at')) or
                not grant['granted_at'] <= time.time() < grant['expires_at'] <= grant['granted_at'] + 300):
            raise TransactionError('first enrollment finish grant is stale or selects different evidence')
        if storage.pending() is not None or (storage.base / 'accepted.json').exists() or (
                storage.base / 'accepted.json').is_symlink():
            raise TransactionError('first enrollment cannot replace an accepted router')
        config = native.literal_configuration(
            generations.initial_configuration(boot_request['xen'],current['disk'],boot_intent['boot']))
        if host.initial_guest(current['disk'], config, deadline=time.monotonic() + 30) is None:
            raise TransactionError('enrolled router guest is not running with its recorded Xen identity')
        if current['stage'] == 'prepared':
            current = storage.record_installation(generations.seal({
                **{key:value for key,value in current.items() if key != 'record_sha256'},
                'stage':'enrolled','enrollment_sha256':generations.digest(guest),
                'machine_id':guest['machine_id']}))
        elif (current['enrollment_sha256'] != generations.digest(guest) or
                current['machine_id'] != guest['machine_id']):
            raise TransactionError('first enrollment retry differs from its recorded identity')
        result = {'kind':'klokast.router-initial-enrollment-result.v1','box':storage.box,
                  'operation_id':operation,'status':'enrolled-first-contact',
                  'machine_id':guest['machine_id'], 'state_sha256':guest['state_sha256'],
                  'guest_sha256':generations.digest(guest),'installation':current}
        records.write(work / 'initial-enrollment-result.json',result)
        return result
