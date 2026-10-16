"""One controller flow for qualified shared, VPN and router VM replacement."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess

import infrastructure_images as images
import ops_replacement_inputs as inputs
import platform_resource_runtime as runtime
import platform_source
import vm_local_images
import vm_service_probe
import dmz_provisioning
import routing_profile
import platform_vpn_egress
import vpn_routing_runtime
import sys
import vpn_egress_provisioning
import platform_resource_model as model
import vm_router_updates
from platform_updates import digest

from vm_service_runtime import REPO, ROOT, ROLES, authority, profile, resume_command, step, target
from vm_service_execution import finish


def execute(box, role, *, image=None, resume=None, dry_run=False, lock_held=False, abandon=False, rebuild=False, retire=False, discard_root_data=False, migrate_root_data=False, maintenance=False):
    if migrate_root_data and (role not in ('ops','air') or resume or retire or abandon):
        raise RuntimeError('--migrate-root-data requires a new ops or air replacement')
    if maintenance and (role != 'air' or resume or retire or abandon):
        raise RuntimeError('--maintenance is only for a new runner replacement; resume uses its recorded decision')
    if (rebuild or retire) and role not in ROLES:
        raise RuntimeError('rebuild and generation retirement require a supported service role')
    if discard_root_data and (role not in dmz_provisioning.ROLES or resume or retire or abandon):
        raise RuntimeError('--discard-root-data requires a new BAK, DMZ or IoT replacement; resume reads its recorded decision')
    if rebuild and (resume or retire):
        raise RuntimeError('rebuild cannot accompany resume or retirement')
    with contextlib.nullcontext() if lock_held else runtime.vm_update_installation_lock():
        intended = authority(box, role)
        if intended == 'stopped': return {'state':'skipped','box':box,'role':role,'reason':'declared stopped'}
        if role == 'air' and not resume and not retire and not maintenance:
            before_idle = vm_service_probe.probe(box, role)
            if before_idle['busy_pids']:
                return {'state':'skipped','box':box,'role':role,'reason':'runner busy','busy_processes':len(before_idle['busy_pids'])}
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
            raise RuntimeError('incomplete service update; resume with: '+resume_command(box,record))
        if retire:
            return step(box, role, 'retire-plan' if dry_run else 'retire')
        if role in dmz_provisioning.ROLES and observed.get('creation') and not observed['creation'].get('service') and not observed.get('installed'):
            raise RuntimeError('initial shared guest acceptance is incomplete; resume with: ansible/bin/platform-update provision --box '+box+' --role '+role)
        if role in ('ops','air') and not resume and not dry_run and not observed.get('installed') and not migrate_root_data:
            raise RuntimeError('first infrastructure transition requires --migrate-root-data; no image was prepared')
        if role == 'ops' and migrate_root_data and not dry_run:
            import vm_control_updates
            if observed.get('installed'):
                raise RuntimeError('controller state is already retained; omit --migrate-root-data')
            vm_control_updates.grow_data(box, repo=REPO)
            observed = step(box, role, 'status')
        installed = observed['installed']
        if role in dmz_provisioning.ROLES and not resume and not installed and not assignment and observed.get('present') is False:
            if discard_root_data:raise RuntimeError('--discard-root-data requires an existing source root')
            return dmz_provisioning.provision(box,repo=REPO,role=role,dry_run=dry_run,lock_held=True,image=image)
        if resume:
            record = pending[0] if pending else observed.get('operation') or installed
            if not record or record['operation_id'] != resume or record['box'] != box or record['role'] != role: raise RuntimeError('resume does not select the current service operation')
            if abandon and record.get('stage') == 'abandoned':
                return {'state':'abandoned','box':box,'role':role,'operation_id':resume}
            if observed.get('operation_retired'):
                return {'state':'retired','box':box,'role':role,'operation_id':resume,'replaced':False}
            if not pending and not abandon:
                vm_service_probe.verify(record['requested_configuration']['before'], vm_service_probe.probe(box,role), record, unfinished=False)
                return {'state':'complete','box':box,'role':role,'image':record['image'],'replaced':False}
            work, variables = inputs.recover(record,configuration_key='service_configuration',image_key='service_image',root=ROOT)
            operation = resume; image = record['image']; checksum = record['inputs_sha256']
        else:
            if dry_run: return {'state':'planned','box':box,'role':role,'profile':profile(role),'installed_image':installed['image'] if installed else None,
                               'engine_commit':revision,'instance_commit':instance,'configuration_only':'health; no replacement or convergence',
                               'storage':{'source_disks':(observed.get('assignment') or {}).get('disks',{}),
                                          'root_gib':8,'data_policy':'reuse existing; allocate 50 GiB only when absent',
                                          'discard_root_data':discard_root_data} if role in dmz_provisioning.ROLES else
                               {'root_gib':100 if role=='ops' else 50,'data_gib':50,
                                'migrate_root_data':migrate_root_data,'maintenance':maintenance,
                                'source':observed.get('infrastructure_source')} if role in ('ops','air') else None}
            if image is None:
                built=images.local_action(box,profile(role),revision,'prepare')
                if built.get('state') not in ('candidate-built','candidate-reused'): raise RuntimeError('image preparation is incomplete')
                image=built['operation_id']
            if installed and image==installed['image'] and not rebuild and not discard_root_data and not migrate_root_data:
                vm_service_probe.verify(installed['requested_configuration']['before'], vm_service_probe.probe(box,role), installed, unfinished=False)
                return {'state':'complete','box':box,'role':role,'image':image,'replaced':False,
                        'configuration_update_needed':configuration_needed(installed,box,role,revision,instance)}
            receipt=images.local_action(box,profile(role),revision,'image-receipt',image)
            vm_local_images.validate(box,image,receipt['files'])
            recipe=json.loads((REPO/'ansible/update-profiles'/(profile(role)+'.json')).read_text())
            if receipt['files']['inputs.json']['profile_sha256'] != digest(images.qualify_profile(recipe,repo=REPO)):
                raise RuntimeError('selected service image requires qualification with the approved guest recipe')
            catalog = Path('/var/lib/klokast/updates/discovery')
            with (catalog/'build.lock').open('a') as catalog_lock:
                try:
                    fcntl.flock(catalog_lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise RuntimeError('active controller image preparation or cleanup is still running; retry this service update after it completes') from error
                vm_local_images.import_receipt(catalog/'builds',box,receipt)
            before=vm_service_probe.probe(box,role, repair=rebuild or discard_root_data)
            if before['workloads'].get('running'):
                raise RuntimeError('running applications require compatibility tests before replacement; no disks were allocated')
            if role in dmz_provisioning.ROLES:
                if before.get('application_writers'):
                    raise RuntimeError('application writers block shared guest replacement; no disks were allocated')
                if not before.get('persistent_storage') and not discard_root_data:
                    raise RuntimeError('first BAK, DMZ or IoT transition requires explicit --discard-root-data; nightly cannot discard data')
                if before.get('persistent_storage') and discard_root_data:
                    raise RuntimeError('shared guest already uses persistent storage; omit --discard-root-data')
            config={k:before[k] for k in ('root_partition','accounts','source_files')}
            if role not in ('router','ops'):
                selected = platform_source.snapshot()['instance']['boxes'][box].get('routing-profile')
                if selected is None: raise RuntimeError('declare a routing-profile before starting a new replacement')
                selected = routing_profile.validate(selected)
                config['bootstrap_dns'] = selected['bootstrap-dns']
            if role == 'vpn-egress':
                config['routing_profile'] = selected
                config['vpn_routing'] = {'rule-providers': routing_profile.providers(selected),
                    'rules': platform_vpn_egress.routing_rules(selected),
                    'nameserver': [v+'#VPN' for v in selected['vpn-dns']]}
            release = receipt['files']['release-evidence.json']
            if role in ('ops','air'):
                from platform_updates import no_application_release
                candidate=receipt['files']['candidate.json'];boot=candidate['boot_test']
                no_application_release(receipt['files']['inputs.json'],candidate,boot.get('openrc_test',{}),
                    boot.get('personalized_test',{}),boot.get('maintenance_restore',{}),require_infrastructure_storage=True)
            config.update(box=box,role=role,image=image,before=before,engine_commit=revision,instance_commit=instance,
                          image_evidence={'kernel_release':release['kernel_release'],'packages_sha256':digest(release['packages'])})
            variables={'service_configuration':config,'service_image':image,'service_receipt':receipt}
            if role in ('ops','air'):
                import vm_control_updates
                config.update(vm_control_updates.configuration(box,role,before,observed,
                    migrate=migrate_root_data,maintenance=maintenance))
                if role=='air':
                    variables['vpn_network']=vm_control_updates.network_configuration(box,config['infrastructure_creation'])
                if role=='ops':
                    variables['ops_instance_repo_url']=inputs.run(['git','-C',inputs.INSTANCE,'remote','get-url','origin'])
            if role == 'router':
                router, values = vm_router_updates.configuration(box,before,observed,receipt['files']['inputs.json'])
                config.update(router)
                component = receipt['files']['inputs.json']['tailscale']
                config['image_evidence'].update(tailscale_version=component['version'],
                    tailscale_sha256=component['tailscale_sha256'],tailscaled_sha256=component['tailscaled_sha256'])
                variables['router_verification'] = values
            if role in dmz_provisioning.ROLES:
                public, values=dmz_provisioning.configuration(box,repo=REPO,role=role)
                config.update({role+'_creation':public,'discard_root_data':discard_root_data})
                variables[role+'_runtime']=values
            if role == 'vpn-egress':
                snapshot = platform_source.snapshot()
                topology = model.load_topology(repo_root=REPO)
                declared = model.registry_box_configs(snapshot['rendered']['projection']['registry'], topology)[box]
                values = vpn_egress_provisioning.prepare_configuration(box, declared, topology, selected)
                values.update(vpn_egress_uid=before['accounts']['vpn-egress'][0],
                              vpn_egress_gid=before['accounts']['vpn-egress'][1])
                config['vpn_creation'] = vpn_egress_provisioning.creation_config(box, topology, selected)
                config['vpn_configuration_sha256'] = digest(values['vpn_egress_config'])
                config['vpn_intent_sha256'] = vpn_egress_provisioning.configuration_signature(box,declared,topology,repo=REPO)
                current = snapshot['rendered']['projection']
                view = dict(registry=current['registry'], registry_sha256=current['registry_sha256'],
                    registry_path=str(platform_source.INSTANCE/'klokast-instance.json'),
                    installed_routing_profiles=vpn_routing_runtime.installed_profiles(current['registry']),
                    installed_air_bootstrap=vpn_routing_runtime.installed_air_bootstrap(current['registry']))
                view['installed_routing_profiles'][box] = selected
                variables.update(vpn_runtime=values, vpn_network=view)
            work,checksum=inputs.freeze(REPO,variables,configuration_key='service_configuration',root=ROOT)
            operation=secrets.token_hex(12)
            # A lost prepare response must leave the exact resume command locally.
            inputs.save(work/'operation.json', {'box':box,'role':role,'operation_id':operation,'inputs_sha256':checksum})
        if dry_run: return {'state':'planned','box':box,'role':role,'resume':operation,'image':image}
        return finish_frozen(box, role, work, variables, observed, operation, image, checksum,
                             resume=resume, abandon=abandon)


def finish_frozen(box, role, work, variables, observed, operation, image, checksum, *, resume=None, abandon=False):
    """Start a clean interpreter so every dependency comes from the verified snapshot."""
    request = {'box': box, 'role': role, 'operation': operation, 'image': image,
               'inputs_sha256': checksum, 'resume': resume, 'abandon': abandon,
               'observed': observed, 'requested_configuration': variables['service_configuration']}
    result = subprocess.run([sys.executable, '-I', str(work/'public/ansible/bin/vm-service-resume')],
                            input=json.dumps(request), text=True, capture_output=True,
                            cwd=work/'public', timeout=10800)
    with (work/'execution.log').open('a') as log:
        os.chmod(log.name, 0o600)
        log.write(result.stdout); log.write(result.stderr)
    if result.returncode:
        if variables['service_configuration'].get(role+'_initial'):
            raise RuntimeError('frozen shared guest creation failed; inspect '+str(work/'execution.log')+'; resume with: ansible/bin/platform-update provision --box '+box+' --role '+role)
        raise RuntimeError('frozen service execution failed; inspect '+str(work/'execution.log')+
                           '; resume with: ansible/bin/platform-update update --box '+box+' --role '+role+' --resume '+operation)
    return json.loads(result.stdout.strip().splitlines()[-1])


def configuration_needed(installed, box, role, revision, instance):
    config=installed.get('convergence', installed['requested_configuration'])
    if role=='vpn-egress' and 'vpn_intent_sha256' in config:
        snapshot=platform_source.snapshot();topology=model.load_topology(repo_root=REPO)
        declared=model.registry_box_configs(snapshot['rendered']['projection']['registry'],topology)[box]
        return config['vpn_intent_sha256']!=vpn_egress_provisioning.configuration_signature(box,declared,topology,repo=REPO)
    return any(config.get(k)!=v for k,v in (('engine_commit',revision),('instance_commit',instance)))
