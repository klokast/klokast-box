"""Router inputs and native health checks for the common VM transaction."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import uuid

import ops_replacement_inputs as inputs
import platform_resource_compiler as resource_compiler
import router_personalize
import router_updates
from platform_updates import digest

REPO = Path(__file__).resolve().parents[2]

# Return hashes and public identity only. Native state stays in the router.
PROBE = r'''
import grp, hashlib, json, os, pathlib, pwd, subprocess, sys
P = pathlib.Path
def run(argv):
    p = subprocess.run(argv,capture_output=True,text=True,timeout=30)
    if p.returncode or len(p.stdout)>1024**2: raise RuntimeError('router probe failed: '+argv[0])
    return p.stdout.strip()
def sha(path):
    p=P(path)
    if not p.exists(): return None
    if p.is_symlink() or not p.is_file(): raise RuntimeError('unsafe router identity path: '+path)
    return hashlib.sha256(p.read_bytes()).hexdigest()
tail=json.loads(run(['tailscale','status','--json']))
if tail['BackendState']!='Running' or not tail['Self']['Online']: raise RuntimeError('router Tailscale is not running')
keys={str(p):sha(str(p)) for directory in ('/etc/ssh','/var/lib/tailscale/ssh')
      for p in P(directory).glob('ssh_host_*')}
native=json.loads(P('/var/lib/tailscale/tailscaled.state').read_text())
machine=native.get('_machinekey')
if not isinstance(machine,str) or not machine: raise RuntimeError('router has no supported native machine key')
for path in ('/var/lib/tailscale/tka','/var/lib/tailscale/tpm-sealed'):
    if P(path).exists() or P(path).is_symlink(): raise RuntimeError('router has unsupported identity storage')
owner={'dnsmasq_uid':pwd.getpwnam('dnsmasq').pw_uid,'dnsmasq_gid':grp.getgrnam('dnsmasq').gr_gid,
       'tailscale_gid':grp.getgrnam('tailscale').gr_gid}
for name in ('dnsmasq','tailscale'):
    if len([g for g in grp.getgrall() if g.gr_gid==owner[name+'_gid']])!=1: raise RuntimeError('router has conflicting service groups')
if len([u for u in pwd.getpwall() if u.pw_uid==owner['dnsmasq_uid']])!=1: raise RuntimeError('router has conflicting dnsmasq ownership')
root=next(line.split()[0] for line in P('/proc/mounts').read_text().splitlines() if line.split()[1]=='/')
if root not in ('/dev/xvda','/dev/xvda3'): raise RuntimeError('unsupported router root layout')
storage=None
if P('/etc/klokast/router-storage.json').exists():
    sys.path.insert(0,'/usr/local/libexec')
    import vm_router_state
    storage=vm_router_state.verify()
for name in ('dhcpcd','dnsmasq','nftables','tailscale','ntpd'): run(['rc-service',name,'status'])
run(['dnsmasq','--test']);run(['nft','-c','-f','/etc/nftables.nft'])
packages={}
for block in P('/lib/apk/db/installed').read_text().strip().split('\n\n'):
    row={line[0]:line[2:] for line in block.splitlines() if line.startswith(('P:','V:'))}
    if set(row)!={'P','V'} or row['P'] in packages: raise RuntimeError('invalid router package manifest')
    packages[row['P']]=row['V']
files={str(p).lstrip('/'):sha(str(p)) for p in (P('/etc/network/interfaces'),P('/etc/dhcpcd.conf'),
       P('/etc/dnsmasq.conf'),P('/etc/nftables.nft'))}
files.update({str(p).lstrip('/'):sha(str(p)) for p in P('/etc/klokast').rglob('*.nft')})
result={'identity':{'hostname':os.uname().nodename,'tailscale_id':tail['Self']['ID'],
    'tailscale_hostname':tail['Self']['HostName'],'tailscale_tags':sorted(tail['Self'].get('Tags',[])),
    'machine_key':hashlib.sha256(machine.encode()).hexdigest(),'ssh_keys':keys,
    'machine_id':sha('/etc/machine-id'),
    'duid':sha('/var/lib/dhcpcd/duid'),'privacy_secret':sha('/var/lib/dhcpcd/secret')},
    'accounts':{'neo':[pwd.getpwnam('neo').pw_uid,pwd.getpwnam('neo').pw_gid]},
    'router_accounts':owner,'root_partition':'3' if root.endswith('3') else '',
    'source_files':{},'configuration_files':files,'workloads':{},'persistent_storage':storage,
    'retained_uuid':storage['filesystem_uuid'] if storage else None,
    'kernel':os.uname().release,'kernel_modules':P('/lib/modules/'+os.uname().release).is_dir(),
    'packages_sha256':hashlib.sha256(json.dumps(packages,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
    'boot_id':P('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'xen_uuid':P('/sys/hypervisor/uuid').read_text().strip(),
    'tailscale_version':run(['tailscale','version']).splitlines()[0],
    'tailscale_sha256':sha('/usr/local/bin/tailscale'), 'tailscaled_sha256':sha('/usr/local/sbin/tailscaled'),
    'forwarding':P('/proc/sys/net/ipv4/ip_forward').read_text().strip()=='1',
    'wan':any(line.startswith('default via ') for line in run(['ip','-4','route']).splitlines()),
    'firewall':bool(run(['nft','list','ruleset'])),'online':True,'service':True}
if not all(result['identity'][k] for k in ('duid','privacy_secret')): raise RuntimeError('router DHCP identity is incomplete')
'''


def probe(box, *, repair=False, detail_code=''):
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}',box): raise RuntimeError('invalid router probe target')
    script = ("set -eu\ndoas python3 - <<'ROUTER_PROBE'\n"+PROBE+detail_code+
              '\nprint(json.dumps(result,sort_keys=True))\nROUTER_PROBE\n')
    result = subprocess.run(['tailscale','ssh','neo@'+box+'-router','sh','-s'],
        input=script,text=True,capture_output=True,timeout=180)
    if result.returncode or len(result.stdout)>1024**2:
        raise RuntimeError('router native probe failed: '+result.stderr[-2000:])
    value=json.loads(result.stdout)
    if (value['identity']['hostname']!=box+'-router' or value['identity']['tailscale_hostname']!=box+'-router' or
            value['accounts']!={'neo':[1000,1000]} or
            not all(value[k] for k in ('kernel_modules','online','firewall','forwarding','wan'))):
        raise RuntimeError('router identity, accounts, WAN or service health differs')
    return value


def render(box, manifest, *, repo=REPO):
    """Freeze the current compiler policy through the normal Ansible templates."""
    def compiled():
        return json.loads(subprocess.run([str(repo/'ansible/bin/platform-resources'),'show'],
            check=True,capture_output=True,text=True,timeout=180).stdout)
    policy=compiled()
    includes=router_updates.rendered_includes(policy,box)
    values=resource_compiler.box_access_router_vars(policy,box)
    work=Path('/var/lib/klokast/updates/discovery/router')/secrets.token_hex(12)
    work.mkdir(mode=0o700,parents=True)
    args=dict(values,router_candidate_box=box,router_candidate_result_dir=str(work),
        router_candidate_manifest=manifest,router_candidate_packages={p['name']:p['version'] for p in manifest['packages']},
        router_candidate_includes={k.lstrip('/'):v for k,v in includes.items()},
        router_candidate_overlay=None,router_candidate_overlay_files={})
    inputs.save(work/'arguments.json',args)
    with (work/'render.log').open('x') as log:
        os.chmod(log.name,0o600)
        subprocess.run(['ansible-playbook','-i',str(repo/'ansible/execution-inventory/hosts'),
            str(repo/'ansible/playbooks/74-router-candidate-render.yml'),'--limit',box+'-router','-e','@'+str(work/'arguments.json')],
            cwd=repo,env=dict(os.environ,ANSIBLE_CONFIG=str(repo/'ansible/ansible.cfg')),stdout=log,stderr=log,check=True,timeout=300)
    config=json.loads((work/'personalization.json').read_text());router_personalize.validate(config)
    if (compiled()!=policy or config['box']!=box or config['inputs_sha256']!=manifest['inputs_sha256'] or
            config['packages']!=args['router_candidate_packages'] or
            any(config['files'].get(k.lstrip('/'))!=v for k,v in includes.items())):
        raise RuntimeError('router policy changed during rendering; retry with current inputs')
    return config, values


def configuration(box, before, observed, manifest):
    config, values = render(box,manifest)
    stored=before.get('persistent_storage')
    if bool(stored)!=bool(observed.get('assignment')):
        raise RuntimeError('router storage and protected boot assignment disagree')
    contract=({k:v for k,v in stored.items() if k!='mounted'} if stored else
        {'kind':'klokast.router-retained-state.v1','box':box,'filesystem_uuid':str(uuid.uuid4()),'accounts':before['router_accounts']})
    source=None if stored else observed.get('router_source')
    if not stored and not source: raise RuntimeError('router migration lacks a verified legacy source')
    return {'router_state':contract,'router_source':source,'router_configuration':config},values


def verify(before, after, state, *, unfinished=True):
    config=state['requested_configuration']; contract=config['router_state']
    if (after['identity']!=before['identity'] or after['accounts']!=before['accounts'] or
            after['router_accounts']!=contract['accounts'] or after['retained_uuid']!=contract['filesystem_uuid'] or
            after['persistent_storage']!=dict(contract,mounted=True) or after['xen_uuid']!=state['new_uuid']):
        raise RuntimeError('router identity, service ownership or retained storage changed')
    expected=config['image_evidence']
    if (after['kernel']!=expected['kernel_release'] or after['packages_sha256']!=expected['packages_sha256'] or
            any(after[k]!=expected[k] for k in ('tailscale_version','tailscale_sha256','tailscaled_sha256'))):
        raise RuntimeError('router kernel, packages or upstream Tailscale differ from the qualified image')
    if unfinished:
        files=dict(config['router_configuration']['files'])
        files['etc/dnsmasq.conf']=re.sub(r'^dhcp-leasefile=.*$','dhcp-leasefile=/srv/retained/dnsmasq/dnsmasq.leases',files['etc/dnsmasq.conf'],flags=re.M)
        expected_files={k:hashlib.sha256(v.encode()).hexdigest() for k,v in files.items()
                        if k.endswith('.nft') or k in ('etc/network/interfaces','etc/dhcpcd.conf','etc/dnsmasq.conf')}
        if after['configuration_files']!=expected_files:
            raise RuntimeError('router configuration differs from frozen Instance policy')
    if not all(after[k] for k in ('kernel_modules','online','firewall','forwarding','wan','service')):
        raise RuntimeError('router service or network health failed')
    # Isolation and DHCP/DNS also require the frozen Ansible verification role
    # before finish() submits these checks to the protected transaction.
    return dict.fromkeys(('boot','kernel_modules','identity','retained_state','firewall',
                          'forwarding','isolation','dhcp_dns','wan','management','reboot'),True)


def verify_network(box, work, values):
    with tempfile.TemporaryDirectory(prefix='router-verify-') as temporary:
        path=Path(temporary)/'variables.json';inputs.save(path,dict(values,router_update_box=box))
        subprocess.run(['ansible-playbook','-i',str(work/'inventory/hosts.json'),
            str(work/'public/ansible/playbooks/74-router-service-verify.yml'),'--limit',box+'-router','-e','@'+str(path)],
            cwd=work/'public',env=dict(os.environ,ANSIBLE_CONFIG=str(work/'public/ansible/ansible.cfg')),check=True,timeout=600)
