#!/usr/bin/env python3
"""Fixed print appliance build and replacement. Never mounts guest filesystems."""
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

BASE=Path('/mnt/dom0_data/klokast-print')
MIB=1024*1024
SLOTS={'root':4096*MIB,'kernel':32*MIB,'initramfs':128*MIB,'result':MIB}
LV='/dev/vg0/lv_print_server_print'


def configuration(work,name,identity,kernel,initramfs,extra,disks,vifs=(),memory=4096):
    text=(f'name = {name!r}\nuuid = {identity!r}\ntype = "pvh"\nmemory = {memory}\nmaxmem = {memory}\n'
          f'vcpus = 2\nkernel = {str(kernel)!r}\nramdisk = {str(initramfs)!r}\nextra = {extra!r}\n'
          f'disk = {disks!r}\nvif = {list(vifs)!r}\non_poweroff = "destroy"\non_reboot = "restart"\non_crash = "destroy"\n')
    path=work/(name+'.cfg');path.write_text(text);path.chmod(0o600)
    return path


def stop_print():
    current=domain('print')
    if current:
        marker=BASE/'installed.json'
        if not marker.is_file(): raise RuntimeError('unrecorded print domain; refusing destruction')
        installed=json.loads(marker.read_text())
        require_identity(current,installed['uuid'])
        run(['xl','shutdown','print'])
        deadline=time.monotonic()+30
        while domain('print') is not None and time.monotonic()<deadline:time.sleep(1)
        current=domain('print')
        if current:
            require_identity(current,installed['uuid']);run(['xl','destroy',str(current['domid'])])
    if domain('print') is not None:raise RuntimeError('print domain is still running')
    Path('/etc/xen/auto/print.cfg').unlink(missing_ok=True)
    Path('/etc/xen/print.cfg').unlink(missing_ok=True)
    persist()
    if Path(LV).exists():
        if not (BASE/'installed.json').is_file():raise RuntimeError('unrecorded print disk; refusing removal')
        installed=json.loads((BASE/'installed.json').read_text())
        if run(['lvs','--noheadings','-o','lv_uuid',LV]).stdout.strip()!=installed.get('lv_uuid'):
            raise RuntimeError('print disk identity changed; refusing removal')
        # Disks are disposable, including identity. No generation is retained.
        run(['lvremove','-f',LV])
    for path in BASE.glob('current-*'):
        if not path.is_file() or path.is_symlink():raise RuntimeError('unsafe installed print artifact')
        path.unlink()
    (BASE/'installed.json').unlink(missing_ok=True)


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


def main():
    box,operation,action=sys.argv[1:]
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}',box) or not re.fullmatch('[0-9a-f]{24}',operation):
        raise RuntimeError('invalid print operation identity')
    if action == 'retire':
        stop_print()
        print(json.dumps(dict(state='retired',hostname=box+'-print')))
        return
    if action != 'build':raise RuntimeError('unsupported print action')
    if socket.gethostname().split('.')[0] != box+'-dom0':raise RuntimeError('print target is not the selected dom0')
    work=BASE/'staging'/operation
    safe_directory(work)
    safe_file(work/'request.json',MIB)
    request=json.loads((work/'request.json').read_text())
    if request['box']!=box or request['operation_id']!=operation:raise RuntimeError('print request mismatch')
    if set(request['files']) != {'capsule.tar','bootstrap-kernel','bootstrap-initramfs'}:
        raise RuntimeError('incomplete print input set')
    public=request['public']
    if (not re.fullmatch(r'br[a-z0-9_-]{1,20}',public['bridge']) or
            not re.fullmatch(r'00:16:3e(:[0-9a-f]{2}){3}',public['mac'])):
        raise RuntimeError('invalid compiled print network attachment')
    for name,record in request['files'].items():
        if name not in ('capsule.tar','bootstrap-kernel','bootstrap-initramfs'):raise RuntimeError('unexpected print input')
        path=work/name;safe_file(path,2100*MIB)
        if path.stat().st_size!=record['bytes'] or checksum(path)!=record['sha256']:raise RuntimeError('print input bytes changed')
    request['capsule']=request['files']['capsule.tar']
    loops={}
    try:
        for name,size in SLOTS.items():
            with (work/(name+'.slot')).open('xb') as stream:stream.truncate(size)
        loops['input']=attach_loop(work/'capsule.tar',readonly=True)
        for name in SLOTS:loops[name]=attach_loop(work/(name+'.slot'))
        extra=(f'console=hvc0 panic=1 klokast_operation={operation} klokast_inputs={request["inputs_sha256"]} '
               f'klokast_capsule={request["capsule"]["sha256"]} klokast_capsule_bytes={request["capsule"]["bytes"]}')
        name='print-build-'+operation;identity=str(uuid.uuid4())
        disks=[f'phy:{loops["input"]},xvda,r']+[f'phy:{loops[n]},xvd{letter},w' for n,letter in zip(SLOTS,'bcde')]
        config=configuration(work,name,identity,work/'bootstrap-kernel',work/'bootstrap-initramfs',extra,disks)
        boot_guest(work,name,identity,config,request,timeout=900)
        result=read_slot(work/'result.slot')
        if (result.get('success') is not True or result.get('operation_id')!=operation or
                result.get('inputs_sha256')!=request['inputs_sha256']):raise RuntimeError('print build failed; inspect isolated console')
        for name in ('kernel','initramfs'):
            record=result['artifacts'][name]
            if not 0<record['bytes']<=SLOTS[name] or checksum(work/(name+'.slot'),record['bytes'])!=record['sha256']:
                raise RuntimeError('print boot artifact verification failed')
            with (work/(name+'.slot')).open('r+b') as stream:stream.truncate(record['bytes'])
        # Qualification boots the sealed OS read-only, without a NIC or enrollment.
        with (work/'test-result.slot').open('xb') as stream:stream.truncate(MIB)
        test_loop=attach_loop(work/'test-result.slot');loops['test']=test_loop
        test_name='print-test-'+operation
        config=configuration(work,test_name,str(uuid.uuid4()),work/'kernel.slot',work/'initramfs.slot',
                f'console=hvc0 root=/dev/xvda1 ro modules=ext4 init=/usr/local/libexec/print-selftest klokast_operation={operation} klokast_inputs={request["inputs_sha256"]}',
                [f'phy:{loops["root"]},xvda,r',f'phy:{test_loop},xvdb,w'],memory=2048)
        boot_guest(work,test_name,str_identity(config),config,request,slot='test-result.slot',kind='klokast.print-test.v1',timeout=180)
        test=read_slot(work/'test-result.slot')
        if test.get('success') is not True:raise RuntimeError('print image qualification failed')
        write(work/'build-result.json',dict(result,qualification=test))
    finally:
        for name,device in reversed(list(loops.items())):
            path=work/('capsule.tar' if name=='input' else 'test-result.slot' if name=='test' else name+'.slot')
            detach_loop(path,device)
    # Qualification succeeded. Delete the previous appliance before installation.
    stop_print()
    run(['lvcreate','-L','4G','-n','lv_print_server_print','vg0'])
    # Record ownership immediately so an interrupted copy can be safely rebuilt.
    identity=str(uuid.uuid4())
    lv_uuid=run(['lvs','--noheadings','-o','lv_uuid',LV]).stdout.strip()
    write(BASE/'installed.json',dict(uuid=identity,operation_id=operation,hostname=box+'-print',state='installing',lv_uuid=lv_uuid))
    with (work/'root.slot').open('rb') as source,open(LV,'wb',buffering=0) as target:
        shutil.copyfileobj(source,target,MIB);os.fsync(target.fileno())
    for name in ('kernel','initramfs'):shutil.copyfile(work/(name+'.slot'),BASE/('current-'+name))
    public=request['public']
    config=configuration(work,'print',identity,BASE/'current-kernel',BASE/'current-initramfs',
            'console=hvc0 root=/dev/xvda1 ro modules=ext4', [f'phy:{LV},xvda,w'],
            [f'bridge={public["bridge"]},mac={public["mac"]}'],memory=2048)
    shutil.copyfile(config,'/etc/xen/print.cfg')
    Path('/etc/xen/auto').mkdir(exist_ok=True)
    Path('/etc/xen/auto/print.cfg').symlink_to('/etc/xen/print.cfg')
    write(BASE/'installed.json',dict(uuid=identity,operation_id=operation,hostname=box+'-print',state='installed',lv_uuid=lv_uuid,image=result['inputs_sha256']))
    persist()
    run(['xl','create','/etc/xen/print.cfg'])
    # Never retain a candidate or private enrollment capsule after installation.
    for path in work.iterdir():
        if path.is_file() and path.suffix not in ('.log','.json'):path.unlink()
    print(json.dumps(dict(state='started',hostname=box+'-print',operation_id=operation)))


def str_identity(config):
    import ast
    return ast.literal_eval(next(line.split('=',1)[1].strip() for line in config.read_text().splitlines() if line.startswith('uuid =')))


if __name__=='__main__':
    BASE.mkdir(mode=0o700,exist_ok=True)
    with (BASE/'operation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            main()
        finally:
            operation=sys.argv[2]
            if re.fullmatch('[0-9a-f]{24}',operation):
                work=BASE/'staging'/operation
                # Transport retains attachments if exact domain cleanup failed.
                from xen_build_runtime import loop_devices
                if work.is_dir() and not any(domain(prefix+operation) for prefix in ('print-build-','print-test-')):
                    for path in work.iterdir():
                        if path.is_file() and (path.suffix in ('.slot','.tar') or path.name.startswith('bootstrap-')) and not loop_devices(path):path.unlink()
