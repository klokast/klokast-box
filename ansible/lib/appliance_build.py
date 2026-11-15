"""Controller-owned build and retirement of the fixed disposable appliances."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tarfile
import tempfile
import time

import platform_source
import platform_resource_runtime
import router_tailscale
import vm_template_inputs
import vm_local_images
from platform_update_metadata import fetch_json,newest_stable_branch

REPO=Path(__file__).resolve().parents[2]
FILES=REPO/'ansible/roles/appliance/files'
PLAYBOOK=REPO/'ansible/playbooks/80-platform-appliance.yml'
# Closed set: each kind has one image profile, one tag and one key wrapper.
KINDS=('print','exit')


def root(kind):
    return Path('/var/lib/klokast/'+kind+'-appliance')


def require_controller(kind,box):
    if kind not in KINDS:raise RuntimeError('unsupported appliance kind')
    if socket.gethostname().split('.')[0]!=box+'-ops':
        raise RuntimeError('build this image on the selected box controller as smith')
    platform_source.require_local_image(box)


def playbook(kind,box,operation,work,evidence,action,inventory):
    arguments=dict(appliance_kind=kind,appliance_box=box,appliance_operation=operation,
                   appliance_input_dir=str(work),appliance_result_dir=str(evidence),appliance_action=action)
    (work/'arguments.json').write_text(json.dumps(arguments)+'\n')
    with (evidence/(action+'.log')).open('w') as log:
        result=subprocess.run(['ansible-playbook','-vv','-i',str(inventory),str(PLAYBOOK),
                              '--limit',box+'-dom0','-e','@'+str(work/'arguments.json')],cwd=REPO,
                             env=dict(os.environ,ANSIBLE_CONFIG=str(REPO/'ansible/ansible.cfg')),stdout=log,stderr=log)
    if result.returncode:raise RuntimeError(kind+' '+('retirement' if action=='retire' else 'build')+' failed; inspect '+str(evidence/(action+'.log')))


def revoke(hostname,tag):
    devices=json.loads(subprocess.check_output(['doas','/usr/local/sbin/ts-devices-list'],text=True)).get('devices',[])
    old=[d for d in devices if d.get('hostname','').split('.')[0]==hostname]
    if len(old)>1:raise RuntimeError('multiple old '+hostname+' identities; reconcile before rebuilding')
    for device in old:
        if device.get('tags')!=[tag]:raise RuntimeError('old '+hostname+' identity has unexpected tags')
        for attempt in range(12):
            result=subprocess.run(['doas','/usr/local/sbin/ts-device-delete-stale','--id',device['id'],
                                   '--hostname',hostname,'--tag',tag],capture_output=True,text=True)
            if result.returncode==0:break
            time.sleep(5)
        else:raise RuntimeError('old '+hostname+' identity is not yet safely revocable; retry after checking controller peer status')


def operation_context(kind):
    directory=root(kind)
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    operation=secrets.token_hex(12)
    evidence=directory/operation;evidence.mkdir(mode=0o700)
    print(kind+'-appliance: operation '+operation,flush=True)
    return directory,operation,evidence


def retire(kind,box,hostname,tag):
    """Stop the appliance, remove its disk and revoke its identity."""
    require_controller(kind,box)
    directory,operation,evidence=operation_context(kind)
    with platform_resource_runtime.vm_update_installation_lock(), (directory/'build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix=kind+'-',dir='/var/cache/klokast/updates') as temporary:
            work=Path(temporary);work.chmod(0o700)
            status=json.loads(subprocess.check_output(['tailscale','status','--json'],text=True))
            inventory=vm_local_images.inventory(REPO,work/'inventory',box,status)
            playbook(kind,box,operation,work,evidence,'retire',inventory)
            revoke(hostname,tag)
    print(kind+'-appliance: retired; evidence '+str(evidence),flush=True)


def build(kind,box,config,spec,tag,files):
    """Build, qualify and replace one appliance.

    config becomes appliance.json in the capsule; files maps capsule names to
    the kind's fixed runtime content, as a repository path or rendered text.
    """
    require_controller(kind,box)
    implementation=platform_source.implementation()
    if implementation['dirty']:raise RuntimeError('commit and push the implementation before building')
    directory,operation,evidence=operation_context(kind)
    profile_name=kind+'-alpine-v1'
    profile=json.loads((REPO/'ansible/update-profiles'/(profile_name+'.json')).read_text())
    metadata,_=fetch_json(profile['release_metadata'])
    branch=newest_stable_branch(metadata,datetime.datetime.now(datetime.timezone.utc),0)
    with platform_resource_runtime.vm_update_installation_lock(), (directory/'build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix=kind+'-',dir='/var/cache/klokast/updates') as temporary:
            work=Path(temporary);work.chmod(0o700)
            status=json.loads(subprocess.check_output(['tailscale','status','--json'],text=True))
            inventory=vm_local_images.inventory(REPO,work/'inventory',box,status)
            playbook(kind,box,operation,work,evidence,'retire',inventory)
            revoke(config['hostname'],tag)
            inputs=vm_template_inputs.freeze_local(work/'inputs',profile,branch,implementation['commit'],expected_profile=profile_name,
                                                   apk_network_timeout=10,cache_root=Path('/var/cache/klokast/updates'),
                                                   component_resolver=lambda directory:router_tailscale.freeze(directory,REPO,openrc=router_tailscale.COMMON_OPENRC))
            (evidence/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
            base=REPO/'ansible/roles/vm-template-builder/files/vm-template-build-guest'
            boot=vm_template_inputs.bootstrap(work/'inputs',work/'boot',FILES/'build-guest.py',expected_profile=profile_name,
                                               job_files={'base_build.py':base,'appliance_image.py':FILES/(kind+'_image.py'),
                                                          'appliance_common.py':FILES/'appliance_common.py'})
            key=subprocess.check_output(['doas','/usr/local/sbin/ts-authkey-'+kind,'--hostname',config['hostname'],'--tags',tag],text=True).strip()
            (work/'authkey').write_text(key+'\n');(work/'authkey').chmod(0o600);key=''
            (work/'appliance.json').write_text(json.dumps(dict(config,kind=kind))+'\n')
            with tarfile.open(work/'capsule.tar','x',format=tarfile.USTAR_FORMAT) as archive:
                for relative in ['inputs.json',*[p['file'] for p in inputs['packages']],*['keys/'+k for k in inputs['keys']],
                                 inputs['tailscale']['file'],inputs['tailscale']['openrc_file']]:
                    archive.add(work/'inputs'/relative,arcname=relative,recursive=False)
                for name in ('appliance.json','authkey'):archive.add(work/name,arcname=name,recursive=False)
                for name,source in files.items():
                    if isinstance(source,str):
                        (work/name).write_text(source);source=work/name
                    archive.add(source,arcname=name,recursive=False)
            def record(path):return dict(bytes=path.stat().st_size,sha256=vm_template_inputs.sha256(path))
            request=dict(box=box,operation_id=operation,inputs_sha256=inputs['inputs_sha256'],
                         files={'capsule.tar':record(work/'capsule.tar'),'bootstrap-kernel':boot['kernel'],'bootstrap-initramfs':boot['initramfs']},
                         public=dict(bridge=spec['guest_spec']['vifs'][0]['bridge'],mac=spec['guest_spec']['vifs'][0]['mac']))
            (work/'request.json').write_text(json.dumps(request)+'\n')
            playbook(kind,box,operation,work,evidence,'build',inventory)
    print(kind+'-appliance: installed; evidence '+str(evidence),flush=True)
