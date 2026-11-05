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
import urllib.request

import air_maintenance_cycle as cycle
import air_maintenance
from platform_maintenance import write


def version(value):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',value):raise RuntimeError('unsupported Codex version: '+value)
    return tuple(map(int,value.split('.')))


def require_current(box,selected):
    import codex_artifact
    request=urllib.request.Request(codex_artifact.METADATA,headers={'User-Agent':'Klokast-image-update'})
    with urllib.request.urlopen(request,timeout=30) as response:
        release=json.loads(response.read(4*1024*1024))
    latest=codex_artifact.selection(release)['version']
    if version(selected)<version(latest):raise RuntimeError('a newer Codex release is available; prepare a fresh AIR image')
    script="""set -eu
python3 - <<'PYREMOTE'
import json,pathlib,subprocess
paths=[pathlib.Path('/usr/local/bin/codex'),pathlib.Path('/home/agent/.local/bin/codex'),pathlib.Path('/home/agent/.codex/packages/app-server-daemon/current/bin/codex')]
versions=[]
for p in paths:
 if p.exists():versions.append(subprocess.check_output([str(p),'--version'],text=True,timeout=15).strip().split()[-1])
print(json.dumps(versions))
PYREMOTE
"""
    result=subprocess.run(['tailscale','ssh','agent@'+box+'-air','sh','-s'],input=script,capture_output=True,text=True,timeout=90,check=True)
    if any(version(v)>version(selected) for v in json.loads(result.stdout)):
        raise RuntimeError('selected image would downgrade Codex; prepare a newer image')


def status(box,operation=None):
    cycle.source.require_controller()
    paths=[cycle.ROOT/operation/'image-job.json'] if operation else list(cycle.ROOT.glob('*/image-job.json'))
    records=[json.loads(p.read_text()) for p in paths if p.exists()]
    records=[r for r in records if r['box']==box]
    return max(records,key=lambda r:r['created_at']) if records else {'state':'absent','box':box}


def start(box,operation=None):
    cycle.source.require_controller()
    if box+'-air' not in cycle.source.snapshot()['instance']['airunners']:raise RuntimeError('AIR target is not declared')
    operation=operation or secrets.token_hex(12)
    if not re.fullmatch('[0-9a-f]{24}',operation):raise RuntimeError('invalid AIR operation')
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
        record=dict(operation=operation,box=box,state='waiting',created_at=time.time())
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
    with (path.parent/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            while True:
                cycle.source.require_controller()
                maintenance=path.parent/'update-air.json'
                saved=json.loads(maintenance.read_text()) if maintenance.exists() else {}
                if saved.get('vm_operation') or air_maintenance.remote(box,'image-idle')['idle']:break
                time.sleep(30)
            with cycle.runtime.vm_update_installation_lock():
                if not cycle.service.step(box,'air','status').get('installed'):
                    raise RuntimeError('AIR requires explicit retained-data migration before automatic updates')
            record['state']='updating';write(path,record)
            result=cycle.step('update',box,operation,'air')
            if result['state']!='complete':raise RuntimeError('AIR became busy; resume after work finishes')
            record.update(state='complete',result=result);write(path,record)
        except BaseException as error:
            record.update(state='failed',error=str(error));write(path,record);raise
