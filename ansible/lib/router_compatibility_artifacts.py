"""Fixed diagnostic boot copies and retirement; no production boot authority.

The caller holds the compatibility operation lock, validates its request and
unchanged accepted source, and fences all helpers. Only six files in that
private operation can be selected. Template/source originals are never targets.
"""
import json
import os
from pathlib import Path
import stat
import time

import router_generations as generations
import router_native as native
from xen_build_runtime import checksum, duplicate_fields, loop_devices, write

MIB=1024*1024
LIMITS={'kernel':32*MIB,'initramfs':1024*MIB,'old-kernel':32*MIB,
        'old-initramfs':512*MIB,'new-kernel':32*MIB,'new-initramfs':128*MIB}


def private(path, maximum):
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or
            info.st_mode&0o077 or not 0<=info.st_size<=maximum):
        raise RuntimeError('compatibility artifact is not an exact private regular file: '+path.name)
    return {'device':info.st_dev,'inode':info.st_ino,'bytes':info.st_size}


def read(path):
    if not private(path,MIB)['bytes']:
        raise RuntimeError('compatibility artifact record is empty')
    return json.loads(path.read_text(),object_pairs_hook=duplicate_fields)


def reference(value, maximum):
    if (not isinstance(value,dict) or set(value)!={'bytes','sha256'} or
            type(value['bytes']) is not int or not 0<value['bytes']<=maximum or
            not generations.matches('[0-9a-f]{64}',value['sha256'])):
        raise RuntimeError('compatibility artifact has an invalid bounded identity')
    return value


def stage(work, name, source, expected):
    """Keep a partial copy attributable to one declared destination and inode."""
    if name not in LIMITS or name in ('kernel','initramfs'):
        raise RuntimeError('compatibility boot copy has an unknown destination')
    expected=reference(expected,LIMITS[name])
    source=Path(source)
    info=source.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or info.st_mode&0o022 or
            info.st_size!=expected['bytes'] or checksum(source)!=expected['sha256']):
        raise RuntimeError('compatibility boot source differs before copying')
    target=work/name; journal=work/('boot-copy-'+name+'.json')
    if target.exists() or target.is_symlink() or journal.exists() or journal.is_symlink():
        raise RuntimeError('compatibility boot copy already has resources; reconcile them')
    intent={'kind':'klokast.router-compatibility-boot-copy.v1','operation_id':work.name,
            'name':name,'expected':expected,'stage':'planned','identity':None}
    write(journal,intent)
    with target.open('xb') as output:
        write(journal,{**intent,'stage':'copying','identity':private(target,LIMITS[name])})
        with source.open('rb') as incoming:
            remaining=expected['bytes']
            while remaining:
                block=incoming.read(min(remaining,MIB))
                if not block:
                    raise RuntimeError('compatibility boot source was truncated while copying')
                output.write(block); remaining-=len(block)
            if incoming.read(1):
                raise RuntimeError('compatibility boot source grew while copying')
        output.flush(); os.fsync(output.fileno())
    actual=private(target,LIMITS[name])
    if actual['bytes']!=expected['bytes'] or checksum(target)!=expected['sha256']:
        raise RuntimeError('compatibility boot copy differs from its declared source')
    write(journal,{**intent,'stage':'complete','identity':actual})


def wanted(value, template, *, historical_work=None):
    candidate_path=template/'candidate.json'
    candidate=read(candidate_path)
    if (checksum(candidate_path)!=value['template']['sha256'] or
            candidate.get('kind')!='klokast.router-template-candidate.v1' or
            candidate.get('box')!=value['box'] or candidate.get('role')!='router' or
            candidate.get('operation_id')!=value['template']['operation'] or
            candidate.get('inputs_sha256')!=value['inputs_sha256']):
        raise RuntimeError('compatibility artifact cleanup has another template source')
    result=dict(value['bootstrap'])
    for name,field in (('old-kernel','kernel'),('old-initramfs','ramdisk')):
        original=value['source']['boot_artifacts'][field]
        if 'bytes' not in original and historical_work is not None:
            # The first legacy inspector recorded hashes, but no byte counts.
            # Accept only its fixed source names. A surviving copy must match
            # that hash before planning; retries use the protected inode plan.
            source='/mnt/dom0_data/xen_images/router-'+('kernel' if field=='kernel' else 'initramfs')
            if (set(original)!={'path','sha256'} or original['path']!=source or
                    not generations.matches('[0-9a-f]{64}',original['sha256'])):
                raise RuntimeError('historical compatibility boot source has an unknown identity')
            plan_path=historical_work/'artifact-cleanup-plan.json'
            if plan_path.exists() or plan_path.is_symlink():
                plan=read(plan_path)
                items=plan.get('files')
                if not isinstance(items,list):
                    raise RuntimeError('historical compatibility artifact plan is invalid')
                selected=[item for item in items if isinstance(item,dict) and item.get('name')==name]
                if len(selected)>1:
                    raise RuntimeError('historical compatibility artifact plan repeats a boot file')
                size=selected[0]['identity']['bytes'] if selected else LIMITS[name]
            elif (historical_work/name).exists() or (historical_work/name).is_symlink():
                size=private(historical_work/name,LIMITS[name])['bytes']
            else:
                size=LIMITS[name]
            result[name]={'bytes':size,'sha256':original['sha256']}
        else:
            result[name]={key:original[key] for key in ('bytes','sha256')}
    for name,field in (('new-kernel','kernel'),('new-initramfs','initramfs')):
        result[name]=candidate['artifacts'][field]
    if set(result)!=set(LIMITS):
        raise RuntimeError('compatibility artifact cleanup has an incomplete boot source')
    return {name:reference(result[name],limit) for name,limit in LIMITS.items()}


def retire(work, value, template, *, authorize, historical=False, absent_only=False):
    """Retire exact boot files after disk cleanup; preserve all small evidence."""
    expected=wanted(value,template,historical_work=work if historical else None)
    plan_path=work/'artifact-cleanup-plan.json'
    fields={'kind','box','operation_id','engine_commit','request_sha256','files'}
    fixed={'kind':'klokast.router-compatibility-artifact-plan.v1','box':value['box'],
           'operation_id':value['operation_id'],'engine_commit':value['engine_commit'],
           'request_sha256':generations.digest(value)}
    def fresh():
        authorize()
        paths={str(work/name) for name in LIMITS}
        for guest in native.Native().inventory(deadline=time.monotonic()+30):
            boot=guest['config'].get('b_info',{})
            if guest['domid'] and (boot.get('kernel') in paths or boot.get('ramdisk') in paths):
                raise RuntimeError('compatibility boot artifact still belongs to a live guest')
        if any(loop_devices(work/name) for name in LIMITS):
            raise RuntimeError('compatibility boot artifact still has a loop mapping')
    fresh()
    if absent_only and (not historical or any((work/name).exists() or (work/name).is_symlink() for name in LIMITS)):
        raise RuntimeError('historical absence reconciliation requires every boot copy to be absent')
    if plan_path.exists() or plan_path.is_symlink():
        plan=read(plan_path)
        if (set(plan)!=fields or any(plan.get(key)!=item for key,item in fixed.items()) or
                not isinstance(plan['files'],list)):
            raise RuntimeError('compatibility artifact cleanup plan changed')
        names=[]
        for item in plan['files']:
            if (not isinstance(item,dict) or set(item)!={'name','identity'} or item['name'] not in LIMITS or
                    not isinstance(item['identity'],dict) or set(item['identity'])!={'device','inode','bytes'} or
                    any(type(number) is not int for number in item['identity'].values()) or
                    item['identity']['device']<0 or item['identity']['inode']<=0 or
                    not 0<=item['identity']['bytes']<=expected[item['name']]['bytes']):
                raise RuntimeError('compatibility artifact cleanup has an invalid selected identity')
            names.append(item['name'])
        if names!=[name for name in LIMITS if name in names] or len(names)!=len(set(names)):
            raise RuntimeError('compatibility artifact cleanup repeats or reorders files')
    else:
        files=[]
        for name in LIMITS:
            path=work/name; journal=work/('boot-copy-'+name+'.json')
            intent=read(journal) if journal.exists() or journal.is_symlink() else None
            if intent is not None:
                if (set(intent)!={'kind','operation_id','name','expected','stage','identity'} or
                        intent['kind']!='klokast.router-compatibility-boot-copy.v1' or intent['operation_id']!=value['operation_id'] or
                        intent['name']!=name or intent['expected']!=expected[name] or
                        intent['stage'] not in ('planned','copying','complete') or
                        (intent['stage']=='planned')!=(intent['identity'] is None)):
                    raise RuntimeError('compatibility boot copy intent changed')
                if intent['identity'] is not None and (
                        not isinstance(intent['identity'],dict) or set(intent['identity'])!={'device','inode','bytes'} or
                        any(type(number) is not int for number in intent['identity'].values()) or
                        intent['identity']['device']<0 or intent['identity']['inode']<=0 or
                        not 0<=intent['identity']['bytes']<=expected[name]['bytes']):
                    raise RuntimeError('compatibility boot copy inode record changed')
            if not path.exists() and not path.is_symlink():
                if (not absent_only and name in ('kernel','initramfs') or
                        intent is not None and intent['stage']!='planned'):
                    raise RuntimeError('compatibility boot file disappeared without retirement intent')
                continue
            actual=private(path,expected[name]['bytes'])
            if intent is not None and intent['identity'] is not None and any(
                    actual[key]!=intent['identity'][key] for key in ('device','inode')):
                raise RuntimeError('compatibility partial boot copy inode changed')
            if intent is None or intent['stage']=='complete':
                if actual['bytes']!=expected[name]['bytes'] or checksum(path)!=expected[name]['sha256']:
                    raise RuntimeError('compatibility boot bytes differ from their declared source')
                if intent is not None and actual!=intent['identity']:
                    raise RuntimeError('compatibility completed boot copy changed')
            files.append({'name':name,'identity':actual})
        plan={**fixed,'files':files}; write(plan_path,plan)
    if absent_only and plan['files']:
        raise RuntimeError('historical absence reconciliation cannot replace a recorded removal plan')
    names=[item['name'] for item in plan['files']]
    progress_path=work/'artifact-cleanup-progress.json'
    progress=read(progress_path) if progress_path.exists() or progress_path.is_symlink() else {
        'kind':'klokast.router-compatibility-artifact-progress.v1','plan_sha256':generations.digest(plan),
        'removed':[],'inflight':None}
    if (set(progress)!={'kind','plan_sha256','removed','inflight'} or
            progress['kind']!='klokast.router-compatibility-artifact-progress.v1' or
            progress['plan_sha256']!=generations.digest(plan) or not isinstance(progress['removed'],list) or
            len(progress['removed'])>len(names) or progress['removed']!=names[:len(progress['removed'])] or
            progress['inflight'] is not None and (len(progress['removed'])==len(names) or
                progress['inflight']!=names[len(progress['removed'])])):
        raise RuntimeError('compatibility artifact cleanup progress changed')
    if any((work/name).exists() or (work/name).is_symlink() for name in LIMITS if name not in names):
        raise RuntimeError('compatibility boot file appeared outside its cleanup plan')
    for item in plan['files']:
        name=item['name']; path=work/name; present=path.exists() or path.is_symlink()
        if name in progress['removed']:
            if present:
                raise RuntimeError('compatibility boot file reappeared after retirement')
            continue
        if not present and progress['inflight']!=name:
            raise RuntimeError('compatibility boot file disappeared without retirement intent')
        fresh()
        if present:
            if private(path,expected[name]['bytes'])!=item['identity']:
                raise RuntimeError('compatibility boot file changed before retirement')
            progress['inflight']=name; write(progress_path,progress)
            fresh(); path.unlink()
            descriptor=os.open(work,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        progress['removed'].append(name); progress['inflight']=None; write(progress_path,progress)
    fresh()
    result={'kind':'klokast.router-compatibility-artifact-cleanup.v1',
        'box':value['box'],'operation_id':value['operation_id'],'engine_commit':value['engine_commit'],
        'plan_sha256':generations.digest(plan),'progress_sha256':generations.digest(progress),
        'bytes_reclaimed':sum(item['identity']['bytes'] for item in plan['files']),'status':'boot-files-retired'}
    target=work/'artifact-cleanup-complete.json'
    if target.exists() or target.is_symlink():
        if read(target)!=result:
            raise RuntimeError('compatibility artifact cleanup completion changed')
    else:
        write(target,result)
    return result
