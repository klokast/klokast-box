"""Advance one service operation using only its verified frozen source."""
import os
import subprocess
import time

import dmz_provisioning
import platform_source
import vm_service_probe
import vm_service_runtime as service
import vpn_egress_provisioning
import vpn_routing_runtime


def reconcile_vpn_network(box, work, view):
    """Use the frozen Ansible roles and collection settings during resume too."""
    previous = os.environ.get('ANSIBLE_CONFIG')
    os.environ['ANSIBLE_CONFIG'] = str(work/'public/ansible/ansible.cfg')
    try:
        vpn_egress_provisioning.network(box, view, repo=work/'public', inventory=work/'inventory/hosts.json')
    finally:
        if previous is None:
            os.environ.pop('ANSIBLE_CONFIG', None)
        else:
            os.environ['ANSIBLE_CONFIG'] = previous


def finish(box, role, work, variables, observed, operation, image, checksum, *, resume=None, abandon=False):
    config=variables['service_configuration']
    options={'repo':work/'public','inventory':work/'inventory/hosts.json','operation':operation,'checksum':checksum}
    state=None
    try:
        if abandon:
            state=service.step(box,role,'abandon',**options)
            return {'state':state['stage'],'box':box,'role':role,'operation_id':operation}
        state=service.step(box,role,'prepare',payload=config,receipt=variables['service_receipt'],**options)
        for _ in range(8):
            if role == 'vpn-egress' and state.get('journal', {}).get('stage', (observed.get('assignment') or {}).get('stage')) in ('stopped', 'starting', 'booted') and 'routing_profile' in config:
                if 'vpn_network' in variables:
                    service.authority(box, role)
                    view = variables['vpn_network']
                    vpn_egress_provisioning.require_network_authority(view)
                else:
                    current = platform_source.snapshot()['rendered']['projection']
                    view = dict(registry=current['registry'], registry_sha256=current['registry_sha256'],
                                registry_path=str(platform_source.INSTANCE/'klokast-instance.json'),
                                installed_routing_profiles=vpn_routing_runtime.installed_profiles(current['registry']),
                                installed_air_bootstrap=vpn_routing_runtime.installed_air_bootstrap(current['registry']))
                    view['installed_routing_profiles'][box] = config['routing_profile']
                reconcile_vpn_network(box, work, view)
            state=service.step(box,role,'advance',**options)
            stage=state['journal']['stage']
            if stage in ('booted','complete'): break
        if stage=='complete': return {'state':'complete','box':box,'role':role,'image':image,'replaced':False}
        if stage!='booted': raise RuntimeError('service did not reach the recorded boot stage')
        if role == 'vpn-egress' and 'vpn_runtime' in variables:
            configure_vpn(box, work, variables['vpn_runtime'])
        if role in dmz_provisioning.ROLES and role+'_runtime' in variables:
            dmz_provisioning.configure(box,work,variables[role+'_runtime'])
        after=service.wait_probe(box,role)
        vm_service_probe.verify(config['before'],after,state)
        verification=state['reboot_verification']
        if verification['status']=='required':
            state=service.step(box,role,'reboot-start',payload={'boot_id':after['boot_id'],'identity':after['identity']},**options)
            verification=state['reboot_verification']
        if verification['status']=='pending':
            if after['boot_id']==verification['before']['boot_id']:
                if service.authority(box,role) != 'running':
                    raise RuntimeError('service runtime intent changed before reboot; preserve the pending verification')
                reboot = subprocess.run(['tailscale','ssh','neo@'+box+'-'+role,'doas','/sbin/reboot'],timeout=45,check=False)
                if reboot.returncode not in (0, 255):
                    raise RuntimeError('service reboot command failed; keep the pending reboot verification')
                deadline=time.monotonic()+300
                while True:
                    after=service.wait_probe(box,role)
                    if after['boot_id']!=verification['before']['boot_id']: break
                    if time.monotonic()>=deadline: raise RuntimeError('new service boot ID did not change')
                    time.sleep(5)
            checks=vm_service_probe.verify(config['before'],after,state)
            state=service.step(box,role,'reboot-complete',payload={'boot_id':after['boot_id'],'identity':after['identity']},**options)
        if role=='vpn-egress':
            service.authority(box,role)
            if 'vpn_runtime' in variables:
                vpn_egress_provisioning.ansible('69-vpn-egress-verify.yml', box, variables['vpn_runtime'],
                    repo=work/'public', inventory=work/'inventory/hosts.json')
            else:
                subprocess.run([str(work/'public/ansible/bin/platform-vpn-egress'),'verify','--box',box],check=True,timeout=600)
        checks=vm_service_probe.verify(config['before'],after,state)
        service.step(box,role,'checks',payload=checks,**options)
        service.step(box,role,'advance',**options)
        state=service.step(box,role,'advance',**options)
        if state['journal']['stage']!='complete': raise RuntimeError('service acceptance is incomplete')
        return {'state':'complete','box':box,'role':role,'image':image,'operation_id':operation,'replaced':True}
    except (RuntimeError,OSError,ValueError,subprocess.SubprocessError) as error:
        if state is None and not resume:
            raise RuntimeError(str(error)+service.prepare_failure_hint(box,role,operation,image)) from error
        raise RuntimeError(str(error)+'; resume with: ansible/bin/platform-update update --box '+box+' --role '+role+' --resume '+operation) from error


def configure_vpn(box, work, values):
    deadline = time.monotonic()+300
    while True:
        service.authority(box, 'vpn-egress')
        try:
            vpn_egress_provisioning.configure(box, values, repo=work/'public', inventory=work/'inventory/hosts.json')
            return
        except (RuntimeError, subprocess.SubprocessError):
            if time.monotonic() >= deadline:
                raise RuntimeError('VPN configuration did not complete within five minutes; preserve the new generation')
            time.sleep(5)
