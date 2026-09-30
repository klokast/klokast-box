"""Bind replacement cleanup inputs and its result to one enrolled generation.

These checks do not start a Xen guest or authorize a cutover. The dom0 runner
must verify the stopped candidate, staged boot inputs, and detached output.
"""
import json
import os
from pathlib import Path
import re
import time
import uuid

import router_candidate
import router_candidate_preparation as preparation
import router_generations as generations
import router_replacement_enrollment as enrollment
import router_records as records
import router_transaction as transaction
from xen_build_runtime import (attach_loop, boot_guest, checksum, detach_loop,
    domain, loop_devices, read_slot, require_identity, run, safe_directory, safe_file)

MIB = 1024 * 1024


def job_for(request, candidate, preparation_request, preparation_job, prepared,
            release, attempt, enrolled):
    transaction.validate(request)
    generations.generation(candidate, request['box'])
    enrollment.validate_attempt(attempt, request)
    enrollment.result(enrolled, request, attempt)
    router_candidate.validate(preparation_job)
    preparation.validate_result(prepared, preparation_request, preparation_job)
    if (candidate['record_sha256'] != request['candidate_sha256'] or
            preparation_request.get('box') != request['box'] or
            preparation_request.get('operation_id') != request['operation_id'] or
            preparation_request.get('engine_commit') != request['engine_commit'] or
            preparation_request.get('inputs_sha256') != preparation_job['inputs_sha256'] or
            preparation_request.get('job_sha256') != generations.digest(preparation_job) or
            preparation_job['operation_id'] != request['operation_id'] or
            preparation_job['engine_commit'] != request['engine_commit'] or
            prepared['prepared']['accounts'] != candidate['accounts'] or
            prepared['prepared']['tailscale'] != candidate['tailscale'] or
            release.get('receipt_sha256') != candidate['release_sha256'] or
            release.get('inputs', {}).get('inputs_sha256') != preparation_job['inputs_sha256'] or
            release.get('runtime_packages') != candidate['packages'] or
            release.get('runtime_packages') != preparation_job['runtime_packages'] or
            prepared['first_contact']['host_key_public_sha256'] !=
                enrolled['host_key_public_sha256']):
        raise transaction.TransactionError('replacement cleanup differs from its prepared or enrolled generation')
    return {'kind':'klokast.router-replacement-finalization-job.v1',
        'box':request['box'],'operation_id':request['operation_id'],
        'inputs_sha256':preparation_job['inputs_sha256'],
        'job_sha256':generations.digest(preparation_job),
        'preparation_job':preparation_job,'manifest':release['inputs'],
        'prepared':prepared['prepared'],'first_contact':prepared['first_contact'],
        'runtime_packages':release['runtime_packages'],'enrolled_guest':enrolled}


def result(value, job, release, enrolled):
    expected = {'kind','operation_id','inputs_sha256','job_sha256',
                'success','finalized','state','state_sha256','machine_id'}
    state = value.get('state') if isinstance(value, dict) else None
    finalized = value.get('finalized') if isinstance(value, dict) else None
    tailscale_state = state.get('var/lib/tailscale/tailscaled.state') if isinstance(state,dict) else None
    if (not isinstance(value,dict) or set(value) != expected or
            value['kind'] != 'klokast.router-replacement-finalization-result.v1' or
            value['operation_id'] != job['operation_id'] or
            value['inputs_sha256'] != job['inputs_sha256'] or
            value['job_sha256'] != generations.digest(job) or
            value['success'] is not True or
            value['machine_id'] != enrolled['machine_id'] or
            not isinstance(state,dict) or
            value['state_sha256'] != generations.digest(state) or
            not isinstance(tailscale_state,dict) or
            tailscale_state.get('sha256') !=
                enrolled['state_sha256'] or
            not isinstance(finalized,dict) or
            finalized.get('packages') != release['runtime_packages'] or
            finalized.get('tests') != release['runtime_tests'] or
            finalized.get('enrolled_state_preserved') is not True):
        raise transaction.TransactionError('replacement cleanup returned incomplete or different offline evidence')
    return value


def assets(final, request, preparation_request, release):
    safe_directory(final)
    value = records.read(final / 'request.json')
    if (not isinstance(value,dict) or set(value) != {
            'kind','box','operation_id','engine_commit','inputs_sha256',
            'preparation_sha256','release_sha256','bootstrap'} or
            value['kind'] != 'klokast.router-replacement-finalizer-assets.v1' or
            any(value[key] != request[key] for key in ('box','operation_id','engine_commit')) or
            value['inputs_sha256'] != preparation_request['inputs_sha256'] or
            value['preparation_sha256'] != generations.digest(preparation_request) or
            value['release_sha256'] != release['receipt_sha256'] or
            not isinstance(value['bootstrap'],dict) or
            set(value['bootstrap']) != {'kernel','initramfs'}):
        raise transaction.TransactionError('replacement finalizer boot assets differ from preparation')
    for name,maximum in (('kernel',32*MIB),('initramfs',1024*MIB)):
        expected = value['bootstrap'][name]
        path = final / ('bootstrap-' + name)
        if (not isinstance(expected,dict) or set(expected) != {'bytes','sha256'} or
                type(expected['bytes']) is not int or
                not 0 < expected['bytes'] <= maximum or
                not generations.matches('[0-9a-f]{64}',expected['sha256'])):
            raise transaction.TransactionError('replacement finalizer has an invalid boot artifact identity')
        safe_file(path,maximum)
        if path.stat().st_size != expected['bytes'] or checksum(path) != expected['sha256']:
            raise transaction.TransactionError('replacement finalizer boot artifact changed')
    return value


def configuration(final,job,disk,result_loop,job_loop,identity):
    name = 'router-replacement-finalize-' + job['operation_id']
    extra = ('console=hvc0 panic=1 klokast_operation=' + job['operation_id'] +
             ' klokast_inputs=' + job['inputs_sha256'] +
             ' klokast_job=' + generations.digest(job))
    attached = ['phy:' + disk['path'] + ',xvda,w',
                'phy:' + result_loop + ',xvdb,w',
                'phy:' + job_loop + ',xvdc,r']
    content = (f'name = {name!r}\nuuid = {identity!r}\ntype = "pvh"\n'
               'memory = 2048\nmaxmem = 2048\nvcpus = 2\n'
               f'kernel = {str(final / "bootstrap-kernel")!r}\n'
               f'ramdisk = {str(final / "bootstrap-initramfs")!r}\n'
               f'extra = {extra!r}\ndisk = {attached!r}\n'
               'vif = []\non_poweroff = "destroy"\non_reboot = "destroy"\non_crash = "destroy"\n')
    path = final / 'finalize.cfg'
    with path.open('x') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return name,path


def run_candidate(adapter, *, deadline):
    """Use one stopped B disk in a networkless, one-shot cleanup guest."""
    request,work = adapter.request,adapter.work
    candidate = adapter.pair['candidate']
    if adapter.host.guest(adapter.pair,deadline=deadline) is not None:
        raise transaction.TransactionError('replacement cleanup requires both production routers stopped')
    adapter.host.detached([value['disk']['path'] for value in adapter.pair.values()],deadline=deadline)
    for side in ('old','candidate'):
        adapter.host.disk(adapter.pair[side]['disk'],deadline=deadline)
    preparation_request = records.read(work / 'request.json')
    preparation_job = records.read(work / 'candidate-job.json')
    prepared = records.read(work / 'preparation-result.json')
    release = records.read(work / 'release.json')
    attempt = records.read(work / 'enrollment-attempt.json')
    enrolled = records.read(work / 'enrollment-result.json')
    job = job_for(request,candidate,preparation_request,preparation_job,prepared,
                  release,attempt,enrolled)
    final = work / 'finalization'
    assets(final,request,preparation_request,release)
    name = 'router-replacement-finalize-' + request['operation_id']
    if domain(name) is not None:
        raise transaction.TransactionError('replacement cleanup guest remains; reconcile its exact domain')
    if any((final / name).exists() or (final / name).is_symlink() for name in
           ('job.json','job.slot','result.slot','finalize.cfg','run.json','result.json')):
        raise transaction.TransactionError('replacement cleanup already attempted; recover the router transaction')
    body = json.dumps(job,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    if not 0 < len(body) < MIB - 1:
        raise transaction.TransactionError('replacement cleanup runtime job exceeds its private slot')
    records.write(final / 'job.json',job)
    slot = final / 'job.slot'
    with slot.open('xb') as stream:
        os.posix_fallocate(stream.fileno(),0,MIB)
        stream.write(body+b'\0')
        os.fsync(stream.fileno())
    result_slot = final / 'result.slot'
    with result_slot.open('xb') as stream:
        os.posix_fallocate(stream.fileno(),0,MIB)
        os.fsync(stream.fileno())
    result_loop,job_loop = None,None
    try:
        result_loop = attach_loop(result_slot)
        job_loop = attach_loop(slot,readonly=True)
        identity = str(uuid.uuid4())
        name,config = configuration(final,job,candidate['disk'],result_loop,job_loop,identity)
        ledger = {'kind':'klokast.router-replacement-finalizer-run.v1',
            'operation_id':request['operation_id'],'request_sha256':generations.digest(request),
            'job_sha256':generations.digest(job),'disk':candidate['disk'],
            'uuid':identity,'config_sha256':checksum(config),
            'result_loop':result_loop,'job_loop':job_loop}
        records.write(final / 'run.json',ledger)
        remaining = int(deadline - time.monotonic() - 30)
        if remaining < 60:
            raise transaction.TransactionError('replacement cleanup has insufficient cutover time')
        boot_guest(final,name,identity,config,job,
            kind='klokast.router-replacement-finalization-result.v1',
            timeout=min(420,remaining))
        if domain(name) is not None:
            raise transaction.TransactionError('replacement cleanup guest did not stop')
        adapter.host.detached([candidate['disk']['path']],deadline=deadline)
        value = result(read_slot(result_slot),job,release,enrolled)
        records.write(final / 'result.json',value)
        return value
    finally:
        if domain(name) is not None:
            raise transaction.TransactionError('replacement cleanup guest remains; retain its disk')
        for path,device in ((slot,job_loop),(result_slot,result_loop)):
            if device is not None:
                adapter.host.wait_detached([device],deadline=deadline)
                detach_loop(path,device)


def fence(adapter, *, deadline):
    """Stop an interrupted cleanup guest before any reverse copy or router boot."""
    final = adapter.work / 'finalization'
    name = 'router-replacement-finalize-' + adapter.request['operation_id']
    ledger_path = final / 'run.json'
    current = domain(name)
    if not ledger_path.exists() and not ledger_path.is_symlink():
        if current is not None:
            raise transaction.TransactionError('unrecorded replacement cleanup guest blocks recovery')
        for path in (final / 'job.slot',final / 'result.slot'):
            if path.exists() or path.is_symlink():
                safe_file(path,MIB)
                attached = loop_devices(path)
                if len(attached) > 1:
                    raise transaction.TransactionError('replacement cleanup has ambiguous unrecorded loops')
                if attached:
                    adapter.host.wait_detached(attached,deadline=deadline)
                    detach_loop(path,attached[0])
        return
    ledger = records.read(ledger_path)
    if (not isinstance(ledger,dict) or set(ledger) != {
            'kind','operation_id','request_sha256','job_sha256','disk','uuid',
            'config_sha256','result_loop','job_loop'} or
            ledger['kind'] != 'klokast.router-replacement-finalizer-run.v1' or
            ledger['operation_id'] != adapter.request['operation_id'] or
            ledger['request_sha256'] != generations.digest(adapter.request) or
            ledger['disk'] != adapter.pair['candidate']['disk'] or
            not generations.matches('[0-9a-f]{64}',ledger['job_sha256']) or
            not generations.matches('[0-9a-f]{64}',ledger['config_sha256']) or
            not generations.matches('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',ledger['uuid']) or
            any(not generations.matches('/dev/loop[0-9]+',ledger[key]) for key in ('result_loop','job_loop')) or
            ledger['result_loop'] == ledger['job_loop']):
        raise transaction.TransactionError('replacement cleanup recovery ledger is invalid')
    config = final / 'finalize.cfg'
    safe_file(config,MIB)
    if (checksum(config) != ledger['config_sha256'] or
            generations.digest(records.read(final / 'job.json')) != ledger['job_sha256']):
        raise transaction.TransactionError('replacement cleanup recovery source changed')
    if current is not None:
        require_identity(current,ledger['uuid'])
        run(['xl','destroy',str(current['domid'])],timeout=max(1,min(30,int(deadline-time.monotonic()))))
        if domain(name) is not None:
            raise transaction.TransactionError('replacement cleanup guest remains after fencing')
    for path,key in ((final / 'job.slot','job_loop'),(final / 'result.slot','result_loop')):
        attached = loop_devices(path)
        if attached:
            if attached != [ledger[key]]:
                raise transaction.TransactionError('replacement cleanup loop changed during recovery')
            adapter.host.wait_detached(attached,deadline=deadline)
            detach_loop(path,ledger[key])
