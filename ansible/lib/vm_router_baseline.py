"""Controller orchestration for the explicit Ansible router baseline playbook."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

import ops_replacement_inputs as inputs
import platform_resource_runtime as runtime
import platform_source
import router_baseline as baseline
import router_updates
import vm_router_updates as router
import vm_service_runtime as service

REPO=Path(__file__).resolve().parents[2]
ROOT=Path('/var/lib/klokast/updates/discovery/router')


def guest_probe(box):
    state_code=(REPO/'ansible/roles/vm-template-builder/files/vm_router_state.py').read_text()
    detail="\nstate_scope={'__name__':'baseline_state_check'}\nexec("+repr(state_code)+",state_scope)\n"+r'''
import re,stat
for name,(_,maximum,required) in state_scope['FILES'].items():
    item=state_scope['read'](P('/'),name,maximum,optional=not required)
    if item is not None:
        state_scope['ownership'](name,item[1],owner)
        if not item[0] and name!='var/lib/misc/dnsmasq.leases':raise RuntimeError('empty router identity: '+name)
for name in result['configuration_files']:
    path=P('/')/name
    for parent in path.parents:
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise RuntimeError('unsafe router configuration parent: '+str(parent))
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or (info.st_uid,info.st_gid)!=(0,0) or info.st_mode&0o022:
        raise RuntimeError('unsafe router configuration: '+name)
for name in ('etc/network/if-up.d/91-klokast-ops-ipv6','etc/sysctl.d/91-klokast-ops-ipv6.conf','etc/dnsmasq.d/91-klokast-ops-ipv6.conf'):
    if (P('/')/name).exists() or (P('/')/name).is_symlink():raise RuntimeError('retired controller IPv6 hook remains: '+name)
overlay='etc/klokast/overlay-ipv6.nft'
if overlay in result['configuration_files']:
    if (P('/')/overlay).read_text() not in ('# Ops IPv6 downstream is disabled.\n','# Ops uses its own uplink.\n'):
        raise RuntimeError('unexpected router IPv6 include')
    result['configuration_files'].pop(overlay)
for name,content in (
    ('etc/klokast/app-resources/vm-input.nft','# Legacy aggregate include retired by keyed platform-resources.\n'),
    ('etc/klokast/app-resources/vm-input.d/000-empty.nft','# Empty placeholder so nft include globs always match.\n')):
    if name in result['configuration_files']:
        if (P('/')/name).read_text()!=content:raise RuntimeError('unexpected VM policy on router: '+name)
        result['configuration_files'].pop(name)
result['inactive_includes']=[name for name in result['configuration_files']
    if name.startswith('etc/klokast/app-resources/router-forward.d/') and
    all(not line.strip() or line.lstrip().startswith('#') for line in (P('/')/name).read_text().splitlines())]
release=P('/etc/alpine-release').read_text().strip()
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',release):raise RuntimeError('unsupported router Alpine release')
result['packages']=packages;result['alpine_branch']='v'+'.'.join(release.split('.')[:2])
'''
    value=router.probe(box,detail_code=detail)
    return {k:value[k] for k in ('identity','router_accounts','root_partition','persistent_storage',
                                'xen_uuid','kernel','packages','alpine_branch','configuration_files','inactive_includes')}


def verify_policy(box, guest, work):
    def compiled():
        return json.loads(subprocess.check_output([str(REPO/'ansible/bin/platform-resources'),'show'],text=True,timeout=180))
    policy=compiled();includes=router_updates.rendered_includes(policy,box)
    expected={k.lstrip('/'):hashlib.sha256(v.encode()).hexdigest() for k,v in includes.items()}
    core={'etc/network/interfaces','etc/dhcpcd.conf','etc/dnsmasq.conf','etc/nftables.nft'}
    actual={k:v for k,v in guest['configuration_files'].items() if k not in core}
    if (any(actual.get(k)!=v for k,v in expected.items()) or
            actual.keys()-expected.keys()-set(guest['inactive_includes'])):
        raise RuntimeError('router baseline firewall includes differ from current Instance policy; reconcile configuration first')
    values=router.resource_compiler.box_access_router_vars(policy,box)
    inputs.save(work/'verify.json',dict(values,router_baseline_box=box))
    with (work/'verify.log').open('w') as log:
        os.chmod(log.name,0o600)
        subprocess.run(['ansible-playbook','-vv','-i',str(REPO/'ansible/execution-inventory/hosts'),
            str(REPO/'ansible/playbooks/74-router-baseline-verify.yml'),'-e','@'+str(work/'verify.json')],
            cwd=REPO,env=dict(os.environ,ANSIBLE_CONFIG=str(REPO/'ansible/ansible.cfg')),
            stdout=log,stderr=log,check=True,timeout=600)
    if compiled()!=policy:raise RuntimeError('Instance router policy changed during baseline verification')


def execute(box):
    platform_source.require_controller();runtime.require_active_controller()
    with runtime.vm_update_installation_lock():
        if service.authority(box,'router')!='running':raise RuntimeError('router baseline requires declared running state')
        engine=inputs.revision(REPO,True);instance=inputs.revision(inputs.INSTANCE,True)
        observed=service.step(box,'router','baseline',payload={'engine_commit':engine})
        work=ROOT/secrets.token_hex(12)
        work.mkdir(mode=0o700,parents=True)
        guest=guest_probe(box)
        verify_policy(box,guest,work)
        if guest_probe(box)!=guest:raise RuntimeError('router identity or configuration changed during baseline inspection')
        saved=observed['baseline']
        if saved:
            baseline.validate(saved,box)
            if saved['guest']!=guest or saved['observation']!=observed['observation']:
                raise RuntimeError('router changed during interrupted baseline publication; preserve its protected evidence')
            request=saved
        else:
            request=baseline.proposal(box,secrets.token_hex(12),engine,instance,observed['observation'],guest)
        baseline.validate(request,box)
        inputs.save(work/'publication.json',request)
        # This record is descriptive evidence. Current controller and Instance
        # authority are checked again before the protected publication step.
        if inputs.revision(REPO,True)!=engine or inputs.revision(inputs.INSTANCE,True)!=instance:
            raise RuntimeError('baseline implementation or Instance changed during verification')
        result=service.step(box,'router','baseline',payload={'engine_commit':engine,'publication':request})
        inputs.save(work/'result.json',result)
        return dict(result,evidence_directory=str(work))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--box',required=True)
    try:print(json.dumps(execute(parser.parse_args().box),sort_keys=True))
    except (RuntimeError,OSError,ValueError,subprocess.SubprocessError) as error:
        print('router baseline: '+str(error),file=sys.stderr);raise SystemExit(1)
