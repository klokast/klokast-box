"""One controller flow for qualified shared and VPN VM replacement."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

import infrastructure_images as images
import ops_replacement_inputs as inputs
import platform_resource_runtime as runtime
import platform_source
import vm_local_images
import vm_service_probe
from platform_updates import digest

REPO = Path(__file__).resolve().parents[2]
ROOT = Path('/home/smith/private/klokast/vm-replacement-inputs')
ROLES = ('bak', 'dmz', 'iot', 'vpn-egress')


def profile(role):
    if role not in ROLES: raise RuntimeError('unsupported service VM role')
    return 'vpn-egress-alpine-v1' if role == 'vpn-egress' else 'shared-alpine-v1'


def target(view, box, role):
    declared = view['instance']['boxes'].get(box)
    if declared is None or role not in ROLES: raise RuntimeError('service target is not declared')
    if role == 'vpn-egress':
        if not declared.get('vpn-egress'): raise RuntimeError('VPN gateway is not declared')
        return 'running'
    return declared.get('substrate', {}).get('shared-guests', {}).get(role, {}).get('runtime-state', 'running')


def authority(box, role):
    return target(platform_source.snapshot(), box, role)


def step(box, role, action, *, repo=REPO, inventory=None, operation='', payload=None, receipt=None, checksum=''):
    if authority(box, role) != 'running' and action != 'status':
        raise RuntimeError('service runtime intent changed; preserve the operation and inspect before resume')
    with tempfile.TemporaryDirectory(prefix='service-step-') as temporary:
        output = Path(temporary) / 'result.json'
        variables = {'service_box':box,'service_role':role,'service_action':action,'service_operation':operation,
                     'service_payload':payload or {},'service_receipt':receipt or {},'service_inputs_sha256':checksum,
                     'service_result_path':str(output)}
        inputs.save(Path(temporary) / 'variables.json', variables)
        result = subprocess.run(['ansible-playbook','-vv','-i',str(inventory or REPO/'ansible/execution-inventory/hosts'),
                 str(repo/'ansible/playbooks/74-vm-service-update.yml'),'--limit',box+'-dom0',
                 '-e','@'+str(Path(temporary)/'variables.json')],cwd=repo,
                 env=dict(os.environ,ANSIBLE_CONFIG=str(repo/'ansible/ansible.cfg')),timeout=4200)
        if result.returncode: raise RuntimeError('service step failed: '+action+'; inspect the private operation log')
        return json.loads(output.read_text())


def wait_probe(box, role):
    deadline = time.monotonic()+300
    while True:
        try: return vm_service_probe.probe(box, role)
        except (RuntimeError, subprocess.SubprocessError) as error:
            if time.monotonic()>=deadline: raise RuntimeError('service did not pass health checks within five minutes') from error
            time.sleep(5)


def execute(box, role, *, image=None, resume=None, dry_run=False, lock_held=False, abandon=False):
    with contextlib.nullcontext() if lock_held else runtime.vm_update_installation_lock():
        intended = authority(box, role)
        if intended == 'stopped': return {'state':'skipped','box':box,'role':role,'reason':'declared stopped'}
        revision = inputs.revision(REPO, True)
        instance = inputs.revision(inputs.INSTANCE, True)
        observed = step(box, role, 'status', operation=resume or '')
        pending = observed['pending']
        assignment = observed.get('assignment')
        if assignment and assignment.get('stage') in ('complete','adopted','recovered') and (
                assignment.get('configuration_drift') or assignment.get('autostart_drift') or assignment.get('runtime') != 'running'):
            raise RuntimeError('service boot assignment or runtime differs; reconcile the declared guest before updating')
        if pending and (not resume or len(pending)!=1 or pending[0]['operation_id']!=resume or pending[0]['role']!=role):
            record=pending[0]
            raise RuntimeError('incomplete service update; resume with: ansible/bin/platform-update update --box '+box+' --role '+record['role']+' --resume '+record['operation_id'])
        installed = observed['installed']
        if resume:
            record = pending[0] if pending else observed.get('operation') or installed
            if not record or record['operation_id'] != resume: raise RuntimeError('resume does not select the current service operation')
            if abandon and record.get('stage') == 'abandoned':
                return {'state':'abandoned','box':box,'role':role,'operation_id':resume}
            if not pending and not abandon:
                vm_service_probe.verify(record['requested_configuration']['before'], vm_service_probe.probe(box,role), record, unfinished=False)
                return {'state':'complete','box':box,'role':role,'image':record['image'],'replaced':False}
            work, variables = inputs.recover(record,configuration_key='service_configuration',image_key='service_image',root=ROOT)
            operation = resume; image = record['image']; checksum = record['inputs_sha256']
        else:
            if dry_run: return {'state':'planned','box':box,'role':role,'profile':profile(role),'installed_image':installed['image'] if installed else None,
                               'engine_commit':revision,'instance_commit':instance,'configuration_only':'health; no replacement or convergence'}
            if image is None:
                built=images.local_action(box,profile(role),revision,'prepare')
                if built.get('state') not in ('candidate-built','candidate-reused'): raise RuntimeError('image preparation is incomplete')
                image=built['operation_id']
            if installed and image==installed['image']:
                vm_service_probe.verify(installed['requested_configuration']['before'], vm_service_probe.probe(box,role), installed, unfinished=False)
                return {'state':'complete','box':box,'role':role,'image':image,'replaced':False,
                        'configuration_update_needed':any(installed['requested_configuration'].get(k)!=v for k,v in (('engine_commit',revision),('instance_commit',instance)))}
            receipt=images.local_action(box,profile(role),revision,'image-receipt',image)
            vm_local_images.validate(box,image,receipt['files'])
            recipe=json.loads((REPO/'ansible/update-profiles'/(profile(role)+'.json')).read_text())
            if receipt['files']['inputs.json']['profile_sha256'] != digest(images.qualify_profile(recipe,repo=REPO)):
                raise RuntimeError('selected service image requires qualification with the approved guest recipe')
            catalog = Path('/var/lib/klokast/updates/discovery')
            with (catalog/'build.lock').open('a') as catalog_lock:
                fcntl.flock(catalog_lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
                vm_local_images.import_receipt(catalog/'builds',box,receipt)
            before=vm_service_probe.probe(box,role)
            if before['workloads'].get('running'):
                raise RuntimeError('running applications require compatibility tests before replacement; no disks were allocated')
            config={k:before[k] for k in ('root_partition','accounts','source_files')}
            release = receipt['files']['release-evidence.json']
            config.update(box=box,role=role,image=image,before=before,engine_commit=revision,instance_commit=instance,
                          image_evidence={'kernel_release':release['kernel_release'],'packages_sha256':digest(release['packages'])})
            variables={'service_configuration':config,'service_image':image,'service_receipt':receipt}
            work,checksum=inputs.freeze(REPO,variables,configuration_key='service_configuration',root=ROOT)
            operation=secrets.token_hex(12)
            # A lost prepare response must leave the exact resume command locally.
            inputs.save(work/'operation.json', {'box':box,'role':role,'operation_id':operation,'inputs_sha256':checksum})
        if dry_run: return {'state':'planned','box':box,'role':role,'resume':operation,'image':image}
        config=variables['service_configuration']
        options={'repo':work/'public','inventory':work/'inventory/hosts.json','operation':operation,'checksum':checksum}
        try:
            if abandon:
                state=step(box,role,'abandon',**options)
                return {'state':state['stage'],'box':box,'role':role,'operation_id':operation}
            state=step(box,role,'prepare',payload=config,receipt=variables['service_receipt'],**options)
            for _ in range(8):
                state=step(box,role,'advance',**options)
                stage=state['journal']['stage']
                if stage in ('booted','complete'): break
            if stage=='complete': return {'state':'complete','box':box,'role':role,'image':image,'replaced':False}
            if stage!='booted': raise RuntimeError('service did not reach the recorded boot stage')
            after=wait_probe(box,role)
            vm_service_probe.verify(config['before'],after,state)
            verification=state['reboot_verification']
            if verification['status']=='required':
                state=step(box,role,'reboot-start',payload={'boot_id':after['boot_id'],'identity':after['identity']},**options)
                verification=state['reboot_verification']
            if verification['status']=='pending':
                if after['boot_id']==verification['before']['boot_id']:
                    if authority(box,role) != 'running':
                        raise RuntimeError('service runtime intent changed before reboot; preserve the pending verification')
                    subprocess.run(['tailscale','ssh','neo@'+box+'-'+role,'doas','reboot'],timeout=45,check=False)
                    deadline=time.monotonic()+300
                    while True:
                        after=wait_probe(box,role)
                        if after['boot_id']!=verification['before']['boot_id']: break
                        if time.monotonic()>=deadline: raise RuntimeError('new service boot ID did not change')
                        time.sleep(5)
                checks=vm_service_probe.verify(config['before'],after,state)
                state=step(box,role,'reboot-complete',payload={'boot_id':after['boot_id'],'identity':after['identity']},**options)
            if role=='vpn-egress':
                authority(box,role)
                subprocess.run([str(REPO/'ansible/bin/platform-vpn-egress'),'verify','--box',box],check=True,timeout=600)
            checks=vm_service_probe.verify(config['before'],after,state)
            step(box,role,'checks',payload=checks,**options)
            step(box,role,'advance',**options)
            state=step(box,role,'advance',**options)
            if state['journal']['stage']!='complete': raise RuntimeError('service acceptance is incomplete')
            return {'state':'complete','box':box,'role':role,'image':image,'operation_id':operation,'replaced':True}
        except (RuntimeError,OSError,ValueError,subprocess.SubprocessError) as error:
            raise RuntimeError(str(error)+'; resume with: ansible/bin/platform-update update --box '+box+' --role '+role+' --resume '+operation) from error
