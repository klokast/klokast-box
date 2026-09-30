"""Stage one proposed replacement generation without boot or cutover authority."""
from pathlib import Path
import time

import router_candidate_disk as disks
import router_candidate_generation as candidate_generation
import router_candidate_preparation as preparation
from router_generation_boot import boot_files
import router_generations as generations
import router_native as native
import router_records as records
import router_replacement_preparation as replacement
from router_transaction import TransactionError


def authority(value, grant, source, prepared, disk_record, release,
              box, operation, engine, now):
    fields = {'kind','box','operation_id','engine_commit','source_request_sha256',
              'preparation_sha256','candidate_disk_sha256','accepted_assignment_sha256',
              'old_sha256','release_sha256','xen_uuid'}
    if (not isinstance(value,dict) or set(value) != fields or
            value['kind'] != 'klokast.router-replacement-generation-selection.v1' or
            (value['box'],value['operation_id'],value['engine_commit']) != (box,operation,engine) or
            any(not generations.matches('[0-9a-f]{64}',value[name]) for name in (
                'source_request_sha256','preparation_sha256','candidate_disk_sha256',
                'accepted_assignment_sha256','old_sha256','release_sha256')) or
            not generations.matches('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',
                                    value['xen_uuid']) or
            value['source_request_sha256'] != generations.digest(source) or
            value['preparation_sha256'] != generations.digest(prepared) or
            value['candidate_disk_sha256'] != generations.digest(disk_record) or
            value['accepted_assignment_sha256'] != source['accepted_assignment_sha256'] or
            value['old_sha256'] != source['old_sha256'] or
            value['release_sha256'] != release['receipt_sha256']):
        raise TransactionError('replacement generation selection differs from the retained preparation')
    if (not isinstance(grant,dict) or set(grant) != {
            'kind','engine_commit','selection_sha256','granted_at','expires_at'} or
            grant['kind'] != 'klokast.router-replacement-generation-grant.v1' or
            grant['engine_commit'] != engine or
            grant['selection_sha256'] != generations.digest(value) or
            any(type(grant[name]) is not int for name in ('granted_at','expires_at')) or
            not grant['granted_at'] <= now < grant['expires_at'] <= grant['granted_at']+300):
        raise TransactionError('replacement generation grant is stale or selects different evidence')
    return value


def execute(storage, operation, engine):
    work = storage.operation(operation)
    source,job,release,profile,binding,report,policy,accepted,accepted_profile = (
        records.read(work / (name+'.json')) for name in (
            'request','candidate-job','release','profile','candidate-source','check',
            'policy','accepted','accepted-profile'))
    result = records.read(work / 'replacement-preparation-result.json')
    prepared = records.read(work / 'preparation-result.json')
    disk_record = disks.record(work,operation)
    value = records.read(work / 'generation-selection.json')
    grant = records.read(work / 'generation-authorization.json')
    native.Native().guard(storage.box,deadline=time.monotonic()+30)
    with storage.lock():
        replacement.selection(source,job,release,profile,binding,report,policy,accepted,
                              storage.box,operation,engine,time.time())
        authority(value,grant,source,prepared,disk_record,release,
                  storage.box,operation,engine,time.time())
        assignment,old = replacement.accepted_runtime(storage,source,accepted,accepted_profile)
        preparation.validate_result(prepared,source,job)
        if (not isinstance(result,dict) or result.get('kind') !=
                'klokast.router-replacement-preparation-result.v1' or
                result.get('box') != storage.box or result.get('operation_id') != operation or
                result.get('status') != 'replacement-prepared' or
                result.get('router_started') is not False or
                result.get('old_sha256') != old['record_sha256'] or
                result.get('preparation_sha256') != generations.digest(prepared) or
                result.get('release_sha256') != release['receipt_sha256']):
            raise TransactionError('replacement generation lacks its exact retained preparation result')
        disk = disks.verify(work,operation)
        if (disk_record['stage'] != 'cloned' or
                disk_record['template_sha256'] != release['artifacts']['os'] or
                result.get('candidate_disk') != disk):
            raise TransactionError('replacement generation disk differs from its prepared clone')
        replacement.selection(source,job,release,profile,binding,report,policy,accepted,
                              storage.box,operation,engine,time.time())
        authority(value,grant,source,prepared,disk_record,release,
                  storage.box,operation,engine,time.time())
        boot = boot_files(storage,source,release,operation)
        candidate = candidate_generation.assemble(box=storage.box,operation=operation,
            template_operation=source['template_operation'],old=old,release=release,
            profile=profile,prepared=prepared['prepared'],disk_record=disk_record,
            boot=boot,xen_uuid=value['xen_uuid'],approved_engine=engine)
        if disks.verify(work,operation) != disk:
            raise TransactionError('replacement candidate disk changed while staging boot artifacts')
        final_assignment,final_old = replacement.accepted_runtime(
            storage,source,accepted,accepted_profile)
        if final_assignment != assignment or final_old != old:
            raise TransactionError('accepted router changed while staging a proposed generation')
        if storage.accepted() != assignment or storage.pending() is not None:
            raise TransactionError('accepted router changed while staging a proposed generation')
        authority(value,grant,source,prepared,disk_record,release,
                  storage.box,operation,engine,time.time())
        preflight = candidate_generation.offline_preflight(candidate,prepared['prepared'],disk_record)
        for name,record in (('proposed-generation',candidate),('candidate-preflight',preflight)):
            path = work / (name+'.json')
            if path.exists() or path.is_symlink():
                if records.read(path) != record:
                    raise TransactionError('replacement '+name+' changed on retry')
            else:
                records.write(path,record)
        return {'kind':'klokast.router-replacement-generation-stage-result.v1',
                'box':storage.box,'operation_id':operation,
                'status':'proposed-generation-staged','old_sha256':old['record_sha256'],
                'candidate_sha256':candidate['record_sha256'],
                'preflight_sha256':generations.digest(preflight),
                'router_started':False,'cutover_authorized':False}
