"""Resumable planned controller switching; credentials never leave their owner."""
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import shlex
import socket
import subprocess
import sys
import time

from platform_maintenance import write

ROOT = Path('/home/smith/private/klokast/controller-switch')
INSTANCE = Path('/home/smith/private/klokast/instance')
LOCK_SCRIPT = r'''
import fcntl, json, os, pathlib, select, stat, sys, time
locks=[]
deadline=time.monotonic()+300
for name in ('/var/lib/klokast/updates/operation.lock', '/var/lib/klokast/updates/discovery/build.lock'):
    fd=os.open(name,os.O_RDWR|os.O_NOFOLLOW)
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o007:
        raise SystemExit('unsafe controller switch lock')
    while True:
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic()>deadline: raise SystemExit('controller operations are still busy; retry the switch')
            time.sleep(1)
    locks.append(fd)
print('locked',flush=True)
# EOF from the coordinator or the bound releases every lock. No daemon remains.
select.select([sys.stdin],[],[],1800)
'''


@contextlib.contextmanager
def locks(config, ha):
    children=[]
    try:
        for box in sorted(config['controllers']):
            argv = [sys.executable, '-u', '-c', LOCK_SCRIPT] if ha.is_local_controller(config,box) else [
                'tailscale','ssh',ha.target(config,box),'python3','-u','-c',shlex.quote(LOCK_SCRIPT)]
            child=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            children.append(child)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout,selectors.EVENT_READ)
                if not selector.select(310) or child.stdout.readline().strip()!='locked':
                    raise RuntimeError('could not acquire controller locks on '+box+'; no authority was changed')
        yield
        if any(child.poll() is not None for child in children):
            raise RuntimeError('controller lock connection ended; inspect the recorded transition before continuing')
    finally:
        for child in reversed(children):
            child.stdin.close()
            try: child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=15)
            child.stdout.close(); child.stderr.close()


def git(*args):
    return subprocess.check_output(['git','-C',str(INSTANCE),*args],text=True,stderr=subprocess.PIPE,timeout=90).strip()


def publish(record, path):
    """Replay an interrupted single-file commit/push without overwriting edits."""
    current=json.loads((INSTANCE/'klokast-instance.json').read_text())
    original=record['instance']
    expected=dict(original,controllers={'active':record['new'],'standby':record['old']})
    if current not in (original,expected):
        raise RuntimeError('Instance changed during the switch; preserve the edits and reconcile before resume')
    changed=git('status','--porcelain')
    if changed and changed not in ('M klokast-instance.json',' M klokast-instance.json'):
        raise RuntimeError('Instance has unrelated or staged edits; switch cannot publish them')
    if current==original:
        if changed: raise RuntimeError('Instance has unrelated edits')
        git('pull','--ff-only')
        if git('rev-parse','HEAD')!=record['original_commit']:
            raise RuntimeError('Instance upstream advanced; start a new switch after review')
        (INSTANCE/'klokast-instance.json').write_text(json.dumps(expected,indent=2,ensure_ascii=False)+'\n')
    subprocess.run(['/usr/local/bin/klokast','check','--instance',str(INSTANCE)],check=True,timeout=60)
    if git('diff','HEAD','--','klokast-instance.json'):
        git('add','--','klokast-instance.json')
        git('commit','-m','Switch controller authority '+record['operation'])
    revision=git('rev-parse','HEAD')
    # No unrelated commit may be swept into an interrupted switch push.
    if git('diff','--name-only',record['original_commit'],'HEAD')!='klokast-instance.json':
        raise RuntimeError('switch commit changed unexpected files')
    record['published_commit']=revision
    write(path,record)
    git('push','origin','HEAD:main')
    if git('rev-parse','@{upstream}')!=revision:
        raise RuntimeError('Instance push did not synchronize upstream')


def credential_identity(config,box,ha):
    script = r'''
set -eu
doas python3 - <<'PYCODE'
import hashlib,json,pathlib,re
result={}
for name in ('tailscale-policy.env','tailscale-devices.env'):
    content=(pathlib.Path('/etc/klokast')/name).read_text()
    match=re.findall(r'^TS_OAUTH_CLIENT_ID=["\x27]?([^"\x27\s]+)',content,re.M)
    if len(match)!=1:raise SystemExit('ambiguous provider client identity')
    result[name]=hashlib.sha256(match[0].encode()).hexdigest()
print(json.dumps(result))
PYCODE
test -s /home/smith/.ssh/github-klokast-instance-write
git -C /home/smith/private/klokast/instance -c core.sshCommand='ssh -i /home/smith/.ssh/github-klokast-instance-write -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -p 443 -o Hostname=ssh.github.com -o HostKeyAlias=github.com' push --dry-run origin HEAD:main >/dev/null
ssh-keygen -y -f /home/smith/.ssh/github-klokast-instance-write | sha256sum | cut -d ' ' -f 1
'''
    result=ha.controller_sh(config,box,script,capture=True,timeout=120)
    lines=result.stdout.splitlines()
    if len(lines)!=2 or not re.fullmatch('[0-9a-f]{64}',lines[1]):
        raise RuntimeError('controller credential readiness returned invalid evidence')
    return dict(json.loads(lines[0]),instance_write=lines[1])


def states(config,record,ha):
    old=ha.controller_status(config,record['old'])
    new=ha.controller_status(config,record['new'])
    if not old.get('reachable') or not new.get('reachable'):
        raise RuntimeError('both controllers must be reachable; no automatic promotion')
    if old.get('active') and new.get('active'):
        raise RuntimeError('conflicting controller authority; use explicit recovery')
    return old,new


def transition(config,record,path,ha):
    old,new=states(config,record,ha)
    initial={'active':record['old'],'standby':record['new']}
    final={'active':record['new'],'standby':record['old']}
    if record['phase']=='prepared':
        if not old.get('active') or new.get('role')!='standby' or new.get('active_box')!=record['old']:
            raise RuntimeError('initial controller authority changed')
        for box in (record['old'],record['new']):
            ha.require_readiness(config,box,record['old'],placement=initial)
            ha.verify_credentials(config,box,active=box==record['old'])
        identities=[credential_identity(config,b,ha) for b in (record['old'],record['new'])]
        if any(identities[0][k]==identities[1][k] for k in identities[0]):
            raise RuntimeError('controllers must have independent provider and Instance write credentials')
        record['phase']='publishing'; write(path,record)
    if record['phase']=='publishing':
        # Handles loss of the push reply without creating another commit.
        publish(record,path)
        record['phase']='published'; write(path,record)
    if record['phase']=='published':
        ha.controller_sh(config,record['new'],
            'set -eu\ncd /home/smith/private/klokast/instance\ntest -z "$(git status --porcelain)"\ngit pull --ff-only\n',timeout=120)
        ha.require_readiness(config,record['new'],record['old'],placement=final)
        record['phase']='synchronized'; write(path,record)
    if record['phase']=='synchronized':
        old,new=states(config,record,ha)
        if new.get('active'):
            raise RuntimeError('destination became active before recorded demotion')
        if old.get('active'):
            ha.set_marker(config,record['old'],'standby',record['new'])
        old,new=states(config,record,ha)
        if old.get('active') or old.get('role')!='standby' or old.get('active_box')!=record['new']:
            raise RuntimeError('old controller demotion is unverified')
        blocked=ha.controller_sh(config,record['old'],
            'exec /usr/local/sbin/klokast-controller-guard --require-active\n',capture=True,check=False,timeout=30)
        if blocked.returncode!=78:
            raise RuntimeError('old controller can still start Platform-wide operations')
        record['phase']='demoted'; write(path,record)
    if record['phase']=='demoted':
        old,new=states(config,record,ha)
        if old.get('active') or old.get('active_box')!=record['new']:
            raise RuntimeError('old controller authority changed before activation')
        if not new.get('active'):
            if new.get('role')!='standby':raise RuntimeError('destination is not the expected standby')
            ha.set_marker(config,record['new'],'active',record['new'])
        record['phase']='activated'; write(path,record)
    if record['phase']=='activated':
        old,new=states(config,record,ha)
        if old.get('active') or not new.get('active') or new.get('active_box')!=record['new']:
            raise RuntimeError('final controller authority differs')
        ha.verify_promotion(config,record['new'])
        record['phase']='complete'; write(path,record)
    if record['phase']!='complete':raise RuntimeError('unknown controller switch phase')
    old,new=states(config,record,ha)
    if old.get('active') or old.get('active_box')!=record['new'] or not new.get('active') or new.get('active_box')!=record['new']:
        raise RuntimeError('completed switch no longer matches controller authority')
    return record


def run(config,args,ha):
    if args.old_active==args.new_active:raise RuntimeError('controller switch needs two different boxes')
    if args.resume and not re.fullmatch('[0-9a-f]{24}',args.resume):raise RuntimeError('invalid switch operation ID')
    if args.dry_run_plan:
        print('Verify independent credentials and repositories; lock both controllers in box order; publish Instance placement; demote and verify old; activate and verify new.')
        return
    if not ha.is_local_controller(config,args.old_active):
        arguments=['ansible/bin/ops-controller-ha','switchover','--old-active',args.old_active,'--new-active',args.new_active]
        if args.resume:arguments+=['--resume',args.resume]
        ha.controller_sh(config,args.old_active,'set -eu\ncd /home/smith/src/klokast/klokast-box\nexec "$@"\n',arguments,timeout=1800)
        return
    ha.require_development(config,args.old_active)
    if __import__('getpass').getuser()!='smith':raise RuntimeError('run the planned switch as smith')
    with locks(config,ha):
        if args.resume:
            if not re.fullmatch('[0-9a-f]{24}',args.resume):raise RuntimeError('invalid switch operation ID')
            path=ROOT/(args.resume+'.json');record=json.loads(path.read_text())
            if record['old']!=args.old_active or record['new']!=args.new_active or record['operation']!=args.resume:
                raise RuntimeError('switch resume selects a different controller pair')
        else:
            for previous in ROOT.glob('*.json'):
                value=json.loads(previous.read_text())
                if value['phase']!='complete':
                    raise RuntimeError('unfinished controller switch; resume '+value['operation'])
            if git('status','--porcelain'):raise RuntimeError('Instance must be clean before switching')
            record={'schema_version':1,'operation':secrets.token_hex(12),'old':args.old_active,'new':args.new_active,
                    'phase':'prepared','original_commit':git('rev-parse','HEAD'),
                    'instance':json.loads((INSTANCE/'klokast-instance.json').read_text())}
            path=ROOT/(record['operation']+'.json');write(path,record)
        print('Controller switch '+record['operation']+'; resume on '+args.old_active+'-ops with --resume '+record['operation'],flush=True)
        transition(config,record,path,ha)
        print(json.dumps({'operation':record['operation'],'state':'complete','active':record['new']}))
