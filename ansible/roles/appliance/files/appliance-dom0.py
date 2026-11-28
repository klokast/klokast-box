#!/usr/bin/env python3
"""Fixed disposable appliance build and replacement. Never mounts guest filesystems."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import time
import uuid
from xen_build_runtime import (attach_loop, boot_guest, checksum, detach_loop, domain,
                               read_slot, require_identity, run, safe_directory, safe_file, write)

MIB=1024*1024
SLOTS={'root':4096*MIB,'kernel':32*MIB,'initramfs':128*MIB,'result':MIB}
# Closed set. The kind selects only these fixed names; the Xen domain is named
# after the kind. Print names predate the exit appliance and stay unchanged.
APPLIANCES={'print':dict(lv='lv_print_server_print',memory=2048),
            'exit':dict(lv='lv_exit_node_exit',memory=512)}
KIND=BASE=LV=None


def select(kind):
    global KIND,BASE,LV
    if kind not in APPLIANCES:raise RuntimeError('unsupported appliance kind')
    KIND,BASE,LV=kind,Path('/mnt/dom0_data/klokast-'+kind),'/dev/vg0/'+APPLIANCES[kind]['lv']


def configuration(work,name,identity,kernel,initramfs,extra,disks,vifs=(),memory=4096):
    text=(f'name = {name!r}\nuuid = {identity!r}\ntype = "pvh"\nmemory = {memory}\nmaxmem = {memory}\n'
          f'vcpus = 2\nkernel = {str(kernel)!r}\nramdisk = {str(initramfs)!r}\nextra = {extra!r}\n'
          f'disk = {disks!r}\nvif = {list(vifs)!r}\non_poweroff = "destroy"\non_reboot = "restart"\non_crash = "destroy"\n')
    path=work/(name+'.cfg');path.write_text(text);path.chmod(0o600)
    return path


def stop_appliance():
    current=domain(KIND)
    if current:
        marker=BASE/'installed.json'
        if not marker.is_file(): raise RuntimeError('unrecorded '+KIND+' domain; refusing destruction')
        installed=json.loads(marker.read_text())
        require_identity(current,installed['uuid'])
        run(['xl','shutdown',KIND])
        deadline=time.monotonic()+30
        while domain(KIND) is not None and time.monotonic()<deadline:time.sleep(1)
        current=domain(KIND)
        if current:
            require_identity(current,installed['uuid']);run(['xl','destroy',str(current['domid'])])
    if domain(KIND) is not None:raise RuntimeError(KIND+' domain is still running')
    Path('/etc/xen/auto/'+KIND+'.cfg').unlink(missing_ok=True)
    Path('/etc/xen/'+KIND+'.cfg').unlink(missing_ok=True)
    persist()
    if Path(LV).exists():
        if not (BASE/'installed.json').is_file():raise RuntimeError('unrecorded '+KIND+' disk; refusing removal')
        installed=json.loads((BASE/'installed.json').read_text())
        if run(['lvs','--noheadings','-o','lv_uuid',LV]).stdout.strip()!=installed.get('lv_uuid'):
            raise RuntimeError(KIND+' disk identity changed; refusing removal')
        # Disks are disposable, including identity. No generation is retained.
        run(['lvremove','-f',LV])
    for path in BASE.glob('current-*'):
        if not path.is_file() or path.is_symlink():raise RuntimeError('unsafe installed '+KIND+' artifact')
        path.unlink()
    (BASE/'installed.json').unlink(missing_ok=True)
    # A build or a retirement ends a stop by the health check.
    (BASE/'health-stop.json').unlink(missing_ok=True)


# The controller's health check stops an installed appliance that cannot work until a person acts,
# for example a print appliance whose printer was off at boot. The record keeps it stopped,
# also across a dom0 reboot, until the controller starts it again.
REASON_FIELDS=('check','detail','restart_requires')


def installed_record(hostname):
    marker=BASE/'installed.json'
    if not marker.is_file():raise RuntimeError('no installed '+KIND+' appliance')
    installed=json.loads(marker.read_text())
    if installed.get('state')!='installed' or installed.get('hostname')!=hostname:
        raise RuntimeError('the installed '+KIND+' record does not match '+hostname)
    return installed


def health_stop(hostname,operation,work):
    installed=installed_record(hostname)
    safe_file(work/'health-stop.json',4096)
    reason=json.loads((work/'health-stop.json').read_text())
    if (not isinstance(reason,dict) or set(reason)!=set(REASON_FIELDS) or
            any(not isinstance(reason[k],str) or not 0<len(reason[k])<=400 or not reason[k].isprintable() for k in REASON_FIELDS)):
        raise RuntimeError('invalid '+KIND+' health stop reason')
    # Record first: an interrupted stop still keeps the appliance stopped at the next boot.
    write(BASE/'health-stop.json',dict(reason,at=int(time.time()),operation_id=operation,hostname=hostname,image=installed['image']))
    Path('/etc/xen/auto/'+KIND+'.cfg').unlink(missing_ok=True)
    persist()
    current=domain(KIND)
    if current:
        require_identity(current,installed['uuid'])
        run(['xl','shutdown',KIND])
        deadline=time.monotonic()+60
        while domain(KIND) is not None and time.monotonic()<deadline:time.sleep(1)
        current=domain(KIND)
        if current:require_identity(current,installed['uuid']);run(['xl','destroy',str(current['domid'])])
    if domain(KIND) is not None:raise RuntimeError(KIND+' domain is still running')
    print(json.dumps(dict(state='stopped',hostname=hostname,operation_id=operation)))


def health_start(hostname,operation):
    installed_record(hostname)
    if not (BASE/'health-stop.json').is_file():
        raise RuntimeError(hostname+' was not stopped by the health check; refusing to start it')
    config=Path('/etc/xen/'+KIND+'.cfg')
    if not config.is_file() or config.is_symlink():raise RuntimeError('the '+KIND+' Xen configuration is missing')
    link=Path('/etc/xen/auto/'+KIND+'.cfg')
    if not link.is_symlink():link.symlink_to(config)
    (BASE/'health-stop.json').unlink()
    persist()
    if domain(KIND) is None:run(['xl','create',str(config)])
    print(json.dumps(dict(state='started',hostname=hostname,operation_id=operation)))


def persist():
    # EFI is a maintenance write window, never the steady-state writable disk.
    record=next((line.split() for line in Path('/proc/mounts').read_text().splitlines()
                 if line.split()[1]=='/media/nvme0n1p1'),None)
    if record is None:raise RuntimeError('expected EFI mount is missing')
    try:
        run(['mount','-o','remount,rw',record[0],record[1]])
        run(['lbu','commit','-d'])
    finally:
        run(['mount','-o','remount,ro',record[0],record[1]])


def main(box,operation,action):
    hostname=box+'-'+KIND
    if action == 'retire':
        stop_appliance()
        print(json.dumps(dict(state='retired',hostname=hostname)))
        return
    if socket.gethostname().split('.')[0] != box+'-dom0':raise RuntimeError(KIND+' target is not the selected dom0')
    if action == 'stop':
        health_stop(hostname,operation,BASE/'staging'/operation)
        return
    if action == 'start':
        health_start(hostname,operation)
        return
    if action != 'build':raise RuntimeError('unsupported '+KIND+' action')
    work=BASE/'staging'/operation
    safe_directory(work)
    safe_file(work/'request.json',MIB)
    request=json.loads((work/'request.json').read_text())
    if request['box']!=box or request['operation_id']!=operation:raise RuntimeError(KIND+' request mismatch')
    if set(request['files']) != {'capsule.tar','bootstrap-kernel','bootstrap-initramfs'}:
        raise RuntimeError('incomplete '+KIND+' input set')
    public=request['public']
    if (not re.fullmatch(r'br[a-z0-9_-]{1,20}',public['bridge']) or
            not re.fullmatch(r'00:16:3e(:[0-9a-f]{2}){3}',public['mac'])):
        raise RuntimeError('invalid compiled '+KIND+' network attachment')
    for name,record in request['files'].items():
        if name not in ('capsule.tar','bootstrap-kernel','bootstrap-initramfs'):raise RuntimeError('unexpected '+KIND+' input')
        path=work/name;safe_file(path,2100*MIB)
        if path.stat().st_size!=record['bytes'] or checksum(path)!=record['sha256']:raise RuntimeError(KIND+' input bytes changed')
    request['capsule']=request['files']['capsule.tar']
    loops={}
    try:
        for name,size in SLOTS.items():
            with (work/(name+'.slot')).open('xb') as stream:stream.truncate(size)
        loops['input']=attach_loop(work/'capsule.tar',readonly=True)
        for name in SLOTS:loops[name]=attach_loop(work/(name+'.slot'))
        extra=(f'console=hvc0 panic=1 klokast_operation={operation} klokast_inputs={request["inputs_sha256"]} '
               f'klokast_capsule={request["capsule"]["sha256"]} klokast_capsule_bytes={request["capsule"]["bytes"]}')
        name=KIND+'-build-'+operation;identity=str(uuid.uuid4())
        disks=[f'phy:{loops["input"]},xvda,r']+[f'phy:{loops[n]},xvd{letter},w' for n,letter in zip(SLOTS,'bcde')]
        config=configuration(work,name,identity,work/'bootstrap-kernel',work/'bootstrap-initramfs',extra,disks)
        boot_guest(work,name,identity,config,request,timeout=900)
        result=read_slot(work/'result.slot')
        if (result.get('success') is not True or result.get('operation_id')!=operation or
                result.get('inputs_sha256')!=request['inputs_sha256']):raise RuntimeError(KIND+' build failed; inspect isolated console')
        for name in ('kernel','initramfs'):
            record=result['artifacts'][name]
            if not 0<record['bytes']<=SLOTS[name] or checksum(work/(name+'.slot'),record['bytes'])!=record['sha256']:
                raise RuntimeError(KIND+' boot artifact verification failed')
            with (work/(name+'.slot')).open('r+b') as stream:stream.truncate(record['bytes'])
        # Qualification boots the sealed OS read-only, without a NIC or enrollment.
        with (work/'test-result.slot').open('xb') as stream:stream.truncate(MIB)
        test_loop=attach_loop(work/'test-result.slot');loops['test']=test_loop
        test_name=KIND+'-test-'+operation
        config=configuration(work,test_name,str(uuid.uuid4()),work/'kernel.slot',work/'initramfs.slot',
                f'console=hvc0 root=/dev/xvda1 ro modules=ext4 init=/usr/local/libexec/{KIND}-selftest klokast_operation={operation} klokast_inputs={request["inputs_sha256"]}',
                [f'phy:{loops["root"]},xvda,r',f'phy:{test_loop},xvdb,w'],memory=2048)
        boot_guest(work,test_name,str_identity(config),config,request,slot='test-result.slot',kind='klokast.'+KIND+'-test.v1',timeout=180)
        test=read_slot(work/'test-result.slot')
        if test.get('success') is not True:raise RuntimeError(KIND+' image qualification failed')
        write(work/'build-result.json',dict(result,qualification=test))
    finally:
        for name,device in reversed(list(loops.items())):
            path=work/('capsule.tar' if name=='input' else 'test-result.slot' if name=='test' else name+'.slot')
            detach_loop(path,device)
    # Qualification succeeded. Delete the previous appliance before installation.
    stop_appliance()
    created=run(['lvcreate','--yes','--wipesignatures','y','-L','4G','-n',APPLIANCES[KIND]['lv'],'vg0'],check=False)
    if created.returncode:raise RuntimeError(KIND+' disk allocation failed: '+created.stderr[-1000:])
    # Record ownership immediately so an interrupted copy can be safely rebuilt.
    identity=str(uuid.uuid4())
    lv_uuid=run(['lvs','--noheadings','-o','lv_uuid',LV]).stdout.strip()
    write(BASE/'installed.json',dict(uuid=identity,operation_id=operation,hostname=hostname,state='installing',lv_uuid=lv_uuid))
    source_sha256=checksum(work/'root.slot')
    with (work/'root.slot').open('rb') as source,open(LV,'wb',buffering=0) as target:
        shutil.copyfileobj(source,target,MIB);os.fsync(target.fileno())
    if checksum(Path(LV),SLOTS['root'])!=source_sha256:raise RuntimeError(KIND+' clone bytes did not verify')
    for name in ('kernel','initramfs'):shutil.copyfile(work/(name+'.slot'),BASE/('current-'+name))
    public=request['public']
    config=configuration(work,KIND,identity,BASE/'current-kernel',BASE/'current-initramfs',
            'console=hvc0 root=/dev/xvda1 ro modules=ext4', [f'phy:{LV},xvda,w'],
            [f'bridge={public["bridge"]},mac={public["mac"]}'],memory=APPLIANCES[KIND]['memory'])
    shutil.copyfile(config,'/etc/xen/'+KIND+'.cfg')
    Path('/etc/xen/auto').mkdir(exist_ok=True)
    Path('/etc/xen/auto/'+KIND+'.cfg').symlink_to('/etc/xen/'+KIND+'.cfg')
    write(BASE/'installed.json',dict(uuid=identity,operation_id=operation,hostname=hostname,state='installed',lv_uuid=lv_uuid,clone_sha256=source_sha256,image=result['inputs_sha256']))
    persist()
    run(['xl','create','/etc/xen/'+KIND+'.cfg'])
    # Never retain a candidate or private enrollment capsule after installation.
    for path in work.iterdir():
        if path.is_file() and path.suffix not in ('.log','.json'):path.unlink()
    print(json.dumps(dict(state='started',hostname=hostname,operation_id=operation)))


def str_identity(config):
    import ast
    return ast.literal_eval(next(line.split('=',1)[1].strip() for line in config.read_text().splitlines() if line.startswith('uuid =')))


if __name__=='__main__':
    box,kind,operation,action=sys.argv[1:]
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}',box) or not re.fullmatch('[0-9a-f]{24}',operation):
        raise RuntimeError('invalid appliance operation identity')
    select(kind)
    BASE.mkdir(mode=0o700,exist_ok=True)
    with (BASE/'operation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            main(box,operation,action)
        finally:
            work=BASE/'staging'/operation
            # Transport retains attachments if exact domain cleanup failed.
            from xen_build_runtime import loop_devices
            if work.is_dir() and not any(domain(prefix+operation) for prefix in (KIND+'-build-',KIND+'-test-')):
                for path in work.iterdir():
                    if path.is_file() and (path.suffix in ('.slot','.tar') or path.name.startswith('bootstrap-')) and not loop_devices(path):path.unlink()
