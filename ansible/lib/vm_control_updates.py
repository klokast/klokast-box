"""Controller and runner configuration within the common VM lifecycle."""
import os
from pathlib import Path
import subprocess
import tempfile

import ops_replacement_inputs as inputs
import platform_source
from vm_control_configuration import guest_config


def configuration(box, role, before, observed, *, migrate, maintenance):
    view = platform_source.snapshot()
    public = guest_config(box, role, *before['accounts'].get('agent', [1004,1004]),
                          view=view['rendered']['projection'])
    source = observed.get('infrastructure_source')
    persistent = bool(before.get('persistent_storage'))
    if not persistent and not migrate:
        raise RuntimeError('first infrastructure transition requires --migrate-root-data; nightly cannot migrate state')
    if persistent and migrate:
        raise RuntimeError('infrastructure state is already retained; omit --migrate-root-data')
    values = {'infrastructure_creation': public, 'infrastructure_source': source,
              'migrate_root_data': migrate, 'maintenance': maintenance}
    if role == 'air' and maintenance:
        import air_maintenance
        reservation = air_maintenance.remote(box, 'status').get('reservation')
        values['maintenance_reservation'] = reservation['operation'] if reservation else None
    if role == 'ops':
        active = view['instance']['controllers']['active']
        if before['guard'].get('active_box') != active:
            raise RuntimeError('standby authority differs from current Instance')
        public['replacement'] = {'active_box':active, 'old_root_partition':before['root_partition'],
                                 'accounts':{n:before['accounts'][n] for n in ('smith','minion')}}
        values['active_box'] = active
        import vm_control_toolchains
        values['toolchains'] = vm_control_toolchains.resolve()
    return values


def playbook(box, name, variables, *, repo, inventory=None):
    with tempfile.TemporaryDirectory(prefix='infrastructure-update-') as temporary:
        path=Path(temporary)/'variables.json'; inputs.save(path,variables)
        result=subprocess.run(['ansible-playbook','-vv','-i',str(inventory or repo/'ansible/execution-inventory/hosts'),
            str(repo/'ansible/playbooks'/name),'--limit',box,'-e','@'+str(path)],cwd=repo,
            env=dict(os.environ,ANSIBLE_CONFIG=str(repo/'ansible/ansible.cfg')),timeout=7200)
        if result.returncode:raise RuntimeError('infrastructure configuration or verification failed; inspect the operation log')


def configure(box, role, work, variables):
    config=variables['service_configuration']
    values={'service_configuration':config,'service_role':role,'service_box':box,
            'ops_replace_box':box,'ops_replace_active_box':config.get('active_box'),
            'ops_instance_repo_url':variables.get('ops_instance_repo_url',''),
            **({'ops_frozen_toolchain':config['toolchains']} if role=='ops' else {}),
            'ops_replacement':{'stage':'booted','requested_configuration':config},
            'infrastructure_agent_uid':config['accounts'].get('agent',[1004,1004])[0],
            'infrastructure_agent_gid':config['accounts'].get('agent',[1004,1004])[1]}
    if role=='ops':
        nightly=config['before']['nightly']
        values.update(ops_controller_nightly_service_roles=nightly['roles'],
                      ops_controller_nightly_services=nightly['all'],
                      ops_controller_nightly_enabled=nightly['enabled'])
    playbook(box,'74-vm-infrastructure-configure.yml',values,repo=work/'public',inventory=work/'inventory/hosts.json')


def grow_data(box, *, repo):
    """Explicit first-migration maintenance, before replacement inputs freeze."""
    playbook(box, '66-ops-data.yml', {'ops_data_size_gib': 50}, repo=repo)


def network_configuration(box, public):
    import vpn_routing_runtime
    current=platform_source.snapshot()['rendered']['projection']
    view=dict(registry=current['registry'],registry_sha256=current['registry_sha256'],
        registry_path=str(platform_source.INSTANCE/'klokast-instance.json'),
        installed_routing_profiles=vpn_routing_runtime.installed_profiles(current['registry']),
        installed_air_bootstrap=vpn_routing_runtime.installed_air_bootstrap(current['registry']))
    view['installed_air_bootstrap'][box]=public['bootstrap_dns']
    return view
