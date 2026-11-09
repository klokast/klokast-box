"""Freshness checks and bounded controller jobs for AIR image replacement."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

import air_maintenance_cycle as cycle
import air_maintenance
import ops_replacement_inputs as inputs
from platform_maintenance import write


def version(value):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',value):raise RuntimeError('unsupported Codex version: '+value)
    return tuple(map(int,value.split('.')))


def require_current(box,selected,claude=None):
    import codex_artifact
    import github_metadata
    import ops_vpn_proxy
    release=ops_vpn_proxy.public_download(lambda:github_metadata.fetch('codex-stable'),'Codex release check')
    latest=codex_artifact.selection(release)['version']
    if version(selected)<version(latest):raise RuntimeError('a newer Codex release is available; prepare a fresh AIR image')
    if claude is not None:
        import claude_artifact
        if claude_artifact.version(claude)<claude_artifact.version(claude_artifact.current_version()):
            raise RuntimeError('a newer Claude Code release is available; prepare a fresh AIR image')
    script="""set -eu
python3 - <<'PYREMOTE'
import json,pathlib,subprocess
def versions(paths,field):
 found=[]
 for p in map(pathlib.Path,paths):
  if p.exists():found.append(subprocess.check_output([str(p),'--version'],text=True,timeout=15).strip().split()[field])
 return found
# Codex prints 'codex-cli VERSION'; Claude Code prints 'VERSION (Claude Code)'.
print(json.dumps({'codex':versions(['/usr/local/bin/codex','/home/agent/.local/bin/codex','/home/agent/.codex/packages/app-server-daemon/current/bin/codex'],-1),
                  'claude':versions(['/usr/local/bin/claude','/usr/bin/claude','/home/agent/.local/bin/claude'],0)}))
PYREMOTE
"""
    result=subprocess.run(['tailscale','ssh','agent@'+box+'-air','sh','-s'],input=script,capture_output=True,text=True,timeout=90,check=True)
    installed=json.loads(result.stdout)
    if any(version(v)>version(selected) for v in installed['codex']):
        raise RuntimeError('selected image would downgrade Codex; prepare a newer image')
    if claude is not None:
        import claude_artifact
        if any(claude_artifact.version(v)>claude_artifact.version(claude) for v in installed['claude']):
            raise RuntimeError('selected image would downgrade Claude Code; prepare a newer image')


def status(box,operation=None):
    cycle.source.require_controller()
    paths=[cycle.ROOT/operation/'image-job.json'] if operation else list(cycle.ROOT.glob('*/image-job.json'))
    records=[(p,json.loads(p.read_text())) for p in paths if p.exists()]
    records=[(p,r) for p,r in records if r['box']==box]
    if not records:return {'state':'absent','box':box}
    path,record=max(records,key=lambda item:item[1]['created_at'])
    progress=path.parent/'progress.json'
    if record['state']=='updating' and progress.exists():record['progress']=json.loads(progress.read_text())['progress']
    return record


def start(box,operation=None):
    cycle.source.require_controller()
    if box+'-air' not in cycle.source.snapshot()['instance']['airunners']:raise RuntimeError('AIR target is not declared')
    operation=operation or secrets.token_hex(12)
    if not re.fullmatch('[0-9a-f]{24}',operation):raise RuntimeError('invalid AIR operation')
    cycle.ROOT.mkdir(parents=True,mode=0o700,exist_ok=True)
    # Serialize selection and launch so repeated requests join the same job.
    with (cycle.ROOT/'launch.lock').open('a') as launch:
        fcntl.flock(launch,fcntl.LOCK_EX)
        requested=status(box,operation)
        if requested['state']=='absent':
            records=[json.loads(p.read_text()) for p in cycle.ROOT.glob('*/image-job.json')]
            active=[r for r in records if r['box']==box and r['state'] in ('waiting','updating')]
            if active:operation=min(active,key=lambda r:r['created_at'])['operation']
        return launch_job(box,operation)


def launch_job(box,operation):
    directory=cycle.ROOT/operation;directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    with (directory/'launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=directory/'image-job.json'
        if path.exists() and json.loads(path.read_text())['box']!=box:raise RuntimeError('operation belongs to another AIR target')
        record=status(box,operation)
        if record['state']=='complete':return record
        with (directory/'worker.lock').open('a') as worker_lock:
            try:fcntl.flock(worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return record
            # A new interpreter loads the updated modules. Joining a live worker
            # must not change the checkout from which it is preparing inputs.
            inputs.update_checkout(cycle.source.REPO)
        record=dict(operation=operation,box=box,state='waiting',reason='checking that '+box+'-air tmux sessions are detached',
                    checks=[],created_at=time.time())
        write(path,record)
        with (directory/'image-job.log').open('ab') as log:
            subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'bin/air-image-update'),
                              'worker','--box',box,'--operation',operation],stdin=subprocess.DEVNULL,
                             stdout=log,stderr=log,start_new_session=True,close_fds=True)
        return record


def worker(box,operation):
    import signal
    signal.signal(signal.SIGALRM,lambda *_: (_ for _ in ()).throw(RuntimeError('AIR job reached its twelve-hour bound; resume operation')))
    signal.alarm(43200)
    path=cycle.ROOT/operation/'image-job.json';record=status(box,operation)
    target=box+'-air';record['checks']=[];record.pop('progress',None)
    with (path.parent/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            while True:
                cycle.source.require_controller()
                maintenance=path.parent/'update-air.json'
                saved=json.loads(maintenance.read_text()) if maintenance.exists() else {}
                if saved.get('vm_operation'):break
                activity=air_maintenance.remote(box,'image-idle')
                if activity['idle']:
                    record['checks']=[target+' tmux sessions are detached: OK','Discussions on '+target+' are not active: OK']
                    break
                attached=activity.get('attached',0);busy=len(activity.get('busy_pids',[]))
                if attached:reason=target+' tmux sessions are attached: waiting'
                elif busy:reason='Discussions or other work on '+target+' are active: waiting'
                else:reason=(activity.get('reason') or target+' is not idle')+': waiting'
                record.update(state='waiting',reason=reason,checks=[target+' tmux sessions are detached: OK'] if not attached else [],
                              attached=attached,busy_processes=busy)
                write(path,record)
                time.sleep(30)
            record.pop('attached',None);record.pop('busy_processes',None)
            # The shared lock gives no holder detail, so report only what is known.
            record.update(state='waiting',reason='checking that no other update work is ongoing');write(path,record)
            with cycle.runtime.vm_update_installation_lock(wait=True):
                record['checks']=record.get('checks',[])+['No other update work is ongoing: OK']
                record.update(reason='checking the installed '+target+' service');write(path,record)
                if not saved.get('vm_operation') and not cycle.service.step(box,'air','status').get('installed'):
                    raise RuntimeError('AIR requires explicit retained-data migration before automatic updates')
            # Update code in this process and its frozen worker publishes named stages here.
            progress=path.parent/'progress.json';progress.unlink(missing_ok=True)
            os.environ['KLOKAST_PROGRESS']=str(progress)
            record.update(state='updating',reason='preparing and verifying the replacement image');write(path,record)
            result=cycle.step('update',box,operation,'air',wait_for_lock=True)
            if result['state']!='complete':raise RuntimeError('AIR became busy; resume after work finishes')
            reason='update verified'+('; tmux workspace '+result['workspace'] if 'workspace' in result else '')
            record.update(state='complete',reason=reason,result=result);write(path,record)
        except BaseException as error:
            record.update(state='failed',error=str(error));write(path,record);raise
