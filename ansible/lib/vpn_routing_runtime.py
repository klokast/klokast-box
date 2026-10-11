"""Controller orchestration for existing protected VPN routing assignments."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import routing_profile


def remote(box, action='read', **values):
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', box): raise ValueError('invalid routing target box')
    code = Path(__file__).with_name('vpn_routing_assignment.py').read_text()
    payload = dict(values, box=box, action=action)
    p = subprocess.run(['tailscale', 'ssh', 'neo@'+box+'-dom0',
                        'doas python3 -c '+shlex.quote(code)], input=json.dumps(payload),
                       text=True, capture_output=True, timeout=120)
    if p.returncode or len(p.stdout) > 65536:
        raise RuntimeError('cannot inspect protected VPN routing assignment for '+box+': '+p.stderr[-1000:])
    value = json.loads(p.stdout)
    if action == 'read-air': return value
    if value['profile'] is not None: value['profile'] = routing_profile.validate(value['profile'])
    return value


def installed_profiles(registry):
    result = {}
    for box, config in registry.get('boxes', {}).items():
        if 'vpn_egress' not in config or 'routing_profile' not in config: continue
        value = remote(box)
        if value['state'] == 'absent': continue
        if value['profile'] is None:
            raise RuntimeError('VPN routing profile is not recorded for '+box+'; run platform-vpn-egress configure --box '+box+' to adopt the matching installed profile')
        result[box] = value['profile']
    return result


def guest_profile(box):
    code = """import json, pathlib, yaml
c=yaml.safe_load(pathlib.Path('/etc/vpn-egress/config.yml').read_text())
print(json.dumps({'dns':c['dns'],'rule-providers':c['rule-providers'],
                 'uuid':pathlib.Path('/sys/hypervisor/uuid').read_text().strip()}))
"""
    p = subprocess.run(['tailscale','ssh','neo@'+box+'-vpn-egress','doas python3 -c '+shlex.quote(code)],
                       capture_output=True,text=True,timeout=90)
    if p.returncode or len(p.stdout)>65536: raise RuntimeError('cannot read installed gateway routing settings')
    value=json.loads(p.stdout)
    return routing_profile.from_config(value), value['uuid']


def select(box, desired, *, adopt=False, readonly=False, create=False):
    desired=routing_profile.validate(desired)
    value=remote(box)
    if value['state']=='absent': return desired, value
    if not readonly and value['state'] not in ('ready','complete','adopted','recovered') and not (create and value['state'] in ('allocated', 'copied')):
        raise RuntimeError('VPN replacement is incomplete; resume its recorded operation before maintenance')
    if value['profile'] is None:
        if not adopt: raise RuntimeError('installed VPN routing profile needs adoption through configure')
        actual, identity=guest_profile(box)
        if actual!=desired: raise RuntimeError('legacy gateway profile differs from Instance; restore matching desired values before adoption')
        value=remote(box,'adopt',profile=actual,uuid=identity)
    return value['profile'], value


def installed_air_bootstrap(registry):
    result={}
    for box in registry.get('boxes', {}):
        if box+'-air' not in registry.get('airunners', []): continue
        values=remote(box, 'read-air')['bootstrap_dns']
        if values is None: continue
        if not isinstance(values, list) or len(values)!=2 or values[0]==values[1] or not all(routing_profile.public_ipv4(v) for v in values):
            raise RuntimeError('installed runner bootstrap DNS is invalid')
        result[box]=values
    return result
