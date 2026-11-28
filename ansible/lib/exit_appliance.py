"""Controller-owned disposable Tailscale exit appliance workflow."""
import ipaddress
import json
from pathlib import Path
import subprocess
import time

import appliance_build
from health_checks import check,tailnet_devices,tailnet_identity,xen_checks
import machine_status
import platform_source

REPO=Path(__file__).resolve().parents[2]
TAG='tag:exit'


def context(box):
    platform_source.require_controller()
    data=json.loads(subprocess.check_output([str(REPO/'ansible/bin/platform-resources'),'--app','exit-node','show'],text=True))
    specs=[s for s in data['app_vm_specs'] if s['app']=='exit-node' and s['node']==box]
    if len(specs)!=1:raise RuntimeError('declare exactly one exit appliance for the selected box')
    spec=specs[0]
    if spec['node_hostname']!=box+'-exit':raise RuntimeError('exit appliance name must be BOX-exit')
    if spec.get('node_domain_role')!='dmz':raise RuntimeError('exit appliance must be in the DMZ zone')
    # Network parameters are compiled and validated by the Platform compiler.
    interfaces=spec['guest_spec']['network_interfaces']
    gateway=next(line.split()[1] for line in interfaces.splitlines() if line.strip().startswith('gateway '))
    ipaddress.IPv4Address(gateway)
    return spec,dict(hostname=spec['node_hostname'],network_interfaces=interfaces,gateway=gateway)


def build(box):
    spec,config=context(box)
    files={name:appliance_build.FILES/name for name in ('exit-appliance.openrc','exit-start','exit-selftest')}
    appliance_build.build('exit',box,config,spec,TAG,files)


def verify(box):
    """Check the identity through the Tailscale API; the appliance has no management path."""
    spec,config=context(box)
    devices=json.loads(subprocess.check_output(['doas','/usr/local/sbin/ts-devices-list'],text=True)).get('devices',[])
    found=[d for d in devices if d.get('hostname','').split('.')[0]==config['hostname']]
    if len(found)!=1:raise RuntimeError('expected exactly one '+config['hostname']+' identity, found '+str(len(found)))
    device=found[0]
    if device.get('tags')!=[TAG]:raise RuntimeError(config['hostname']+' has unexpected tags: '+str(device.get('tags')))
    if device.get('connectedToControl') is not True:raise RuntimeError(config['hostname']+' is not connected to Tailscale; inspect its Xen console')
    return dict(hostname=config['hostname'],vm_ipv4_address=spec['vm_ipv4_address'],tags=device['tags'],
                connected=True,last_seen=device.get('lastSeen'))


def health(box):
    """Read-only checks without a login: the dom0 guest list and the Tailscale API.

    The appliance has no management path, so its forwarding is not exercised here.
    """
    spec,config=context(box)
    found=[check('Declaration','ok',config['hostname']+' in the DMZ at '+spec['vm_ipv4_address'])]
    guest=spec['guest_spec']['guest_name']
    try:found+=xen_checks(guest,machine_status.run_probe(box+'-dom0',machine_status.DOM0_PROBE))
    except (RuntimeError,OSError) as error:found.append(check('Xen guest','failed','cannot read '+box+'-dom0: '+str(error)[-200:]))
    try:found+=tailnet_identity(config['hostname'],[TAG],tailnet_devices(),time.time())
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        found.append(check('Tailnet identity','failed','cannot read the Tailnet devices: '+str(error)[-200:]))
    return {'checks':found}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('preflight','build','verify','health','stop','start','retire'))
    parser.add_argument('--box',required=True)
    args=parser.parse_args()
    if args.command=='preflight':
        spec,config=context(args.box);print(json.dumps(dict(hostname=config['hostname'],vm_ipv4_address=spec['vm_ipv4_address'])))
    elif args.command=='build':build(args.box)
    elif args.command=='verify':print(json.dumps(verify(args.box)))
    elif args.command=='health':print(json.dumps(health(args.box)))
    elif args.command in ('stop','start'):appliance_build.power_command('exit',args.box,args.command)
    else:
        _,config=context(args.box)
        appliance_build.retire('exit',args.box,config['hostname'],TAG)
