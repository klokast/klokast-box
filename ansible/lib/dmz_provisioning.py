"""Approved DMZ configuration shared by creation and reconstruction."""
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


def configuration(box, *, repo):
    snapshot=platform_source.snapshot()
    topology=model.load_topology(repo_root=repo)
    declared=model.registry_box_configs(snapshot['rendered']['projection']['registry'],topology)[box]
    profile=routing_profile.validate(snapshot['instance']['boxes'][box]['routing-profile'])
    zone=topology['zones']['dmz']
    public={'kind':'klokast.infrastructure-config.v1','box':box,'role':'dmz',
            'bridge':zone['bridge'],'address':zone['vm_ipv4_address']+'/'+str(zone['router_ipv4_prefix']),
            'gateway':zone['router_ipv4_address'],'bootstrap_dns':profile['bootstrap-dns'],
            'bootstrap_source':topology['zones']['bak']['dom0_ipv4_address'],
            'public_key':(Path.home()/'.ssh/github-klokast-codex.pub').read_text().strip(),
            'agent_uid':1004,'agent_gid':1004}
    client=any(v['role']=='dmz' for v in platform_vpn_egress.clients(box,declared,topology))
    runtime={'dmz_box':box,'vm_dns_preserve_bootstrap':False,
             'vm_dns_bootstrap_resolvers':profile['bootstrap-dns'],
             'vm_dns_resolvers':[topology['vpn_egress']['ipv4_address']] if client else profile['bootstrap-dns'],
             'vm_dns_vpn_client':client}
    return public,runtime


def configure(box, work, values):
    import vm_service_updates as updates
    if updates.authority(box,'dmz')!='running':
        raise RuntimeError('DMZ runtime intent changed before configuration')
    repo=work/'public'
    with tempfile.TemporaryDirectory(prefix='dmz-configuration-') as temporary:
        path=Path(temporary)/'vars.json'
        path.write_text(json.dumps(values));path.chmod(0o600)
        subprocess.run(['ansible-playbook','-vv','-i',str(work/'inventory/hosts.json'),
                        str(repo/'ansible/playbooks/69-dmz-configure.yml'),'--limit',box+'-dmz',
                        '-e','@'+str(path)],cwd=repo,check=True,timeout=1200,
                        env=dict(os.environ,ANSIBLE_CONFIG=str(repo/'ansible/ansible.cfg')))


def provision(box, *, repo, dry_run=False, lock_held=False):
    import contextlib
    import uuid
    import infrastructure_images as images
    import ops_replacement_inputs as inputs
    import router_update_controller as transport
    import vm_service_updates as updates
    import vm_service_probe as probe
    with contextlib.nullcontext() if lock_held else transport.installation_lock():
        if updates.authority(box,'dmz')!='running':return {'state':'skipped','box':box,'role':'dmz','reason':'declared stopped'}
        observed=updates.step(box,'dmz','status')
        others=[r for r in observed['pending'] if r.get('kind')!='klokast.infrastructure-assignment.v1']
        if others:raise RuntimeError('incomplete VM replacement blocks initial DMZ provisioning; '+updates.resume_command(box,others[0]))
        installed=observed['installed']
        if installed:
            probe.verify(installed['requested_configuration']['before'],probe.probe(box,'dmz'),installed,unfinished=False)
            return {'state':'complete','box':box,'role':'dmz','created':False}
        creation=observed.get('creation')
        if not creation and observed.get('present'):
            raise RuntimeError('DMZ already exists without the new creation assignment; use update --rebuild --discard-root-data')
        if dry_run:return {'state':'planned','box':box,'role':'dmz','action':'resume-creation' if creation else 'create','root_gib':8,'data_gib':50}
        if creation:
            public=creation['configuration']
            pointer=public.get('dmz_inputs',{})
            if set(pointer)!={'input_snapshot','inputs_sha256'}:raise RuntimeError('incomplete DMZ creation has no frozen input pointer')
            token=pointer['input_snapshot']
            if not re.fullmatch('[0-9a-f]{24}',token):raise RuntimeError('invalid DMZ creation snapshot')
            work=updates.ROOT/token
            if work.is_symlink() or not work.is_dir() or work.stat().st_mode & 0o077:
                raise RuntimeError('DMZ creation snapshot is absent or not private')
            manifest=json.loads((work/'manifest.json').read_text())
            if inputs.checksum(manifest)!=pointer['inputs_sha256']:raise RuntimeError('DMZ creation input checksum differs')
            variables=manifest['variables'];config=variables['service_configuration']
            if variables['infrastructure_config']!={k:v for k,v in public.items() if k!='dmz_inputs'}:
                raise RuntimeError('DMZ creation configuration differs from recorded inputs')
            image=creation['operation_id'];checksum=pointer['inputs_sha256']
            work,variables=inputs.recover({'requested_configuration':config,'image':image,'inputs_sha256':checksum},
                configuration_key='service_configuration',image_key='service_image',root=updates.ROOT)
        else:
            revision=inputs.revision(repo,True)
            selected=images.local_action(box,'shared-alpine-v1',revision,'prepare')
            if selected.get('state') not in ('candidate-built','candidate-reused'):raise RuntimeError('DMZ image preparation did not complete')
            image=selected['operation_id']
            receipt=images.local_action(box,'shared-alpine-v1',revision,'image-receipt',image)
            public,values=configuration(box,repo=repo);public['data_uuid']=str(uuid.uuid4())
            release=receipt['files']['release-evidence.json']
            config={'dmz_initial':True,'box':box,'role':'dmz','image':image,'engine_commit':revision,
                    'instance_commit':inputs.revision(inputs.INSTANCE,True),'dmz_creation':public,
                    'image_evidence':{'kernel_release':release['kernel_release'],'packages_sha256':updates.digest(release['packages'])}}
            variables={'service_configuration':config,'service_image':image,'service_receipt':receipt,
                       'infrastructure_config':public,'dmz_runtime':values}
            work,checksum=inputs.freeze(repo,variables,configuration_key='service_configuration',root=updates.ROOT)
        return updates.finish_frozen(box,'dmz',work,variables,observed,image,image,checksum)


def finish_creation(box, work, variables, checksum):
    import os
    import time
    import vm_service_updates as updates
    import vm_service_probe as probe
    import vpn_egress_provisioning as transport
    repo=work/'public';config=variables['service_configuration'];image=variables['service_image']
    public=dict(variables['infrastructure_config'],dmz_inputs={'input_snapshot':config['input_snapshot'],'inputs_sha256':checksum})
    if updates.authority(box,'dmz')!='running':raise RuntimeError('DMZ intent changed before initial creation')
    transport.ansible('69-dmz-provision.yml',box+'-dom0,'+box+'-dmz',
        dict(variables['dmz_runtime'],infrastructure_box=box,infrastructure_role='dmz',
             infrastructure_image=image,infrastructure_config=public),repo=repo,inventory=work/'inventory/hosts.json')
    configure(box,work,variables['dmz_runtime'])
    after=updates.wait_probe(box,'dmz')
    options={'repo':repo,'inventory':work/'inventory/hosts.json','operation':image}
    state=updates.step(box,'dmz','creation-evidence',payload={'phase':'before','probe':after},**options)
    before=state['reboot_verification']['before']
    record={'kind':'klokast.vm-service-update.v1','operation_id':image,'box':box,'role':'dmz',
            'image':image,'new_uuid':state['uuid'],'data_uuid':state['data_uuid'],
            'requested_configuration':dict(config,before=before)}
    probe.verify(before,after,record)
    if after['boot_id']==before['boot_id']:
        if updates.authority(box,'dmz')!='running':raise RuntimeError('DMZ intent changed before initial reboot')
        result=subprocess.run(['tailscale','ssh','neo@'+box+'-dmz','doas','/sbin/reboot'],timeout=45,check=False)
        if result.returncode not in (0,255):raise RuntimeError('initial DMZ reboot command failed')
    deadline=time.monotonic()+300
    while after['boot_id']==before['boot_id']:
        if time.monotonic()>=deadline:raise RuntimeError('initial DMZ reboot verification is incomplete; rerun provision')
        time.sleep(5);after=updates.wait_probe(box,'dmz')
    probe.verify(before,after,record)
    updates.step(box,'dmz','creation-evidence',payload={'phase':'after','probe':after,'service':record},**options)
    return {'state':'complete','box':box,'role':'dmz','created':True,'image':image}
