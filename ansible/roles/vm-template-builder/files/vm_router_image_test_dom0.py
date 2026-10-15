"""Common builder router qualification. Dom0 handles opaque files and Xen only."""
from pathlib import Path
import os
import shutil
import uuid

from xen_build_runtime import attach_loop, boot_guest, checksum, detach_loop, domain, read_slot, write

MIB = 1024**2


def exercise(work, request, build):
    operation = request['operation_id']
    names = {k:'vm-'+v+'-'+operation for k,v in (
        ('seed','test'),('migrate','personalize'),('boot','openrc'),('attach','profile'),('negative','restore'))}
    if any(domain(name) is not None for name in names.values()):
        raise RuntimeError('router qualification guest already exists; preserve its disks')
    root = work/'router-old.slot'; candidate = work/'router-new.slot'; data = work/'router-data.slot'
    wrong = work/'router-wrong.slot'; result = work/'router-result.slot'; config = work/'router-test.cfg'
    created = []; loops = {}; proofs = {}

    def allocate(path, size):
        with path.open('xb') as stream:
            os.posix_fallocate(stream.fileno(),0,size)
        created.append(path)

    def clone(path):
        allocate(path,(work/'root.slot').stat().st_size)
        with (work/'root.slot').open('rb') as source, path.open('r+b') as target:
            shutil.copyfileobj(source,target,MIB); target.flush(); os.fsync(target.fileno())
        if checksum(path) != checksum(work/'root.slot'):
            raise RuntimeError('router qualification clone differs from its image')

    def launch(stage, selected, *, source=None, storage=data):
        name = names['seed' if stage == 'seed' else stage if stage in ('migrate','attach') else
                     'negative' if stage in ('missing','substitute') else 'boot']
        identity = str(uuid.uuid4())
        specs = [(selected,'a','w'),(result,'c','w')]
        if source:
            specs += [(source,'d','r'),(storage,'e','w')]
        elif stage != 'seed' and storage is not None: specs.append((storage,'b','w'))
        try:
            for path,_,mode in specs: loops[path] = attach_loop(path,readonly=mode == 'r')
            with result.open('r+b') as stream:
                stream.write(b'\0'*MIB); stream.flush(); os.fsync(stream.fileno())
            normal = stage not in ('seed','migrate','attach')
            extra = ('console=hvc0 panic=1 root=/dev/xvda rootfstype=ext4 modules=ext4 rw init='+
                ('/sbin/init' if normal else '/usr/local/libexec/vm_router_image_test.py')+
                ' klokast_operation='+operation+' klokast_inputs='+request['inputs_sha256']+
                ' klokast_router_fixture=1 klokast_router_test=1 klokast_router_personalize=1 klokast_router_stage='+stage)
            value = {'name':name,'uuid':identity,'type':'pvh','memory':2048,'maxmem':2048,'vcpus':2,
                'kernel':str(work/'kernel.slot'),'ramdisk':str(work/'initramfs.slot'),'extra':extra,
                'disk':['phy:'+loops[p]+',xvd'+letter+','+mode for p,letter,mode in specs],
                'vif':[],'on_poweroff':'destroy','on_reboot':'destroy','on_crash':'destroy'}
            config.write_text('\n'.join(k+' = '+repr(v) for k,v in value.items())+'\n')
            write(work/'test-lifecycle.json',{'domain':names['seed'],'stage':stage,'guest':name,'uuid':identity})
            boot_guest(work,name,identity,config,request,slot=result.name,kind='klokast.router-image-fixture.v1',timeout=300)
            proof = read_slot(result)
            if proof.get('stage') != stage or proof.get('production_data_used') is not False:
                raise RuntimeError('router qualification returned mismatched stage evidence')
            proofs[stage] = proof
            return proof
        finally:
            if domain(name) is not None:
                raise RuntimeError('router qualification still holds its disks; preserve all resources')
            for path,device in list(loops.items()):
                detach_loop(path,device); del loops[path]
    try:
        for name in ('kernel','initramfs'):
            with (work/(name+'.slot')).open('r+b') as stream: stream.truncate(build['artifacts'][name]['bytes'])
        clone(root); clone(candidate); allocate(data,1024*MIB); allocate(wrong,1024*MIB); allocate(result,MIB)
        launch('seed',root)
        launch('migrate',candidate,source=root)
        launch('first',candidate); launch('reboot-first',candidate)
        launch('missing',candidate,storage=None); launch('substitute',candidate,storage=wrong)
        root.unlink(); created.remove(root); clone(root)
        launch('attach',root,source=candidate)
        launch('second',root); launch('reboot-second',root)
        boots = [proofs[name] for name in ('first','reboot-first','second','reboot-second')]
        if (len({p.get('boot_id') for p in boots}) != 4 or
                any(p.get('identity') != proofs['seed']['seed']['identity'] or
                    p.get('filesystem_uuid') != proofs['seed']['seed']['contract']['filesystem_uuid'] or
                    any(p.get(k) is not True for k in ('storage','services','leases')) for p in boots) or
                any(proofs[s].get('refused') is not True or proofs[s].get('root_state_absent') is not True
                    for s in ('missing','substitute'))):
            raise RuntimeError('router native replacement, reboot, or refusal evidence is incomplete')
        return {'kind':'klokast.vm-template-test-result.v1','success':True,'operation_id':operation,
            'inputs_sha256':request['inputs_sha256'],'kernel_release':build['kernel_release'],
            'tested_root_sha256':checksum(work/'root.slot'),'router_qualification':proofs,
            'tests':dict.fromkeys(('boot','kernel_modules','native_services','retained_migration',
                'retained_reattachment','exclusive_attachment','openrc_boot','reboot','missing_data_refused',
                'substituted_data_refused','identity','leases','numeric_ownership'),True)}
    finally:
        if any(domain(name) is not None for name in names.values()) or loops:
            raise RuntimeError('router fixture cleanup requires all guest attachments to be released')
        # Failed runs retain their console logs and stage records, not active disks.
        for path in created: path.unlink()
        config.unlink(missing_ok=True)
        write(work/'test-lifecycle.json',{'domain':names['seed'],'stage':'cleaned',
            'openrc_domain':names['boot'],'personalize_domain':names['migrate'],
            'profile_domain':names['attach'],'restore_domain':names['negative']})
