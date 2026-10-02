"""Retire exact controller transfer bytes after native template retirement.

The controller owns these files. This module has no root, guest, package, or
credential operation. The caller holds the installation and router driver locks
and obtains a fresh native unused-template reference check before calling it.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import time

from platform_updates import UpdateError, digest
import router_update_controller as transport

MIB = 1024 * 1024


def directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
            stat.S_IMODE(info.st_mode) != 0o700):
        raise UpdateError('router template cache directory is unsafe')


def identity(path, expected):
    descriptor=os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o022 or
                info.st_size != expected['bytes']):
            raise UpdateError('router template cache file identity changed: '+path.name)
        checksum = hashlib.sha256();count=0
        for block in iter(lambda: stream.read(MIB), b''):
            count+=len(block)
            if count > expected['bytes']:
                raise UpdateError('router template cache file grew beyond its recorded bound')
            checksum.update(block)
        after=os.fstat(stream.fileno())
        if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns) != (
                after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise UpdateError('router template cache file changed while hashing')
    if checksum.hexdigest() != expected['sha256']:
        raise UpdateError('router template cache file bytes changed: '+path.name)
    return {'device': info.st_dev, 'inode': info.st_ino, 'bytes': info.st_size}


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def scope(request, arguments):
    if (not isinstance(request, dict) or set(request) !=
            {'kind','box','role','operation_id','inputs_sha256','capsule','bootstrap'} or
            request['kind'] != 'klokast.router-template-request.v1' or request['role'] != 'router' or
            not isinstance(request['bootstrap'], dict) or set(request['bootstrap']) != {'kernel','initramfs'}):
        raise UpdateError('router template cache request is incomplete')
    expected = {'capsule': request['capsule'], **request['bootstrap']}
    result = {}
    for name, maximum in (('capsule',2049*MIB),('kernel',32*MIB),('initramfs',1024*MIB)):
        value = expected[name]
        if (not isinstance(value,dict) or set(value) != {'bytes','sha256'} or
                type(value['bytes']) is not int or not 0 < value['bytes'] <= maximum or
                not isinstance(value['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['sha256'])):
            raise UpdateError('router template cache artifact is not bounded')
        result['capsule.tar' if name == 'capsule' else 'boot/'+name] = value
    parts = arguments.get('router_template_transfer_parts')
    if not isinstance(parts,list) or not 3 <= len(parts) <= 1553:
        raise UpdateError('router template cache transfer manifest is incomplete')
    cursor = 0
    for artifact in ('capsule','kernel','initramfs'):
        group = [item for item in parts if isinstance(item,dict) and item.get('artifact') == artifact]
        if not group or parts[cursor:cursor+len(group)] != group:
            raise UpdateError('router template cache transfer order changed')
        cursor += len(group)
        total = 0
        for index,item in enumerate(group):
            if (set(item) != {'artifact','name','bytes','sha256'} or item['name'] != f'part-{index:04d}' or
                    type(item['bytes']) is not int or not 0 < item['bytes'] <= 2*MIB or
                    index < len(group)-1 and item['bytes'] != 2*MIB or
                    not isinstance(item['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',item['sha256'])):
                raise UpdateError('router template cache transfer part is invalid')
            total += item['bytes']
            result['transfer/'+artifact+'/'+item['name']] = {'bytes':item['bytes'],'sha256':item['sha256']}
        if total != expected[artifact]['bytes']:
            raise UpdateError('router template cache transfer size changed')
    if cursor != len(parts):
        raise UpdateError('router template cache transfer contains unknown artifacts')
    return result


def retire(cache, state, box, operation, reference):
    if not re.fullmatch('[0-9a-f]{24}',operation):
        raise UpdateError('router template cache needs one exact operation')
    work,result = Path(cache)/('build-'+operation),Path(state)/operation
    for path in (Path(cache),Path(state),work,result):
        directory(path)
    if (not isinstance(reference,dict) or reference.get('kind') != 'klokast.router-template-reference-check.v1' or
            set(reference) != {'kind','box','operation_id','status','references_sha256','retained_templates','removed_now'} or
            reference.get('box') != box or reference.get('operation_id') != operation or
            reference.get('status') != 'unused-template-reference-verified' or reference.get('removed_now') != [] or
            not isinstance(reference.get('references_sha256'),str) or
            not re.fullmatch('[0-9a-f]{64}',reference['references_sha256']) or
            type(reference.get('retained_templates')) is not int or not 0 <= reference['retained_templates'] <= 4096):
        raise UpdateError('router template cache requires fresh native unused-reference proof')
    request = transport.load(work/'request.json')
    arguments = transport.load(result/'arguments.json')
    complete = transport.load(result/'cleanup-obsolete-complete.json')
    native_plan = transport.load(result/'cleanup-obsolete-plan.json')
    native_progress = transport.load(result/'cleanup-obsolete-progress.json')
    if (request.get('box') != box or request.get('operation_id') != operation or
            arguments.get('router_template_box') != box or arguments.get('router_template_operation') != operation or
            arguments.get('router_template_input_dir') != str(work) or
            arguments.get('router_template_result_dir') != str(result) or
            complete.get('kind') != 'klokast.router-template-cleanup.v2' or
            complete.get('operation_id') != operation or complete.get('cleanup_kind') != 'obsolete' or
            complete.get('status') != 'selected-files-retired' or
            complete.get('removed') != ['os.slot','kernel','initramfs'] or
            complete.get('plan_sha256') != digest(native_plan) or
            complete.get('progress_sha256') != digest(native_progress) or
            native_plan.get('kind') != 'klokast.router-template-cleanup-plan.v1' or
            native_plan.get('operation_id') != operation or native_plan.get('cleanup_kind') != 'obsolete' or
            native_progress.get('kind') != 'klokast.router-template-cleanup-progress.v1' or
            native_progress.get('plan_sha256') != digest(native_plan) or
            native_progress.get('removed') != complete['removed'] or native_progress.get('inflight') is not None):
        raise UpdateError('router template cache lacks exact native obsolete retirement')
    files=native_plan.get('files')
    if (not isinstance(files,list) or [item.get('name') for item in files if isinstance(item,dict)] != complete['removed'] or
            len(files) != 3 or any(not isinstance(item,dict) or set(item) != {'name','identity'} or
                not isinstance(item['identity'],dict) or set(item['identity']) != {'device','inode','bytes'} or
                any(type(item['identity'][key]) is not int for key in ('device','inode','bytes')) or
                item['identity']['device'] < 0 or item['identity']['inode'] <= 0 or
                not 0 < item['identity']['bytes'] <= {'os.slot':2048*MIB,'kernel':32*MIB,'initramfs':128*MIB}[item['name']]
                for item in files) or complete.get('bytes_reclaimed') != sum(item['identity']['bytes'] for item in files)):
        raise UpdateError('router template cache native retirement has incomplete file identities')
    selected = scope(request,arguments)
    for name in ('boot','transfer','transfer/capsule','transfer/kernel','transfer/initramfs'):
        directory(work/name)
    if ({p.name for p in work.iterdir()} != {'request.json','personalization.json','capsule.tar','boot','transfer'} and
            {p.name for p in work.iterdir()} != {'request.json','personalization.json','boot','transfer'}):
        raise UpdateError('router template cache has unknown top-level resources')
    if {p.name for p in (work/'boot').iterdir()} - {'kernel','initramfs'}:
        raise UpdateError('router template cache has unknown boot resources')
    for artifact in ('capsule','kernel','initramfs'):
        allowed = {Path(name).name for name in selected if name.startswith('transfer/'+artifact+'/')}
        if {p.name for p in (work/'transfer'/artifact).iterdir()} - allowed:
            raise UpdateError('router template cache has unknown transfer resources')
    if {p.name for p in (work/'transfer').iterdir()} != {'capsule','kernel','initramfs'}:
        raise UpdateError('router template cache has unknown transfer directories')
    fixed = {'kind':'klokast.router-template-cache-plan.v1','box':box,'operation_id':operation,
             'request_sha256':digest(request),'arguments_sha256':digest(arguments),
             'native_cleanup_sha256':digest(complete),'scope':selected}
    plan_path,progress_path = result/'cache-cleanup-plan.json',result/'cache-cleanup-progress.json'
    if plan_path.exists() or plan_path.is_symlink():
        plan = transport.load(plan_path)
        if any(plan.get(k) != v for k,v in fixed.items()) or set(plan) != set(fixed)|{'files'}:
            raise UpdateError('router template cache removal plan changed')
    else:
        plan = {**fixed,'files':[{'name':name,'identity':identity(work/name,expected)}
                                for name,expected in selected.items()]}
        transport.write(plan_path,plan)
    names = [item['name'] for item in plan['files']]
    if names != list(selected):
        raise UpdateError('router template cache removal scope changed')
    progress = transport.load(progress_path) if progress_path.exists() or progress_path.is_symlink() else {
        'kind':'klokast.router-template-cache-progress.v1','plan_sha256':digest(plan),'removed':[],'inflight':None}
    if (set(progress) != {'kind','plan_sha256','removed','inflight'} or
            progress['kind'] != 'klokast.router-template-cache-progress.v1' or progress['plan_sha256'] != digest(plan) or
            not isinstance(progress['removed'],list) or progress['removed'] != names[:len(progress['removed'])] or
            len(progress['removed']) > len(names) or progress['inflight'] is not None and
                (len(progress['removed']) == len(names) or progress['inflight'] != names[len(progress['removed'])])):
        raise UpdateError('router template cache removal progress changed')
    transport.write(progress_path,progress)
    deadline = time.monotonic()+900
    removed_now=[]
    for item in plan['files']:
        name=item['name'];path=work/name;present=path.exists() or path.is_symlink()
        if time.monotonic() >= deadline:
            raise UpdateError('router template cache cleanup timed out; retry the same plan')
        for parent in (Path(cache),Path(state),work,result,work/'boot',work/'transfer',
                       work/'transfer/capsule',work/'transfer/kernel',work/'transfer/initramfs'):
            directory(parent)
        if (transport.load(work/'request.json') != request or transport.load(result/'arguments.json') != arguments or
                transport.load(result/'cleanup-obsolete-complete.json') != complete):
            raise UpdateError('router template cache source changed during retirement')
        if name in progress['removed']:
            if present: raise UpdateError('router template cache file reappeared after retirement')
            continue
        if not present and progress['inflight'] != name:
            raise UpdateError('router template cache file disappeared without removal intent')
        if present:
            if identity(path,selected[name]) != item['identity']:
                raise UpdateError('router template cache file changed before retirement')
            progress['inflight']=name;transport.write(progress_path,progress)
            path.unlink();sync(path.parent);removed_now.append(name)
        progress['removed'].append(name);progress['inflight']=None;transport.write(progress_path,progress)
    complete={'kind':'klokast.router-template-cache-cleanup.v1','box':box,'operation_id':operation,
              'plan_sha256':digest(plan),'progress_sha256':digest(progress),'status':'transfer-files-retired',
              'bytes_reclaimed':sum(item['identity']['bytes'] for item in plan['files']),'removed':names}
    target=result/'cache-cleanup-complete.json'
    if target.exists() or target.is_symlink():
        if transport.load(target) != complete: raise UpdateError('router template cache completion changed')
    else: transport.write(target,complete)
    return {**complete,'removed_now':removed_now}
