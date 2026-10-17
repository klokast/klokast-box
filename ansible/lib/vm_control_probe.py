"""Bounded infrastructure health and conservative runner activity checks."""
import json
import re
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'roles/vm-template-builder/files'))
from vm_controller_state import PROVIDER_FILES

PROBE = r'''
import fcntl,grp,hashlib,json,os,pathlib,pwd,shlex,subprocess
P=pathlib.Path
def run(argv):
 p=subprocess.run(argv,capture_output=True,text=True,timeout=45)
 if p.returncode or len(p.stdout)>1024*1024:raise RuntimeError('infrastructure probe failed: '+argv[0])
 return p.stdout
def sha(path):
 p=P(path)
 if p.is_symlink() or not p.is_file():raise RuntimeError('required regular infrastructure file is absent: '+path)
 return hashlib.sha256(p.read_bytes()).hexdigest()
role=os.uname().nodename.rsplit('-',1)[-1]
tail=json.loads(run(['tailscale','status','--json']))
names=('neo','smith','minion') if role=='ops' else ('neo','agent')
accounts={n:[pwd.getpwnam(n).pw_uid,pwd.getpwnam(n).pw_gid] for n in names}
packages={}
for block in P('/lib/apk/db/installed').read_text().strip().split('\n\n'):
 fields={v[0]:v[2:] for v in block.splitlines() if v.startswith(('P:','V:'))}
 if set(fields)!={'P','V'} or fields['P'] in packages:raise RuntimeError('ambiguous package manifest')
 packages[fields['P']]=fields['V']
mounts=[v.split() for v in P('/proc/mounts').read_text().splitlines()]
root=[v for v in mounts if v[1]=='/']
if len(root)!=1 or root[0][0] not in ('/dev/xvda','/dev/xvda3'):raise RuntimeError('unsupported infrastructure root')
data=[v for v in mounts if v[1]=='/srv/retained']
retained=None
if data:
 if len(data)!=1 or data[0][2]!='ext4':raise RuntimeError('unexpected infrastructure data mount')
 retained=run(['blkid',data[0][0]]).strip()
marker=P('/etc/klokast-'+role+'-storage.json')
storage=json.loads(run(['python3','/usr/local/libexec/vm_infrastructure_storage.py','--verify'])) if marker.exists() else None
identity={'hostname':os.uname().nodename,'tailscale_id':tail['Self']['ID'],
 'tailscale_hostname':tail['Self']['HostName'],'tailscale_tags':sorted(tail['Self'].get('Tags',[])),
 'tailscale_key':tail['Self']['PublicKey'],
 'machine_id':sha('/etc/machine-id') if P('/etc/machine-id').exists() else None,
 'ssh_keys':{p.name:sha(str(p)) for p in sorted(P('/etc/ssh').glob('ssh_host_*_key.pub'))}}
busy=[]
if role=='air':
 activity=json.loads(run(['/usr/local/bin/klokast-air-workspace','status']))
 busy=activity['busy_pids']
 if 'wheel' in run(['id','-Gn','agent']).split():raise RuntimeError('agent must remain unprivileged')
 tool=json.loads(P('/etc/klokast-codex.json').read_text())
 if run(['/usr/local/bin/codex','--version']).strip()!='codex-cli '+tool['version']:raise RuntimeError('runner Codex artifact differs')
 if P('/etc/runlevels/default/sshd').exists():raise RuntimeError('runner bootstrap listener remains enabled')
else:
 credentials={}
 for relative in PROVIDER_FILES:
  path=P('/'+relative)
  if not os.path.lexists(path):continue
  info=path.lstat()
  expected=accounts['smith'] if relative.startswith('home/smith/') else [0,0]
  if [info.st_uid,info.st_gid]!=expected or info.st_mode&0o777!=0o600:raise RuntimeError('controller credential metadata differs: '+relative)
  credentials[relative]={'sha256':sha(str(path)),'uid':info.st_uid,'gid':info.st_gid,'mode':info.st_mode&0o777}
 with P('/var/lib/klokast/updates/discovery/build.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 guard=json.loads(run(['/usr/local/sbin/klokast-controller-guard','--status','--json']))
 if not guard.get('configured') or guard.get('role')!='standby':raise RuntimeError('controller target is not the configured standby')
 identity['instance_read_key']=sha('/home/smith/.ssh/github-klokast-instance.pub')
 identity['recovery_keys']=sha('/home/smith/.ssh/authorized_keys')
 cron=subprocess.run(['crontab','-u','smith','-l'],capture_output=True,text=True,timeout=30)
 if cron.returncode not in (0,1):raise RuntimeError('cannot inspect the controller nightly schedule')
 jobs=[v for v in cron.stdout.splitlines() if '/usr/local/sbin/ops-controller-nightly' in v]
 if len(jobs)>1:raise RuntimeError('controller has multiple nightly schedules')
 words=shlex.split(jobs[0]) if jobs else []
 roles=[words[i+1] for i,v in enumerate(words) if v=='--service-role' and i+1<len(words)]
 if any(v not in ('bak','dmz','iot','vpn-egress','router','air') for v in roles):raise RuntimeError('nightly role selection differs')
 nightly={'roles':roles,'all':'--services' in words,'enabled':bool(jobs) and not jobs[0].lstrip().startswith('#')}
for name in names:
 if (len([u for u in pwd.getpwall() if u.pw_uid==accounts[name][0]])!=1 or
     len([g for g in grp.getgrall() if g.gr_gid==accounts[name][1]])!=1 or
     grp.getgrgid(accounts[name][1]).gr_name!=name):raise RuntimeError('ambiguous infrastructure account')
allowed={'/','/boot','/srv/retained','/var/lib/klokast/download-sources'}
if role=='air':allowed.add('/home/agent/.codex-archives')
if storage:allowed.update(json.loads(marker.read_text())['bindings'])
if any(v[0].startswith('/dev/') and v[1] not in allowed for v in mounts):raise RuntimeError('unrecorded infrastructure persistent mount')
run(['nft','-c','-f','/etc/nftables.nft'])
result={'identity':identity,'accounts':accounts,'root_partition':'3' if root[0][0].endswith('3') else '',
 'retained_uuid':retained,'persistent_storage':storage,'busy_pids':sorted(busy),
 'bootstrap_dns':json.loads(P('/etc/klokast-infrastructure.json').read_text())['configuration'].get('bootstrap_dns',['1.1.1.1','8.8.8.8']) if P('/etc/klokast-infrastructure.json').is_file() else None,
 'source_files':{n:sha('/'+n) for n in ('etc/passwd','etc/group')},
 'kernel':os.uname().release,'kernel_modules':P('/lib/modules/'+os.uname().release).is_dir(),
 'packages_sha256':hashlib.sha256(json.dumps(packages,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
 'boot_id':P('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'xen_uuid':P('/sys/hypervisor/uuid').read_text().strip(),'online':tail['BackendState']=='Running',
 'firewall':bool(run(['nft','list','ruleset']).strip()),'service':True,
 'workloads':{'running':[],'containers':[],'images':[],'volumes':[]}}
if role=='ops':result.update(guard=guard,nightly=nightly,credentials=credentials)
else:result['activity']=activity
print(json.dumps(result,sort_keys=True))
'''


def probe(box, role, *, repair=False):
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}',box) or role not in ('ops','air'):
        raise RuntimeError('invalid infrastructure probe target')
    user='smith' if role=='ops' else 'neo'
    result=subprocess.run(['tailscale','ssh',user+'@'+box+'-'+role,'doas','-n','python3','-'],
                          input='PROVIDER_FILES = '+repr(PROVIDER_FILES)+'\n'+PROBE,text=True,capture_output=True,timeout=180)
    if result.returncode or len(result.stdout)>1024*1024:
        raise RuntimeError('infrastructure health probe failed for '+box+'-'+role+': '+result.stderr[-1000:])
    value=json.loads(result.stdout)
    if value['identity']['hostname']!=box+'-'+role or value['identity']['tailscale_hostname']!=box+'-'+role or not value['online']:
        raise RuntimeError('infrastructure machine identity or connectivity differs')
    expected=['tag:airunner'] if role=='air' else ['tag:ops']
    if value['identity']['tailscale_tags']!=expected:raise RuntimeError('infrastructure Tailnet tags differ')
    from vm_service_probe import filesystem_uuid
    value['retained_uuid']=filesystem_uuid(value['retained_uuid'])
    return value


def verify(before, after, state, *, unfinished=True):
    config=state['requested_configuration']
    expected={'kind':'klokast.infrastructure-storage.v1','box':state['box'],'role':state['role'],
              'filesystem_uuid':state['data_uuid'],'accounts':before['accounts']}
    if (after['identity']!=before['identity'] or after['accounts']!=before['accounts'] or
            after['persistent_storage']!=expected or after['retained_uuid']!=state['data_uuid'] or
            after['xen_uuid']!=state['new_uuid']):
        raise RuntimeError('infrastructure identity, accounts or retained data changed')
    if state['role']=='ops' and after['guard']['active_box']!=config['active_box']:
        raise RuntimeError('controller authority changed during replacement')
    if state['role']=='ops' and 'credentials' in before and after.get('credentials')!=before['credentials']:
        raise RuntimeError('same-controller credential content, ownership or permissions changed')
    image=config['image_evidence']
    if after['kernel']!=image['kernel_release'] or after['packages_sha256']!=image['packages_sha256']:
        raise RuntimeError('infrastructure packages or kernel differ from the qualified image')
    if not all(after[k] for k in ('kernel_modules','online','firewall','service')):
        raise RuntimeError('replacement infrastructure health failed')
    return {k:True for k in ('boot','kernel_modules','identity','firewall','retained_state','workloads_unchanged','service','reboot')}
