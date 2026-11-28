"""Controller-owned disposable print appliance workflow."""
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
    _,spec,config=context(box)
    from jinja2 import Environment,FileSystemLoader,StrictUndefined
    template_dir=REPO/'apps/print-server/ansible/roles/print-server/templates'
    cups=Environment(loader=FileSystemLoader(template_dir),undefined=StrictUndefined).get_template('cupsd.conf.j2').render(
        print_tailscale_hostname=config['hostname'],print_tailnet_dns_suffix=subprocess.check_output([str(REPO/'ansible/bin/magicdns-suffix')],text=True).strip(),
        print_lan_enabled=bool(config['lan_subnet']),print_lan_subnet=config['lan_subnet'])
    files={name:appliance_build.FILES/name for name in ('print-appliance.openrc','print-start','print-selftest')}
    appliance_build.build('print',box,config,spec,'tag:print',dict(files,**{'cupsd.conf':cups}))


def health(box):
    """Read-only Platform checks without a login: the dom0 guest list and the Tailscale API.

    print-serverctl health adds the CUPS queue checks over IPP.
    """
    _,spec,config=context(box)
    found=[check('Declaration','ok','%s in the backend zone at %s; printer %s; LAN printing %s'%(
        config['hostname'],spec['vm_ipv4_address'],config['printer'],'on' if config['lan_subnet'] else 'off'))]
    guest=spec['guest_spec']['guest_name']
    try:found+=xen_checks(guest,machine_status.run_probe(box+'-dom0',machine_status.DOM0_PROBE))
    except (RuntimeError,OSError) as error:found.append(check('Xen guest','failed','cannot read '+box+'-dom0: '+str(error)[-200:]))
    try:found+=tailnet_identity(config['hostname'],['tag:print'],tailnet_devices(),time.time())
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        found.append(check('Tailnet identity','failed','cannot read the Tailnet devices: '+str(error)[-200:]))
    return {'checks':found}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('build','preflight','health'))
    parser.add_argument('--box',required=True)
    args=parser.parse_args()
    if args.command=='preflight':
        _,spec,config=context(args.box);print(json.dumps(dict(hostname=spec['node_hostname'],vm_ipv4_address=spec['vm_ipv4_address'],lan_subnet=config['lan_subnet'])))
    elif args.command=='health':print(json.dumps(health(args.box)))
    else:build(args.box)
