"""Controller-owned disposable print appliance workflow."""
import datetime
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time

import platform_source
import platform_resource_runtime
import vm_template_inputs
import vm_local_images
from platform_update_metadata import fetch_json,newest_stable_branch

REPO=Path(__file__).resolve().parents[2]
PROFILE='print-alpine-v1'
ROOT=Path('/var/lib/klokast/print-appliance')


def run(argv,**kwargs):
    return subprocess.run([str(v) for v in argv],check=True,**kwargs)


def context(box):
    platform_source.require_controller()
    data=json.loads(subprocess.check_output([str(REPO/'ansible/bin/platform-resources'),'--app','print-server','show'],text=True))
    specs=[s for s in data['app_vm_specs'] if s['app']=='print-server' and s['node']==box]
    if len(specs)!=1:raise RuntimeError('declare exactly one print appliance for the selected box')
    spec=specs[0]
    if spec['node_hostname']!=box+'-print':raise RuntimeError('print appliance name must be BOX-print')
    devices=[d for d in data['managed_iot_devices'] if d['node']==box and d['app']=='print-server']
    if len(devices)!=1:raise RuntimeError('declare exactly one physical printer')
    # Network parameters are compiled and validated by the Platform compiler.
    interfaces=spec['guest_spec']['network_interfaces']
    gateway=next(line.split()[1] for line in interfaces.splitlines() if line.strip().startswith('gateway '))
    ipaddress.IPv4Address(gateway)
    lan=next((s for s in data['lan_print_services'] if s['node']==box),None)
    config=dict(hostname=spec['node_hostname'],network_interfaces=interfaces,gateway=gateway,
                printer=devices[0]['ipv4_address'],lan_subnet=lan['subnet'] if lan else '')
    return data,spec,config


def build(box):
    if __import__('socket').gethostname().split('.')[0]!=box+'-ops':
        raise RuntimeError('build this image on the selected box controller as smith')
    platform_source.require_local_image(box)
    data,spec,config=context(box)
    implementation=platform_source.implementation()
    if implementation['dirty']:raise RuntimeError('commit and push the implementation before building')
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    operation=secrets.token_hex(12)
    evidence=ROOT/operation;evidence.mkdir(mode=0o700)
    print('print-appliance: operation '+operation,flush=True)
    profile=json.loads((REPO/'ansible/update-profiles'/ (PROFILE+'.json')).read_text())
    metadata,_=fetch_json(profile['release_metadata'])
    branch=newest_stable_branch(metadata,datetime.datetime.now(datetime.timezone.utc),0)
    with platform_resource_runtime.vm_update_installation_lock(), (ROOT/'build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='print-',dir='/var/cache/klokast/updates') as temporary:
            work=Path(temporary);work.chmod(0o700)
            status=json.loads(subprocess.check_output(['tailscale','status','--json'],text=True))
            inventory=vm_local_images.inventory(REPO,work/'inventory',box,status)
            arguments=dict(print_box=box,print_operation=operation,print_input_dir=str(work),print_result_dir=str(evidence),print_action='retire')
            (work/'arguments.json').write_text(json.dumps(arguments)+'\n')
            with (evidence/'retire.log').open('w') as log:
                result=subprocess.run(['ansible-playbook','-vv','-i',str(inventory),str(REPO/'ansible/playbooks/80-platform-print-appliance.yml'),
                                      '--limit',box+'-dom0','-e','@'+str(work/'arguments.json')],cwd=REPO,
                                     env=dict(os.environ,ANSIBLE_CONFIG=str(REPO/'ansible/ansible.cfg')),stdout=log,stderr=log)
            if result.returncode:raise RuntimeError('print retirement failed; inspect '+str(evidence/'retire.log'))
            devices=json.loads(subprocess.check_output(['doas','/usr/local/sbin/ts-devices-list'],text=True)).get('devices',[])
            old=[d for d in devices if d.get('hostname','').split('.')[0]==config['hostname']]
            if len(old)>1:raise RuntimeError('multiple old print identities; reconcile before rebuilding')
            for device in old:
                if device.get('tags')!=['tag:print']:raise RuntimeError('old print identity has unexpected tags')
                for attempt in range(12):
                    result=subprocess.run(['doas','/usr/local/sbin/ts-device-delete-stale','--id',device['id'],
                                           '--hostname',config['hostname'],'--tag','tag:print'],capture_output=True,text=True)
                    if result.returncode==0:break
                    time.sleep(5)
                else:raise RuntimeError('old print identity is not yet safely revocable; retry the build after checking controller peer status')
            inputs=vm_template_inputs.freeze_local(work/'inputs',profile,branch,implementation['commit'],expected_profile=PROFILE,
                                                   apk_network_timeout=10,cache_root=Path('/var/cache/klokast/updates'))
            (evidence/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
            files=REPO/'ansible/roles/print-appliance/files'
            base=REPO/'ansible/roles/vm-template-builder/files/vm-template-build-guest'
            boot=vm_template_inputs.bootstrap(work/'inputs',work/'boot',files/'build-guest.py',expected_profile=PROFILE,
                                               job_files={'base_build.py':base,'print_image.py':files/'print_image.py'})
            key=subprocess.check_output(['doas','/usr/local/sbin/ts-authkey-print','--hostname',config['hostname'],'--tags','tag:print'],text=True).strip()
            (work/'authkey').write_text(key+'\n');(work/'authkey').chmod(0o600);key=''
            (work/'print.json').write_text(json.dumps(config)+'\n')
            from jinja2 import Environment,FileSystemLoader,StrictUndefined
            template_dir=REPO/'apps/print-server/ansible/roles/print-server/templates'
            cups=Environment(loader=FileSystemLoader(template_dir),undefined=StrictUndefined).get_template('cupsd.conf.j2').render(
                print_tailscale_hostname=config['hostname'],print_tailnet_dns_suffix=subprocess.check_output([str(REPO/'ansible/bin/magicdns-suffix')],text=True).strip(),
                print_lan_enabled=bool(config['lan_subnet']),print_lan_subnet=config['lan_subnet'])
            (work/'cupsd.conf').write_text(cups)
            with tarfile.open(work/'capsule.tar','x',format=tarfile.USTAR_FORMAT) as archive:
                for relative in ['inputs.json',*[p['file'] for p in inputs['packages']],*['keys/'+k for k in inputs['keys']]]:
                    archive.add(work/'inputs'/relative,arcname=relative,recursive=False)
                for name in ('print.json','authkey','cupsd.conf'):archive.add(work/name,arcname=name,recursive=False)
                for name in ('print-appliance.openrc','print-start','print-selftest'):archive.add(files/name,arcname=name,recursive=False)
            def record(path):return dict(bytes=path.stat().st_size,sha256=vm_template_inputs.sha256(path))
            request=dict(box=box,operation_id=operation,inputs_sha256=inputs['inputs_sha256'],
                         files={'capsule.tar':record(work/'capsule.tar'),'bootstrap-kernel':boot['kernel'],'bootstrap-initramfs':boot['initramfs']},
                         public=dict(bridge=spec['guest_spec']['vifs'][0]['bridge'],mac=spec['guest_spec']['vifs'][0]['mac']))
            (work/'request.json').write_text(json.dumps(request)+'\n')
            arguments=dict(print_box=box,print_operation=operation,print_input_dir=str(work),print_result_dir=str(evidence),print_action='build')
            (work/'arguments.json').write_text(json.dumps(arguments)+'\n')
            with (evidence/'build.log').open('w') as log:
                result=subprocess.run(['ansible-playbook','-vv','-i',str(inventory),str(REPO/'ansible/playbooks/80-platform-print-appliance.yml'),
                                      '--limit',box+'-dom0','-e','@'+str(work/'arguments.json')],cwd=REPO,
                                     env=dict(os.environ,ANSIBLE_CONFIG=str(REPO/'ansible/ansible.cfg')),stdout=log,stderr=log)
            if result.returncode:raise RuntimeError('print build failed; inspect '+str(evidence/'build.log'))
    print('print-appliance: installed; evidence '+str(evidence),flush=True)


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('build','preflight'))
    parser.add_argument('--box',required=True)
    args=parser.parse_args()
    if args.command=='preflight':
        _,spec,config=context(args.box);print(json.dumps(dict(hostname=spec['node_hostname'],vm_ipv4_address=spec['vm_ipv4_address'],lan_subnet=config['lan_subnet'])))
    else:build(args.box)
