"""Exact unused replacement cleanup; never grants boot or enrollment authority."""
import logging
from pathlib import Path
import time

import router_candidate as candidate
import router_candidate_disk as disks
import router_candidate_preparation as preparation
import router_copy_native as copy_native
import router_generation_device as devices
import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import TransactionError

MIB = 1024 * 1024


def context(storage, operation, engine, *, verify):
    if storage.pending() is not None or storage.cold_test() is not None:
        raise TransactionError('unused preparation cleanup requires no pending or cold router transaction')
    work = storage.operation(operation)
    request, job = records.read(work / 'request.json'), records.read(work / 'candidate-job.json')
    candidate.validate(job)
    if (request.get('kind') != 'klokast.router-replacement-preparation.v1' or
            any(request.get(key) != expected for key, expected in (
                ('box', storage.box), ('operation_id', operation), ('engine_commit', engine), ('mode', 'replacement'))) or
            any(job[key] != request[key] for key in ('box','operation_id','engine_commit','mode','inputs_sha256')) or
            request.get('job_sha256') != generations.digest(job)):
        raise TransactionError('unused cleanup source is not this exact replacement preparation')
    assignment = storage.accepted()
    if (request['accepted_assignment_sha256'] != assignment['record_sha256'] or
            request['old_sha256'] != assignment['current_sha256']):
        raise TransactionError('unused preparation cleanup requires its unchanged accepted assignment')
    for name in ('worker.log','complete.json','enrollment-attempt.json','controller-enrollment.json',
                 'enrollment-result.json','acceptance.json','copy/allocation.json'):
        if (work / name).exists() or (work / name).is_symlink():
            raise TransactionError('unused preparation has launch, copy or identity markers; reconcile cutover instead')
    if (work / 'authorization.json').exists() or (work / 'authorization.json').is_symlink():
        if records.read(work / 'authorization.json').get('kind') != 'klokast.router-replacement-preparation-grant.v1':
            raise TransactionError('unused preparation has cutover authority; no prelaunch cleanup is permitted')
    proposed_path = work / 'proposed-generation.json'
    proposed = generations.generation(records.read(proposed_path), storage.box) if proposed_path.exists() else None
    if proposed is not None and (proposed['generation_id'] != operation or
            devices.read(storage, proposed['record_sha256']) is not None):
        raise TransactionError('unused candidate has a different generation or a recorded enrollment')
    verify(storage, require_running=True)
    return work, request, job, assignment, proposed


def resources(storage, work, request, proposed):
    """Select fixed resource files only; preserve all logs and source records."""
    result = []
    def capture(path, maximum, expected=None):
        if not path.exists() and not path.is_symlink():
            return
        records.parents(path)
        records.secure(path, maximum=maximum)
        info = path.stat()
        if info.st_mode & 0o077:
            raise TransactionError('unused preparation file is not private: ' + path.name)
        if expected is not None:
            native.Native().artifact({'path':str(path), **expected}, deadline=time.monotonic() + 120)
        result.append({'path':str(path), 'device':info.st_dev, 'inode':info.st_ino, 'bytes':info.st_size})
    def boot(directory, bootstrap, prefix=''):
        if not isinstance(bootstrap, dict) or set(bootstrap) != {'kernel','initramfs'}:
            raise TransactionError('unused preparation has incomplete boot metadata')
        for name in ('kernel','initramfs'):
            identity = bootstrap[name]
            maximum = (32 if name == 'kernel' else 1024) * MIB
            if (not isinstance(identity, dict) or set(identity) != {'bytes','sha256'} or
                    type(identity['bytes']) is not int or not 0 < identity['bytes'] <= maximum or
                    not generations.matches('[0-9a-f]{64}', identity['sha256'])):
                raise TransactionError('unused preparation boot identity is invalid')
            capture(directory / (prefix + name), maximum, identity)
    def parts(directory):
        path = directory / 'parts.json'
        if not path.exists() and not path.is_symlink():
            if (directory / 'parts').exists():
                raise TransactionError('unused boot parts have no exact manifest')
            return
        manifest = records.read(path)
        if not isinstance(manifest, list) or not 2 <= len(manifest) <= 528:
            raise TransactionError('unused boot part manifest is not bounded')
        paths = set()
        for item in manifest:
            if (not isinstance(item, dict) or set(item) != {'artifact','name','bytes','sha256'} or
                    item['artifact'] not in ('kernel','initramfs') or
                    not generations.matches('part-[0-9]{4}', item['name']) or
                    type(item['bytes']) is not int or not 0 < item['bytes'] <= 2*MIB or
                    not generations.matches('[0-9a-f]{64}', item['sha256'])):
                raise TransactionError('unused boot part has an invalid exact identity')
            target = directory / 'parts' / item['artifact'] / item['name']
            if str(target) in paths:
                raise TransactionError('unused boot part manifest repeats a file')
            paths.add(str(target))
            capture(target, 2*MIB, {'bytes':item['bytes'], 'sha256':item['sha256']})
        parent = directory / 'parts'
        if parent.exists():
            records.secure(parent, directory=True)
            if {p.name for p in parent.iterdir()} - {'kernel','initramfs'}:
                raise TransactionError('unused boot part workspace has unexpected directories')
            for name in ('kernel','initramfs'):
                child = parent / name
                if child.exists():
                    records.secure(child, directory=True)
                    if any(str(p) not in paths for p in child.iterdir()):
                        raise TransactionError('unused boot part workspace has unrecorded files')
    boot(work, request['bootstrap'], 'bootstrap-')
    parts(work)
    capture(work / 'result.slot', MIB)
    final = work / 'finalization'
    if final.exists() or final.is_symlink():
        records.secure(final, directory=True)
        for marker in ('result.slot','result.json','finalize.cfg','run.json'):
            if (final / marker).exists() or (final / marker).is_symlink():
                raise TransactionError('unused preparation has finalizer run markers; preserve its identity state')
        boot(final, records.read(final / 'request.json')['bootstrap'], 'bootstrap-')
        parts(final)
    copy_dir = work / 'copy'
    if copy_dir.exists() or copy_dir.is_symlink():
        records.secure(copy_dir, directory=True)
        if {p.name for p in copy_dir.iterdir()} - {'kernel','initramfs'}:
            raise TransactionError('unused copy workspace has run or unknown resources')
        capsule = records.read(work / 'capsule.json')
        boot(copy_dir, capsule['bootstrap'])
    generation_dir = storage.base / 'generations' / request['operation_id']
    if generation_dir.exists() or generation_dir.is_symlink():
        records.secure(generation_dir, directory=True)
        if {p.name for p in generation_dir.iterdir()} - {'kernel','initramfs'}:
            raise TransactionError('unused generation boot workspace needs exact reconciliation')
        release = records.read(work / 'release.json')
        for name in ('kernel','initramfs'):
            path = generation_dir / name
            if path.exists() or path.is_symlink():
                expected = {'sha256':release['artifacts'][name], 'bytes':path.lstat().st_size}
                if proposed is not None and proposed['boot'][name] != {'path':str(path), **expected}:
                    raise TransactionError('unused generation boot bytes differ from its proposed record')
                capture(path, (32 if name == 'kernel' else 128)*MIB, expected)
    if len(result) > 1065 or len({item['path'] for item in result}) != len(result):
        raise TransactionError('unused preparation resource list is ambiguous or unbounded')
    return result


def plan(storage, operation, engine, *, verify):
    work, request, job, assignment, proposed = context(storage, operation, engine, verify=verify)
    target = work / 'preparation-cleanup-plan.json'
    if target.exists() or target.is_symlink():
        value = records.read(target)
        generations.check_seal(value)
        if (set(value) != {'kind','box','operation_id','engine_commit','request_sha256','assignment_sha256',
                          'old_sha256','disk_record','disk','files','record_sha256'} or
                value['kind'] != 'klokast.router-preparation-cleanup-plan.v1' or
                any(value[key] != expected for key, expected in (
                    ('box',storage.box), ('operation_id',operation), ('engine_commit',engine),
                    ('request_sha256',generations.digest(request)), ('assignment_sha256',assignment['record_sha256']),
                    ('old_sha256',request['old_sha256'])))):
            raise TransactionError('unused preparation cleanup plan changed; retain its resources')
        files = value['files']
        generation_dir = storage.base / 'generations' / operation
        if not isinstance(files,list) or len(files)>1065:
            raise TransactionError('unused preparation cached resource list is unbounded')
        paths = set()
        for item in files:
            if (not isinstance(item,dict) or set(item) != {'path','device','inode','bytes'} or
                    not isinstance(item['path'],str) or item['path'] in paths or
                    any(type(item[key]) is not int for key in ('device','inode','bytes')) or
                    item['device']<0 or item['inode']<=0 or not 0<item['bytes']<=1024*MIB):
                raise TransactionError('unused preparation cached file identity changed')
            path = Path(item['path'])
            relative = str(path.relative_to(work)) if path.is_relative_to(work) else None
            if not (relative is not None and generations.matches(
                    r'(?:bootstrap-(?:kernel|initramfs)|result[.]slot|(?:finalization/)?parts/(?:kernel|initramfs)/part-[0-9]{4}|finalization/bootstrap-(?:kernel|initramfs)|copy/(?:kernel|initramfs))',relative) or
                    path in (generation_dir/'kernel',generation_dir/'initramfs')):
                raise TransactionError('unused preparation cached resource escapes its exact operation')
            paths.add(item['path'])
        original,disk = value['disk_record'],value['disk']
        if original is not None:
            if (not isinstance(original,dict) or set(original) != {'kind','operation_id','path','tag','uuid','stage','template_sha256'} or
                    original['kind'] != 'klokast.router-candidate-disk.v1' or original['operation_id'] != operation or
                    (original['path'],original['tag']) != disks.selection(operation) or
                    original['stage'] not in ('planned','allocated','cloned') or
                    not generations.matches('[0-9a-f]{64}',original['template_sha256']) or
                    (original['stage'] == 'planned' and original['uuid'] is not None) or
                    (original['stage'] != 'planned' and not generations.matches('[A-Za-z0-9-]{1,64}',original['uuid']))):
                raise TransactionError('unused preparation cached allocation record changed')
        if disk is not None:
            if (original is None or not isinstance(disk,dict) or set(disk) != {'path','uuid','bytes'} or
                    disk['path'] != disks.selection(operation)[0] or disk['bytes'] != disks.BYTES or
                    not generations.matches('[A-Za-z0-9-]{1,64}',disk['uuid']) or
                    original['uuid'] not in (None,disk['uuid'])):
                raise TransactionError('unused preparation cached disk selection changed')
        elif original is not None and original['stage'] != 'planned':
            raise TransactionError('unused preparation cached allocation has no selected disk')
        return value
    record_path = work / 'candidate-disk.json'
    disk_record = disks.record(work, operation) if record_path.exists() or record_path.is_symlink() else None
    row = disks.observed(operation, identity=disk_record['uuid'] if disk_record else None)
    if disk_record is None and row is not None:
        raise TransactionError('unused candidate disk has no protected allocation record')
    disk = None
    if disk_record is not None:
        if row is not None:
            # This root observation is the explicitly inspected identity of a
            # planned allocation. The later grant binds it; never guess a UUID.
            disk = disks.validate_row(row, operation, disk_record['uuid'] or row['lv_uuid'])
            if disk_record['stage'] not in ('planned','allocated','cloned'):
                raise TransactionError('unused candidate already has an unbound retirement attempt')
        elif disk_record['stage'] != 'planned':
            raise TransactionError('unused candidate disk disappeared without this cleanup intent')
    if proposed is not None and (disk is None or proposed['disk'] != disk):
        raise TransactionError('unused proposed generation differs from its exact allocated disk')
    disks.refuse_referenced_disk(storage.box, operation,
        disk or {'path':disks.selection(operation)[0], 'uuid':None})
    files = resources(storage,work,request,proposed)
    kept_paths = {item['path'] for key in ('current_sha256','previous_sha256')
        if assignment[key] is not None for item in storage.generation(assignment[key])['boot'].values()}
    if any(item['path'] in kept_paths for item in files):
        raise TransactionError('unused preparation boot resource is shared with a retained router')
    value = generations.seal({'kind':'klokast.router-preparation-cleanup-plan.v1',
        'box':storage.box,'operation_id':operation,'engine_commit':engine,
        'request_sha256':generations.digest(request),'assignment_sha256':assignment['record_sha256'],
        'old_sha256':request['old_sha256'],'disk_record':disk_record,'disk':disk,
        'files':files})
    records.write(target,value)
    return value


def retire(storage, operation, engine, token, *, verify):
    if not generations.matches('[0-9a-f]{12}',token):
        raise TransactionError('unused preparation retirement needs its exact short grant selector')
    selected = plan(storage,operation,engine,verify=verify)
    work,request,job,_,proposed = context(storage,operation,engine,verify=verify)
    grant = records.read(work / ('preparation-cleanup-grant-' + token + '.json'))
    deadline = time.monotonic() + 300
    host = native.Native()
    def fresh():
        now = time.time()
        if (set(grant) != {'kind','plan_sha256','assignment_sha256','service_sha256','registration_absent_sha256',
                          'granted_at','expires_at'} or grant['kind'] != 'klokast.router-preparation-cleanup-authorization.v1' or
                grant['plan_sha256'] != selected['record_sha256'] or
                grant['assignment_sha256'] != selected['assignment_sha256'] or
                any(not generations.matches('[0-9a-f]{64}',grant[key]) for key in (
                    'service_sha256','registration_absent_sha256')) or
                any(type(grant[key]) is not int for key in ('granted_at','expires_at')) or
                not grant['granted_at'] <= now < grant['expires_at'] <= grant['granted_at'] + 600 or
                time.monotonic() >= deadline):
            raise TransactionError('unused preparation cleanup grant expired or changed')
        context(storage,operation,engine,verify=verify)
    fresh()
    progress_path = work / 'preparation-cleanup-progress.json'
    progress = records.read(progress_path) if progress_path.exists() else None
    if progress is not None:
        generations.check_seal(progress)
        if (set(progress) != {'kind','plan_sha256','phase','removed','inflight','record_sha256'} or
                progress['kind'] != 'klokast.router-preparation-cleanup-progress.v1' or
                progress['plan_sha256'] != selected['record_sha256'] or
                progress['phase'] not in ('fenced','disk-removing','disk-retired','files-retiring','complete') or
                not isinstance(progress['removed'],list) or
                progress['removed'] != [item['path'] for item in selected['files'][:len(progress['removed'])]] or
                len(progress['removed']) > len(selected['files']) or
                progress['inflight'] is not None and (len(progress['removed']) == len(selected['files']) or
                    progress['inflight'] != selected['files'][len(progress['removed'])]['path']) or
                progress['phase'] == 'complete' and (len(progress['removed']) != len(selected['files']) or progress['inflight'] is not None) or
                progress['phase'] in ('fenced','disk-removing','disk-retired') and (progress['removed'] or progress['inflight'] is not None)):
            raise TransactionError('unused preparation cleanup progress changed; preserve remaining resources')
    def save(phase):
        progress.pop('record_sha256',None)
        progress['phase'] = phase
        records.write(progress_path,generations.seal(progress))
    if progress is None:
        if selected['disk'] is not None:
            preparation.fence(work,request,job,selected['disk'],deadline=deadline,authorize=fresh,host=host)
        elif (work / 'preparation.json').exists():
            raise TransactionError('unused helper ledger has no allocated disk')
        progress = {'kind':'klokast.router-preparation-cleanup-progress.v1',
            'plan_sha256':selected['record_sha256'],'phase':'fenced','removed':[],'inflight':None}
        save('fenced')
    # Recheck fencing even after loss of the unlink or lvremove reply. Never
    # re-run a helper against a disk already retired under its durable intent.
    ledger = records.read(work / 'preparation.json') if (work / 'preparation.json').exists() else None
    boots = {item['path'] for item in selected['files'] if Path(item['path']).name in (
        'kernel','initramfs','bootstrap-kernel','bootstrap-initramfs')}
    for guest in host.inventory(deadline=deadline):
        if guest['domid'] == 0:
            continue
        info,boot = guest['config']['c_info'],guest['config'].get('b_info',{})
        if (info['name'] == 'router-candidate-prepare-' + operation or
                ledger is not None and info['uuid'] == ledger['uuid'] or
                proposed is not None and info['uuid'] == proposed['xen']['uuid'] or
                boot.get('kernel') in boots or boot.get('ramdisk') in boots):
            raise TransactionError('unused preparation still has a live helper or candidate')
    for item in selected['files']:
        if copy_native.loops(Path(item['path'])):
            raise TransactionError('unused preparation resource still has a loop mapping')
    if progress['phase'] in ('fenced','disk-removing'):
        fresh()
        save('disk-removing')
        if selected['disk_record'] is not None:
            current = disks.record(work,operation)
            original = selected['disk_record']
            if (any(current[key] != original[key] for key in ('kind','operation_id','path','tag','template_sha256')) or
                    current['uuid'] not in (original['uuid'], selected['disk']['uuid'] if selected['disk'] else None)):
                raise TransactionError('unused allocation identity changed after cleanup planning')
            disks._retire_locked(work,operation,box=storage.box,
                inspected_uuid=selected['disk']['uuid'] if selected['disk'] and original['stage'] == 'planned' else None)
        save('disk-retired')
    if disks.observed(operation,identity=selected['disk']['uuid'] if selected['disk'] else None) is not None:
        raise TransactionError('unused candidate LV remains or reappeared after retirement')
    for item in selected['files']:
        path = Path(item['path'])
        present = path.exists() or path.is_symlink()
        if item['path'] in progress['removed']:
            if present:
                raise TransactionError('unused file reappeared after recorded retirement')
            continue
        if not present and progress['inflight'] != item['path']:
            raise TransactionError('unused file disappeared without its exact removal intent')
        fresh()
        if present:
            records.parents(path)
            records.secure(path,maximum=item['bytes'])
            info = path.stat()
            if ({'device':info.st_dev,'inode':info.st_ino,'bytes':info.st_size} !=
                    {key:item[key] for key in ('device','inode','bytes')} or info.st_mode & 0o077):
                raise TransactionError('unused file identity changed before retirement')
            progress['inflight'] = item['path']
            save('files-retiring')
            fresh()
            logging.info('Retiring unused router file operation=%s path=%s',operation,path)
            path.unlink()
            records.syncdir(path.parent)
        progress['removed'].append(item['path'])
        progress['inflight'] = None
        save('files-retiring')
    save('complete')
    result = generations.seal({'kind':'klokast.router-preparation-cleanup-complete.v1',
        'box':storage.box,'operation_id':operation,'engine_commit':engine,
        'plan_sha256':selected['record_sha256'],'assignment_sha256':selected['assignment_sha256'],
        'progress_sha256':records.read(progress_path)['record_sha256'],'status':'unused-resources-retired'})
    result_path = work / 'preparation-cleanup-complete.json'
    if result_path.exists() or result_path.is_symlink():
        if records.read(result_path) != result:
            raise TransactionError('unused preparation completed cleanup changed')
    else:
        records.write(result_path,result)
    return result
