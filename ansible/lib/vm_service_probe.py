"""Bounded service VM identity, workload and boot checks from the controller."""
import json
from pathlib import Path
import re
import subprocess

ROOT = r'''
import hashlib, json, os, pathlib, pwd, subprocess
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
role = 'vpn-egress' if os.uname().nodename.endswith('-vpn-egress') else os.uname().nodename.rsplit('-', 1)[1]
tail = json.loads(run(['tailscale', 'status', '--json']))['Self']
files = {n:sha('/'+n) for n in ('etc/hostname','etc/passwd','etc/group','etc/shadow','etc/fstab','etc/nftables.nft','etc/network/interfaces')}
identity = {'hostname':os.uname().nodename, 'tailscale_id':tail['ID'], 'tailscale_key':tail['PublicKey'],
            'tailscale_hostname':tail['HostName'], 'tailscale_tags':sorted(tail.get('Tags',[])),
            'ssh_keys':{p.name:sha(str(p)) for p in sorted(P('/etc/ssh').glob('ssh_host_*_key.pub'))},
            'network':files['etc/network/interfaces'], 'firewall':files['etc/nftables.nft']}
run(['nft','-c','-f','/etc/nftables.nft'])
accounts = {n:[pwd.getpwnam(n).pw_uid,pwd.getpwnam(n).pw_gid] for n in (('neo','vpn-egress') if role == 'vpn-egress' else ('neo',))}
for name in accounts:
    if pwd.getpwnam(name).pw_shell not in ('/bin/ash','/bin/sh','/sbin/nologin'):
        raise RuntimeError('service login shell requires a compatible image before replacement: ' + name)
root = next(line.split()[0] for line in P('/proc/mounts').read_text().splitlines() if line.split()[1] == '/')
if root not in ('/dev/xvda','/dev/xvda3'): raise RuntimeError('unsupported service root layout: ' + root)
mounts = [line.split() for line in P('/proc/mounts').read_text().splitlines() if line.split()[1] == '/srv/retained']
retained = run(['blkid','-s','UUID','-o','value',mounts[0][0]]).strip() if mounts else None
if mounts and (len(mounts) != 1 or mounts[0][2] != 'ext4'): raise RuntimeError('unsupported retained data mount')
marker = P('/etc/klokast-service-state.json')
value = {'identity':identity,'kernel':os.uname().release,'packages_sha256':packages(),'boot_id':P('/proc/sys/kernel/random/boot_id').read_text().strip(),
         'xen_uuid':P('/sys/hypervisor/uuid').read_text().strip(),'root_partition':'3' if root.endswith('3') else '',
         'source_files':files,'accounts':accounts,'retained_uuid':retained,
         'kernel_modules':P('/lib/modules/'+os.uname().release).is_dir(),'online':tail['Online'],
         'firewall':bool(run(['nft','list','ruleset']).strip()),
         'copy':json.loads(marker.read_text()) if marker.exists() else None,
         'service':run(['rc-service','vpn-egress','status']).strip() if role == 'vpn-egress' else 'podman'}
# Unknown persistent mounts must never be silently omitted from a disk copy.
for line in P('/proc/mounts').read_text().splitlines():
    parts=line.split()
    if parts[0].startswith('/dev/') and parts[1] not in ('/','/boot','/srv/retained'):
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


def probe(box, role):
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', box) or role not in ('bak','dmz','iot','vpn-egress'):
        raise RuntimeError('service probe requires a declared fixed VM name')
    result = None
    for privileged, code in ([(True, ROOT), (False, USER)] if role != 'vpn-egress' else [(True, ROOT)]):
        script = 'set -eu\n' + ('doas ' if privileged else '') + "python3 - <<'SERVICE_PROBE'\n" + code + '\nSERVICE_PROBE\n'
        p = subprocess.run(['tailscale','ssh','neo@'+box+'-'+role,'sh','-s'],input=script,text=True,capture_output=True,timeout=180)
        if p.returncode or len(p.stdout)>1024*1024:
            raise RuntimeError('service probe failed for '+box+'-'+role+': '+p.stderr[-2000:])
        data=json.loads(p.stdout)
        if privileged: result=data
        else: result['workloads']=data
    result.setdefault('workloads', {})
    if (result['identity']['hostname'] != box+'-'+role or result['identity']['tailscale_hostname'] != box+'-'+role) or not all(result[k] for k in ('kernel_modules','online','firewall','service')):
        raise RuntimeError('service health check failed')
    return result


def verify(before, after, state, *, unfinished=True):
    config = state['requested_configuration']
    identity = lambda value: {k:v for k,v in value['identity'].items() if unfinished or k not in ('network','firewall','tailscale_key')}
    if (identity(after) != identity(before) or after['accounts'] != before['accounts'] or
            (unfinished and after['workloads'] != before['workloads']) or after['retained_uuid'] != before['retained_uuid'] or
            after['xen_uuid'] != state['new_uuid']):
        raise RuntimeError('service replacement changed identity, numeric accounts, retained storage or workload records')
    if after['copy'] != {'kind':'klokast.vm-service-state.v1','box':state['box'],'role':state['role'],
                         'copy_verified':True,'source_files':config['source_files']}:
        raise RuntimeError('service retained-state copy evidence differs')
    expected = config['image_evidence']
    if after['kernel'] != expected['kernel_release'] or after['packages_sha256'] != expected['packages_sha256']:
        raise RuntimeError('service kernel or installed packages differ from its qualified image')
    if not all(after[k] for k in ('kernel_modules','online','firewall','service')):
        raise RuntimeError('replacement service is unhealthy')
    return {k:True for k in ('boot','kernel_modules','identity','firewall','retained_state','workloads_unchanged','service','reboot')}
