"""Bounded service VM identity, workload and boot checks from the controller."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = r'''
import grp, hashlib, json, os, pathlib, pwd, signal, socket, subprocess
P = pathlib.Path
def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if p.returncode or len(p.stdout) > 1024 * 1024: raise RuntimeError('guest probe failed: ' + argv[0])
    return p.stdout
def sha(path):
    p = P(path)
    if p.is_symlink() or not p.is_file(): raise RuntimeError('required regular guest file is absent: ' + path)
    return hashlib.sha256(p.read_bytes()).hexdigest()
def packages():
    values = {}
    for block in P('/lib/apk/db/installed').read_text().strip().split('\n\n'):
        fields = {line[0]:line[2:] for line in block.splitlines() if line.startswith(('P:', 'V:'))}
        if set(fields) != {'P','V'} or fields['P'] in values: raise RuntimeError('ambiguous installed package manifest')
        values[fields['P']] = fields['V']
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()
repair = globals().get('REPAIR', False)
role = 'vpn-egress' if os.uname().nodename.endswith('-vpn-egress') else os.uname().nodename.rsplit('-', 1)[1]
status = json.loads(run(['tailscale', 'status', '--json']))
tail = status['Self']
if not repair:
    if any('dns' in message.lower() for message in status.get('Health', [])):
        raise RuntimeError('Tailscale DNS health failed; converge managed DNS before replacement or resume')
    run(['resolvconf', '-l'])
    signal.alarm(30)
    try:
        if not socket.getaddrinfo('controlplane.tailscale.com', 443): raise RuntimeError('service DNS resolution failed')
        if json.loads(run(['tailscale', 'debug', 'prefs']))['CorpDNS']:
            if not socket.getaddrinfo(tail['DNSName'], 22): raise RuntimeError('service Tailnet DNS resolution failed')
    finally:
        signal.alarm(0)
files = {n:sha('/'+n) for n in ('etc/hostname','etc/passwd','etc/group','etc/shadow','etc/fstab','etc/nftables.nft','etc/network/interfaces','etc/network/if-up.d/10-klokast-dns','etc/klokast/dns-filter.nft') if not repair or n in ('etc/passwd','etc/group') or (P('/'+n).is_file() and not P('/'+n).is_symlink())}
identity = {'hostname':os.uname().nodename, 'tailscale_id':tail['ID'], 'tailscale_key':tail['PublicKey'],
            'tailscale_hostname':tail['HostName'], 'tailscale_tags':sorted(tail.get('Tags',[])),
            'ssh_keys':{p.name:sha(str(p)) for p in sorted(P('/etc/ssh').glob('ssh_host_*_key.pub'))},
            'network':files.get('etc/network/interfaces'), 'firewall':files.get('etc/nftables.nft'),
            'machine_id':sha('/etc/machine-id') if P('/etc/machine-id').exists() else None}
if not repair: run(['nft','-c','-f','/etc/nftables.nft'])
if role == 'vpn-egress' and (not repair or P('/etc/vpn-egress/config.yml').is_file()): files['etc/vpn-egress/config.yml'] = sha('/etc/vpn-egress/config.yml')
accounts = {n:[pwd.getpwnam(n).pw_uid,pwd.getpwnam(n).pw_gid] for n in (('neo','vpn-egress') if role == 'vpn-egress' else ('neo',))}
for name, (uid, gid) in accounts.items():
    if len([u for u in pwd.getpwall() if u.pw_uid == uid]) != 1 or len([g for g in grp.getgrall() if g.gr_gid == gid]) != 1 or grp.getgrgid(gid).gr_name != name:
        raise RuntimeError('service numeric ownership is conflicting: '+name)
if role == 'vpn-egress':
    sha('/var/lib/tailscale/tailscaled.state')
    sha('/etc/ssh/ssh_host_ed25519_key')
for name in accounts:
    if not repair and pwd.getpwnam(name).pw_shell not in ('/bin/ash','/bin/sh','/sbin/nologin'):
        raise RuntimeError('service login shell requires a compatible image before replacement: ' + name)
root = next(line.split()[0] for line in P('/proc/mounts').read_text().splitlines() if line.split()[1] == '/')
if root not in ('/dev/xvda','/dev/xvda3'): raise RuntimeError('unsupported service root layout: ' + root)
mounts = [line.split() for line in P('/proc/mounts').read_text().splitlines() if line.split()[1] == '/srv/retained']
retained = run(['blkid','-s','UUID','-o','value',mounts[0][0]]).strip() if mounts else None
if mounts and (len(mounts) != 1 or mounts[0][2] != 'ext4'): raise RuntimeError('unsupported retained data mount')
marker = P('/etc/klokast-service-state.json')
value = {'identity':identity,'kernel':os.uname().release,'packages_sha256':None if repair else packages(),'boot_id':P('/proc/sys/kernel/random/boot_id').read_text().strip(),
         'xen_uuid':P('/sys/hypervisor/uuid').read_text().strip(),'root_partition':'3' if root.endswith('3') else '',
         'source_files':files,'accounts':accounts,'retained_uuid':retained,
         'kernel_modules':P('/lib/modules/'+os.uname().release).is_dir(),'online':tail['Online'],
         'firewall':bool(run(['nft','list','ruleset']).strip()),
         'copy':json.loads(marker.read_text()) if marker.exists() and not repair else None,
         'service':('repair-pending' if repair else run(['rc-service','vpn-egress','status']).strip()) if role == 'vpn-egress' else 'podman'}
if role == 'vpn-egress':
    service_group = grp.getgrnam('vpn-egress').gr_gid
    user = pwd.getpwnam('vpn-egress')
    if user.pw_uid == 0 or service_group == 0 or user.pw_gid != service_group:
        raise RuntimeError('VPN numeric account ownership conflicts with policy')
    if not repair and (user.pw_uid == 0 or service_group == 0 or user.pw_gid != service_group or
            set(os.getgrouplist(user.pw_name, user.pw_gid)) != {service_group} or
            user.pw_dir != '/var/lib/vpn-egress' or user.pw_shell != '/sbin/nologin'):
        raise RuntimeError('VPN account group, home or shell differs; configure the gateway before replacement')
    value['service_group'] = service_group
    if not repair:
        import yaml
        cfg=yaml.safe_load(P('/etc/vpn-egress/config.yml').read_text())
        providers=cfg['rule-providers']
        value['routing_profile']={'domain-list-url': next(iter(providers.values()))['url'] if providers else None,
            'bootstrap-dns': cfg['dns']['proxy-server-nameserver'],
            'vpn-dns': [v.removesuffix('#VPN') for v in cfg['dns']['nameserver']]}
if role in ('bak','dmz','iot'):
    storage=P('/etc/klokast-'+role+'-storage.json')
    value['persistent_storage']=json.loads(run(['python3','/usr/local/libexec/vm_dmz_personalize.py','--verify',role])) if storage.exists() else None
    writers=[]
    for process in P('/proc').glob('[0-9]*'):
        if process.name==str(os.getpid()):continue
        for fd in (process/'fd').glob('*'):
            try:
                target=os.readlink(fd)
                if not target.startswith(('/srv/','/home/neo/.local/share/containers/')) or target=='/srv/retained/platform-tailscale-state':continue
                info=(process/'fdinfo'/fd.name).read_text()
                flags=next(int(line.split()[1],8) for line in info.splitlines() if line.startswith('flags:'))
                if flags & 3: writers.append({'pid':process.name,'path':target})
            except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    value['application_writers']=writers
    for name in ('subuid','subgid'):
        rows=[line for line in P('/etc/'+name).read_text().splitlines() if line.startswith('neo:')]
        if rows!=['neo:100000:65536']:raise RuntimeError('shared guest subordinate numeric ownership differs')
# Unknown persistent mounts must never be silently omitted from a disk copy.
for line in P('/proc/mounts').read_text().splitlines():
    parts=line.split()
    allowed=('/', '/boot', '/srv/retained') + (('/home/neo/.local/share/containers',) if value.get('persistent_storage') else ())
    if parts[0].startswith('/dev/') and parts[1] not in allowed:
        raise RuntimeError('unsupported persistent service mount: ' + parts[1])
print(json.dumps(value,sort_keys=True))
'''
USER = r'''
import json, subprocess
def run(argv):
    p=subprocess.run(argv,capture_output=True,text=True,timeout=60)
    if p.returncode or len(p.stdout)>1024*1024: raise RuntimeError('rootless workload probe failed')
    return p.stdout
info=json.loads(run(['podman','info','--format','json']))
if info['host']['security']['rootless'] is not True: raise RuntimeError('Podman is not rootless')
result={'running':sorted(run(['podman','ps','--format','{{.ID}}']).split())}
for key,argv in {'containers':['podman','ps','-a','--format','{{.ID}} {{.Status}}'],
                 'images':['podman','images','--format','{{.Id}}'],
                 'volumes':['podman','volume','ls','--format','{{.Name}}']}.items():
    # Human relative ages change without a workload change; IDs are stable.
    result[key]=sorted(set(line.split()[0] for line in run(argv).splitlines() if line))
print(json.dumps(result,sort_keys=True))
'''


def filesystem_uuid(value):
    if value is None: return None
    pattern = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
    if re.fullmatch(pattern, value): return value
    # BusyBox blkid can print its full record despite -s UUID -o value.
    # Normalize this output before comparing guests with different tool sets.
    matches = re.findall(r'\bUUID="(' + pattern + r')"', value)
    if len(value.splitlines()) != 1 or len(matches) != 1:
        raise RuntimeError('retained filesystem has no unique UUID')
    return matches[0]


def probe(box, role, *, repair=False):
    if role in ("ops", "air"):
        import vm_control_probe
        return vm_control_probe.probe(box, role, repair=repair)
    if role == 'router':
        import vm_router_updates
        return vm_router_updates.probe(box, repair=repair)
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', box) or role not in ('bak','dmz','iot','vpn-egress'):
        raise RuntimeError('service probe requires a declared fixed VM name')
    result = None
    for privileged, code in ([(True, ROOT), (False, USER)] if role != 'vpn-egress' else [(True, ROOT)]):
        code = ('REPAIR = True\n' if repair and privileged else '') + code
        script = 'set -eu\n' + ('doas ' if privileged else '') + "python3 - <<'SERVICE_PROBE'\n" + code + '\nSERVICE_PROBE\n'
        for attempt in range(3):
            p = subprocess.run(['tailscale','ssh','neo@'+box+'-'+role,'sh','-s'],input=script,text=True,capture_output=True,timeout=180)
            if not p.returncode or 'dial failure:' not in p.stderr or attempt == 2:
                break
            print('Service probe SSH dial failed for '+box+'-'+role+'; retry '+str(attempt+2)+'/3',file=sys.stderr,flush=True)
            time.sleep(2)
        if p.returncode or len(p.stdout)>1024*1024:
            raise RuntimeError('service probe failed for '+box+'-'+role+': '+p.stderr[-2000:])
        data=json.loads(p.stdout)
        if privileged: result=data
        else: result['workloads']=data
    result.setdefault('workloads', {})
    result['retained_uuid'] = filesystem_uuid(result['retained_uuid'])
    if (result['identity']['hostname'] != box+'-'+role or result['identity']['tailscale_hostname'] != box+'-'+role) or (not repair and not all(result[k] for k in ('kernel_modules','online','firewall','service'))) or not result['online']:
        raise RuntimeError('service health check failed')
    return result


def verify(before, after, state, *, unfinished=True):
    if state["role"] in ("ops", "air"):
        import vm_control_probe
        return vm_control_probe.verify(before, after, state, unfinished=unfinished)
    if state['role'] == 'router':
        import vm_router_updates
        return vm_router_updates.verify(before, after, state, unfinished=unfinished)
    config = state['requested_configuration']
    shared = state['role'] in ('bak','dmz','iot') and state['role']+'_creation' in config
    clean = 'vpn_creation' in config or shared
    identity = lambda value: {k:v for k,v in value['identity'].items()
        if (k != 'machine_id' or 'machine_id' in before['identity']) and
           ((unfinished and not clean) or k not in ('network','firewall','tailscale_key'))}
    expected_accounts = dict(before['accounts'])
    if state['role'] == 'vpn-egress':
        group = after.get('service_group')
        if type(group) is not int or group <= 0 or after['accounts']['vpn-egress'][1] != group:
            raise RuntimeError('VPN account does not use its named primary group')
        if not unfinished:
            # Accepted revisions are evidence. Explicit convergence can change
            # group configuration; the retained account UID is still invariant.
            expected_accounts['vpn-egress'] = [before['accounts']['vpn-egress'][0], group]
    expected_workloads = ({k:[] for k in ('running','containers','images','volumes')}
                          if shared and config.get('discard_root_data') else before['workloads'])
    retained = state.get('data_uuid') if shared else before['retained_uuid']
    if (identity(after) != identity(before) or after['accounts'] != expected_accounts or
            (unfinished and after['workloads'] != expected_workloads) or filesystem_uuid(after['retained_uuid']) != filesystem_uuid(retained) or
            after['xen_uuid'] != state['new_uuid']):
        raise RuntimeError('service replacement changed identity, numeric accounts, retained storage or workload records')
    if shared:
        if after.get('persistent_storage') != {'kind':'klokast.'+state['role']+'-storage.v1','box':state['box'],'role':state['role'],'filesystem_uuid':retained}:
            raise RuntimeError('shared guest persistent storage evidence differs')
        if after.get('application_writers') or after['workloads']['running']:
            raise RuntimeError('shared guest application writers appeared before acceptance')
    elif after['copy'] != {'kind':'klokast.vm-service-state.v1','box':state['box'],'role':state['role'],
                         'copy_verified':True,'source_files':config['source_files']}:
        raise RuntimeError('service retained-state copy evidence differs')
    if 'routing_profile' in config and after.get('routing_profile') != config['routing_profile']:
        raise RuntimeError('replacement VPN profile differs from its frozen inputs')
    expected = config['image_evidence']
    if after['kernel'] != expected['kernel_release'] or after['packages_sha256'] != expected['packages_sha256']:
        raise RuntimeError('service kernel or installed packages differ from its qualified image')
    if not all(after[k] for k in ('kernel_modules','online','firewall','service')):
        raise RuntimeError('replacement service is unhealthy')
    return {k:True for k in ('boot','kernel_modules','identity','firewall','retained_state','workloads_unchanged','service','reboot')}
