"""Common BAK, DMZ and IoT creation and reconstruction; retain historical DMZ names."""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile

import platform_resource_model as model
import platform_source
import platform_vpn_egress
import routing_profile
import vm_service_runtime as service


ROLES = ('bak', 'dmz', 'iot')


def configuration(box, *, repo, role='dmz'):
    if role not in ROLES: raise RuntimeError('image provisioning supports BAK, DMZ and IoT only')
    snapshot=platform_source.snapshot()
    topology=model.load_topology(repo_root=repo)
    declared=model.registry_box_configs(snapshot['rendered']['projection']['registry'],topology)[box]
    profile=routing_profile.validate(snapshot['instance']['boxes'][box]['routing-profile'])
    zone=topology['zones'][role]
    public={'kind':'klokast.infrastructure-config.v1','box':box,'role':role,
            'bridge':zone['bridge'],'address':zone['vm_ipv4_address']+'/'+str(zone['router_ipv4_prefix']),
            'gateway':zone['router_ipv4_address'],'bootstrap_dns':profile['bootstrap-dns'],
            'bootstrap_source':topology['zones']['bak']['dom0_ipv4_address'],
            'public_key':(Path.home()/'.ssh/github-klokast-codex.pub').read_text().strip(),
            'agent_uid':1004,'agent_gid':1004}
    client=any(v['role']==role for v in platform_vpn_egress.clients(box,declared,topology))
    runtime={'dmz_box':box,'shared_guest_role':role,'vm_dns_preserve_bootstrap':False,
             'vm_dns_bootstrap_resolvers':profile['bootstrap-dns'],
             'vm_dns_resolvers':[topology['vpn_egress']['ipv4_address']] if client else profile['bootstrap-dns'],
             'vm_dns_vpn_client':client}
    return public,runtime


def configure(box, work, values):
    import vm_service_updates as updates
    role=values.get('shared_guest_role','dmz')
    if role not in ROLES or updates.authority(box,role)!='running':
        raise RuntimeError('shared guest runtime intent changed before configuration')
    repo=work/'public'
    with tempfile.TemporaryDirectory(prefix='dmz-configuration-') as temporary:
        path=Path(temporary)/'vars.json'
        path.write_text(json.dumps(values));path.chmod(0o600)
        subprocess.run(['ansible-playbook','-vv','-i',str(work/'inventory/hosts.json'),
                        str(repo/'ansible/playbooks/69-dmz-configure.yml'),'--limit',box+'-'+role,
                        '-e','@'+str(path)],cwd=repo,check=True,timeout=1200,
                        env=dict(os.environ,ANSIBLE_CONFIG=str(repo/'ansible/ansible.cfg')))


def provision(box, *, repo, role='dmz', dry_run=False, lock_held=False, image=None):
    import contextlib
    import uuid
    import infrastructure_images as images
    import ops_replacement_inputs as inputs
    import router_update_controller as transport
    import vm_service_updates as updates
    import vm_service_probe as probe
    if role not in ROLES: raise RuntimeError('image provisioning supports BAK, DMZ and IoT only')
    with contextlib.nullcontext() if lock_held else transport.installation_lock():
        if updates.authority(box,role)!='running':return {'state':'skipped','box':box,'role':role,'reason':'declared stopped'}
        observed=updates.step(box,role,'status')
        others=[r for r in observed['pending'] if r.get('kind')!='klokast.infrastructure-assignment.v1' or r.get('role')!=role]
        if others:raise RuntimeError('incomplete VM replacement blocks initial shared guest provisioning; '+updates.resume_command(box,others[0]))
        installed=observed['installed']
        if installed:
            probe.verify(installed['requested_configuration']['before'],probe.probe(box,role),installed,unfinished=False)
            return {'state':'complete','box':box,'role':role,'created':False}
        creation=observed.get('creation')
        if not creation and observed.get('present'):
            raise RuntimeError('shared guest already exists without the new creation assignment; use update --rebuild --discard-root-data')
        if dry_run:return {'state':'planned','box':box,'role':role,'action':'resume-creation' if creation else 'create','root_gib':8,'data_gib':50}
        if creation:
            public=creation['configuration']
            pointer=public.get(role+'_inputs',{})
            if set(pointer)!={'input_snapshot','inputs_sha256'}:raise RuntimeError('incomplete shared guest creation has no frozen input pointer')
            token=pointer['input_snapshot']
            if not re.fullmatch('[0-9a-f]{24}',token):raise RuntimeError('invalid shared guest creation snapshot')
            work=updates.ROOT/token
            if work.is_symlink() or not work.is_dir() or work.stat().st_mode & 0o077:
                raise RuntimeError('shared guest creation snapshot is absent or not private')
            manifest=json.loads((work/'manifest.json').read_text())
            if inputs.checksum(manifest)!=pointer['inputs_sha256']:raise RuntimeError('shared guest creation input checksum differs')
            variables=manifest['variables'];config=variables['service_configuration']
            if config['box']!=box or config['role']!=role or variables['infrastructure_config']!={k:v for k,v in public.items() if k!=role+'_inputs'}:
                raise RuntimeError('shared guest creation configuration differs from recorded inputs')
            image=creation['operation_id'];checksum=pointer['inputs_sha256']
            work,variables=inputs.recover({'requested_configuration':config,'image':image,'inputs_sha256':checksum},
                configuration_key='service_configuration',image_key='service_image',root=updates.ROOT)
        else:
            revision=inputs.revision(repo,True)
            if image is None:
                selected=images.local_action(box,'shared-alpine-v1',revision,'prepare')
                if selected.get('state') not in ('candidate-built','candidate-reused'):raise RuntimeError('shared guest image preparation did not complete')
                image=selected['operation_id']
            receipt=images.local_action(box,'shared-alpine-v1',revision,'image-receipt',image)
            public,values=configuration(box,repo=repo,role=role);public['data_uuid']=str(uuid.uuid4())
            release=receipt['files']['release-evidence.json']
            config={role+'_initial':True,'box':box,'role':role,'image':image,'engine_commit':revision,
                    'instance_commit':inputs.revision(inputs.INSTANCE,True),role+'_creation':public,
                    'image_evidence':{'kernel_release':release['kernel_release'],'packages_sha256':updates.digest(release['packages'])}}
            variables={'service_configuration':config,'service_image':image,'service_receipt':receipt,
                       'infrastructure_config':public,role+'_runtime':values}
            work,checksum=inputs.freeze(repo,variables,configuration_key='service_configuration',root=updates.ROOT)
        return updates.finish_frozen(box,role,work,variables,observed,image,image,checksum)


def finish_creation(box, work, variables, checksum):
    import os
    import time
    import vm_service_updates as updates
    import vm_service_probe as probe
    import vpn_egress_provisioning as transport
    repo=work/'public';config=variables['service_configuration'];image=variables['service_image']
    role=config['role']
    if role not in ROLES or config['box']!=box:raise RuntimeError('initial shared guest inputs select another target')
    public=dict(variables['infrastructure_config'],**{role+'_inputs':{'input_snapshot':config['input_snapshot'],'inputs_sha256':checksum}})
    if updates.authority(box,role)!='running':raise RuntimeError('shared guest intent changed before initial creation')
    transport.ansible('69-dmz-provision.yml',box+'-dom0,'+box+'-'+role,
        dict(variables[role+'_runtime'],infrastructure_box=box,infrastructure_role=role,
             infrastructure_image=image,infrastructure_config=public),repo=repo,inventory=work/'inventory/hosts.json')
    configure(box,work,variables[role+'_runtime'])
    after=service.wait_probe(box,role)
    options={'repo':repo,'inventory':work/'inventory/hosts.json','operation':image}
    state=updates.step(box,role,'creation-evidence',payload={'phase':'before','probe':after},**options)
    before=state['reboot_verification']['before']
    record={'kind':'klokast.vm-service-update.v1','operation_id':image,'box':box,'role':role,
            'image':image,'new_uuid':state['uuid'],'data_uuid':state['data_uuid'],
            'requested_configuration':dict(config,before=before)}
    probe.verify(before,after,record)
    if after['boot_id']==before['boot_id']:
        if updates.authority(box,role)!='running':raise RuntimeError('shared guest intent changed before initial reboot')
        result=subprocess.run(['tailscale','ssh','neo@'+box+'-'+role,'doas','/sbin/reboot'],timeout=45,check=False)
        if result.returncode not in (0,255):raise RuntimeError('initial shared guest reboot command failed')
    deadline=time.monotonic()+300
    while after['boot_id']==before['boot_id']:
        if time.monotonic()>=deadline:raise RuntimeError('initial shared guest reboot verification is incomplete; rerun provision')
        time.sleep(5);after=service.wait_probe(box,role)
    probe.verify(before,after,record)
    updates.step(box,role,'creation-evidence',payload={'phase':'after','probe':after,'service':record},**options)
    return {'state':'complete','box':box,'role':role,'created':True,'image':image}
