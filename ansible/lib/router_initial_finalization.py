"""Stop one enrolled first router and retire first-contact access offline."""
import os
from pathlib import Path
import re
import time
import uuid

import router_candidate_preparation as preparation
import router_candidate_disk as disks
import router_generations as generations
import router_initial_installation as installation
import router_native as native
import router_personalize as personalize
import router_records as records
import router_updates
from router_transaction import TransactionError
from xen_build_runtime import (attach_loop, boot_guest, checksum, detach_loop,
    domain, duplicate_fields, loop_devices, read_slot, safe_directory, safe_file)

MIB = 1024 * 1024


def job_for(box,operation,source,preparation_job,prepared,release,enrollment):
    return {'kind':'klokast.router-initial-finalization-job.v1',
        'box':box,'operation_id':operation,'inputs_sha256':source['inputs_sha256'],
        'job_sha256':generations.digest(preparation_job),'preparation_job':preparation_job,
        'manifest':release['inputs'],'prepared':prepared['prepared'],
        'first_contact':prepared['first_contact'],
        'runtime_packages':release['runtime_packages'],'enrolled_guest':enrollment}


def context(storage, operation, engine):
    work = storage.operation(operation)
    current = storage.installation()
    installation.validate(current, storage.box)
    if (current['stage'] != 'enrolled' or current['operation_id'] != operation or
            current['engine_commit'] != engine):
        raise TransactionError('offline initial finalization requires the exact enrolled installation')
    prepared_request = records.read(work / 'request.json')
    preparation_job = records.read(work / 'candidate-job.json')
    prepared = records.read(work / 'preparation-result.json')
    release = records.read(work / 'release.json')
    profile = records.read(work / 'profile.json')
    boot_request = records.read(work / 'initial-boot-request.json')
    boot_intent = records.read(work / 'initial-boot-intent.json')
    enrollment = records.read(work / 'initial-enrollment-guest.json')
    if (prepared_request.get('source_operation') is None or
            prepared_request.get('operation_id') != operation or
            prepared_request.get('box') != storage.box or
            prepared_request.get('engine_commit') != engine or
            boot_request.get('operation_id') != operation or
            boot_intent.get('request_sha256') != generations.digest(boot_request) or
            boot_intent.get('disk') != current['disk'] or
            enrollment.get('box') != storage.box or enrollment.get('operation_id') != operation or
            enrollment.get('machine_id') != current['machine_id'] or
            generations.digest(enrollment) != current['enrollment_sha256'] or
            release.get('receipt_sha256') != current['release_sha256']):
        raise TransactionError('initial finalization differs from its prepared disk or enrolled machine')
    router_updates.validate_release(release,profile,engine)
    preparation.validate_result(prepared,prepared_request,preparation_job)
    if generations.digest(prepared) != current['preparation_sha256']:
        raise TransactionError('initial finalization preparation changed after enrollment')
    return work,current,prepared_request,preparation_job,prepared,release,boot_request,boot_intent,enrollment


def request(value, job, source, release, current, box, operation, engine, expected_job):
    fields = {'kind','box','operation_id','engine_commit','source_operation','inputs_sha256',
              'source_request_sha256','release_sha256','enrollment_sha256',
              'job_sha256','bootstrap'}
    if (not isinstance(value,dict) or set(value) != fields or
            value['kind'] != 'klokast.router-initial-finalization.v1' or
            value['box'] != box or value['operation_id'] != operation or
            value['engine_commit'] != engine or
            value['inputs_sha256'] != source.get('inputs_sha256') or
            value['source_operation'] != source.get('source_operation') or
            not generations.matches('[0-9a-f]{24}',value['source_operation']) or
            value['source_request_sha256'] != generations.digest(source) or
            value['release_sha256'] != release['receipt_sha256'] or
            value['enrollment_sha256'] != current['enrollment_sha256'] or
            job != expected_job or value['job_sha256'] != generations.digest(job) or
            not isinstance(value['bootstrap'],dict) or
            set(value['bootstrap']) != {'kernel','initramfs'}):
        raise TransactionError('initial finalization request differs from its enrolled installation')
    for name,maximum in (('kernel',32*MIB),('initramfs',1024*MIB)):
        item = value['bootstrap'][name]
        if (not isinstance(item,dict) or set(item) != {'bytes','sha256'} or
                type(item['bytes']) is not int or not 0 < item['bytes'] <= maximum or
                not generations.matches('[0-9a-f]{64}',item['sha256'])):
            raise TransactionError('initial finalization boot artifact identity is invalid')
    return value


def grant(value, current, role, engine, now):
    if (not isinstance(value,dict) or set(value) != {'kind','engine_commit',
            'request_sha256','installation_sha256','granted_at','expires_at'} or
            value['kind'] != 'klokast.router-initial-' + role + '-grant.v1' or
            value['engine_commit'] != engine or
            value['request_sha256'] != current[0] or
            value['installation_sha256'] != current[1] or
            any(type(value[key]) is not int for key in ('granted_at','expires_at')) or
            not value['granted_at'] <= now < value['expires_at'] <= value['granted_at'] + 900):
        raise TransactionError('initial finalization grant is stale or selects different evidence')


def stop(storage, operation, engine):
    """Persist the stopped-disk intent before shutting down the exact guest."""
    host = native.Native()
    host.guard(storage.box,deadline=time.monotonic()+30)
    with storage.lock():
        (work,current,source,preparation_job,prepared,release,
         boot_request,boot_intent,enrollment) = context(storage,operation,engine)
        final = storage.operation(operation) / 'finalization'
        records.secure(final,directory=True)
        value = records.read(final / 'request.json')
        job = records.read(final / 'job.json')
        request(value,job,source,release,current,storage.box,operation,engine,
                job_for(storage.box,operation,source,preparation_job,prepared,release,enrollment))
        authorization = records.read(final / 'stop-grant.json')
        grant(authorization,(generations.digest(value),current['record_sha256']),
              'stop',engine,time.time())
        if storage.pending() is not None or (storage.base / 'accepted.json').exists() or (
                storage.base / 'accepted.json').is_symlink():
            raise TransactionError('initial finalization cannot stop an assigned router')
        disk = disks.verify(work,operation,detached=False)
        if disk != current['disk']:
            raise TransactionError('initial finalization disk UUID changed')
        expected = native.literal_configuration(
            generations.initial_configuration(boot_request['xen'],disk,boot_intent['boot']))
        intent = {'kind':'klokast.router-initial-stop-intent.v1','box':storage.box,
            'operation_id':operation,'engine_commit':engine,'request_sha256':generations.digest(value),
            'installation_sha256':current['record_sha256'],'disk':disk,
            'xen_uuid':boot_request['xen']['uuid'],'enrollment_sha256':current['enrollment_sha256']}
        path = final / 'stop-intent.json'
        if path.exists() or path.is_symlink():
            if records.read(path) != intent:
                raise TransactionError('initial router stop intent changed; retain the enrolled disk')
        else:
            records.write(path,intent)
        grant(authorization,(generations.digest(value),current['record_sha256']),
              'stop',engine,time.time())
        host.stop_initial(disk,expected,deadline=time.monotonic()+75)
        result = {'kind':'klokast.router-initial-stop-result.v1','box':storage.box,
                  'operation_id':operation,'status':'stopped-for-offline-finalization',
                  'intent_sha256':generations.digest(intent),'disk':disk}
        records.write(final / 'stop-result.json',result)
        return result


def boot_identity(final, value):
    for name,maximum in (('kernel',32*MIB),('initramfs',1024*MIB)):
        item = value['bootstrap'][name]
        path = final / ('bootstrap-' + name)
        safe_file(path,maximum)
        if path.stat().st_size != item['bytes'] or checksum(path) != item['sha256']:
            raise TransactionError('initial finalizer bootstrap artifact changed')


def configuration(final,value,disk,result_loop,identity):
    name = 'router-initial-finalize-' + value['operation_id']
    extra = ('console=hvc0 panic=1 klokast_operation=' + value['operation_id'] +
             ' klokast_inputs=' + value['inputs_sha256'] +
             ' klokast_job=' + value['job_sha256'])
    attached = ['phy:' + disk['path'] + ',xvda,w','phy:' + result_loop + ',xvdb,w']
    content = (f'name = {name!r}\nuuid = {identity!r}\ntype = "pvh"\nmemory = 2048\nmaxmem = 2048\n'
               f'vcpus = 2\nkernel = {str(final / "bootstrap-kernel")!r}\n'
               f'ramdisk = {str(final / "bootstrap-initramfs")!r}\nextra = {extra!r}\n'
               f'disk = {attached!r}\n'
               'vif = []\non_poweroff = "destroy"\non_reboot = "destroy"\non_crash = "destroy"\n')
    path = final / 'finalize.cfg'
    with path.open('x') as stream:
        stream.write(content)
        stream.flush(); os.fsync(stream.fileno())
    return name,path


def execute(storage,operation,engine):
    """Run one stopped disk in a networkless finalizer or recover its result."""
    host = native.Native()
    host.guard(storage.box,deadline=time.monotonic()+30)
    with storage.lock():
        (work,current,source,preparation_job,prepared,release,
         boot_request,boot_intent,enrollment) = context(storage,operation,engine)
        final = work / 'finalization'
        safe_directory(final)
        value,job = records.read(final / 'request.json'),records.read(final / 'job.json')
        request(value,job,source,release,current,storage.box,operation,engine,
                job_for(storage.box,operation,source,preparation_job,prepared,release,enrollment))
        grant_value = records.read(final / 'run-grant.json')
        grant(grant_value,(generations.digest(value),current['record_sha256']),
              'finalize',engine,time.time())
        stopped = records.read(final / 'stop-result.json')
        stop_intent = records.read(final / 'stop-intent.json')
        if (stopped.get('intent_sha256') != generations.digest(stop_intent) or
                stopped.get('disk') != current['disk'] or
                stop_intent.get('request_sha256') != generations.digest(value)):
            raise TransactionError('initial finalizer has no exact stopped guest evidence')
        disk = disks.verify(work,operation)
        if disk != current['disk']:
            raise TransactionError('initial finalization disk differs from its stopped installation')
        boot_identity(final,value)
        name = 'router-initial-finalize-' + operation
        if domain(name) is not None:
            raise TransactionError('initial finalizer guest remains; reconcile its exact domain')
        slot,config,ledger_path,result_path = (final / name for name in (
            'result.slot','finalize.cfg','run.json','result.json'))
        binding = {'kind':'klokast.router-initial-finalization-run.v1',
            'operation_id':operation,'request_sha256':generations.digest(value),'disk':disk}
        ledger = None
        result_loop = None
        if ledger_path.exists() or ledger_path.is_symlink():
            ledger = records.read(ledger_path)
            if (not isinstance(ledger,dict) or
                    any(ledger.get(key) != item for key,item in binding.items()) or
                    set(ledger) != set(binding) | {'stage','uuid','config_sha256','result_loop','result_sha256'} or
                    ledger['stage'] not in ('booting','complete') or
                    not generations.matches('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',ledger['uuid']) or
                    not re.fullmatch(r'/dev/loop[0-9]+',ledger['result_loop']) or
                    not generations.matches('[0-9a-f]{64}',ledger['config_sha256'])):
                raise TransactionError('initial finalizer run differs from its recorded guest')
            safe_file(config,MIB); safe_file(slot,MIB)
            if checksum(config) != ledger['config_sha256'] or slot.stat().st_size != MIB:
                raise TransactionError('initial finalizer Xen definition or result slot changed')
            attached = loop_devices(slot)
            if attached:
                if attached != [ledger['result_loop']]:
                    raise TransactionError('initial finalizer result loop differs from its record')
                host.wait_detached(attached,deadline=time.monotonic()+30)
                result_loop = attached[0]
        elif any(path.exists() or path.is_symlink() for path in (slot,config,result_path)):
            raise TransactionError('initial finalizer has unrecorded run files')
        try:
            if ledger is None:
                with slot.open('xb') as stream:
                    os.posix_fallocate(stream.fileno(),0,MIB)
                    os.fsync(stream.fileno())
                result_loop = attach_loop(slot)
                identity = str(uuid.uuid4())
                name,config = configuration(final,value,disk,result_loop,identity)
                ledger = {**binding,'stage':'booting','uuid':identity,
                    'config_sha256':checksum(config),'result_loop':result_loop,'result_sha256':None}
                records.write(ledger_path,ledger)
                grant(grant_value,(generations.digest(value),current['record_sha256']),
                      'finalize',engine,time.time())
                boot_guest(final,name,identity,config,value,
                    kind='klokast.router-initial-finalization-result.v1',timeout=420)
            if domain(name) is not None or disks.verify(work,operation) != disk:
                raise TransactionError('initial finalizer did not detach its exact enrolled disk')
            result = read_slot(slot)
            if (not isinstance(result,dict) or result.get('kind') !=
                    'klokast.router-initial-finalization-result.v1' or
                    result.get('operation_id') != operation or
                    result.get('inputs_sha256') != value.get('inputs_sha256') or
                    result.get('job_sha256') != value['job_sha256'] or
                    result.get('success') is not True or
                    result.get('machine_id') != enrollment['machine_id'] or
                    result.get('finalized',{}).get('packages') != release['runtime_packages'] or
                    result.get('finalized',{}).get('tests') != release['runtime_tests'] or
                    result.get('finalized',{}).get('enrolled_state_preserved') is not True or
                    result.get('state_sha256') != personalize.digest(result.get('state')) or
                    result.get('state',{}).get('var/lib/tailscale/tailscaled.state',{}).get('sha256') is None):
                raise TransactionError('initial finalizer returned incomplete or different offline evidence')
            digest = generations.digest(result)
            if ledger['stage'] == 'complete' and ledger['result_sha256'] != digest:
                raise TransactionError('initial finalizer result changed after completion')
            if result_path.exists() or result_path.is_symlink():
                if records.read(result_path) != result:
                    raise TransactionError('initial finalizer has a conflicting completion record')
            else:
                records.write(result_path,result)
            if ledger['stage'] != 'complete':
                records.write(ledger_path,{**ledger,'stage':'complete','result_sha256':digest})
            return {'kind':'klokast.router-initial-finalization-complete.v1',
                    'box':storage.box,'operation_id':operation,'status':'offline-finalized',
                    'result_sha256':digest,'disk':disk}
        finally:
            if domain(name) is not None:
                raise TransactionError('initial finalizer remains; retain its disk and result')
            if result_loop is not None:
                host.wait_detached([result_loop],deadline=time.monotonic()+30)
                detach_loop(slot,result_loop)
