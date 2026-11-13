"""Bounded controller jobs for requested VM image replacement."""
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
from promote_status import running
from vm_lifecycle_roles import INFRASTRUCTURE


def selected(record,box,role):
    # Records written before role selection belong to the runner.
    return record['box']==box and record.get('role','air')==role


def status(box,operation=None,role='air'):
    cycle.source.require_controller()
    paths=[cycle.ROOT/operation/'image-job.json'] if operation else list(cycle.ROOT.glob('*/image-job.json'))
    records=[(p,json.loads(p.read_text())) for p in paths if p.exists()]
    records=[(p,r) for p,r in records if selected(r,box,role)]
    if not records:return {'state':'absent','box':box,'role':role}
    path,record=max(records,key=lambda item:item[1]['created_at'])
    progress=path.parent/'progress.json'
    if record['state']=='updating' and progress.exists():
        stage=json.loads(progress.read_text());record.update(progress=stage['progress'],progress_at=stage.get('updated_at'))
    return record


def save(path,record):
    """Write the job record with the time of each passed check and of the last stage change."""
    now=time.time();old=json.loads(path.read_text()) if path.exists() else {}
    if 'changed_at' not in record or any(record.get(k)!=old.get(k) for k in ('state','reason','error')):record['changed_at']=now
    times=record.get('check_times',{})
    record['check_times']={check:times.get(check,now) for check in record.get('checks',[])}
    write(path,record)


def unfinished():
    """List the latest job of each target when that job is not complete."""
    cycle.source.require_controller()
    latest={}
    for path in cycle.ROOT.glob('*/image-job.json'):
        record=json.loads(path.read_text());key=(record['box'],record.get('role','air'))
        if key not in latest or record['created_at']>latest[key]['created_at']:latest[key]=record
    rows=[]
    for (box,role),record in sorted(latest.items()):
        if record['state']=='complete':continue
        # A waiting or updating record without its worker process was interrupted.
        state=record['state'] if record['state']=='failed' or running(record['operation']) else 'interrupted'
        rows.append(dict(target=box+'-'+role,operation=record['operation'],state=state,created_at=int(record['created_at']),
            detail=str(record.get('error') or record.get('reason') or '')[:500]))
    return rows


def start(box,operation=None,role='air'):
    cycle.source.require_controller()
    # The common target check is the only selection authority for a request.
    if cycle.service.target(cycle.source.snapshot(),box,role)=='stopped':
        raise RuntimeError(box+'-'+role+' is declared stopped; change Instance before an update')
    operation=operation or secrets.token_hex(12)
    if not re.fullmatch('[0-9a-f]{24}',operation):raise RuntimeError('invalid update operation')
    cycle.ROOT.mkdir(parents=True,mode=0o700,exist_ok=True)
    # Serialize selection and launch so repeated requests join the same job.
    with (cycle.ROOT/'launch.lock').open('a') as launch:
        fcntl.flock(launch,fcntl.LOCK_EX)
        requested=status(box,operation,role)
        if requested['state']=='absent':
            records=[json.loads(p.read_text()) for p in cycle.ROOT.glob('*/image-job.json')]
            active=[r for r in records if selected(r,box,role) and r['state'] in ('waiting','updating')]
            if active:operation=min(active,key=lambda r:r['created_at'])['operation']
        return launch_job(box,operation,role)


def launch_job(box,operation,role='air'):
    directory=cycle.ROOT/operation;directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    with (directory/'launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=directory/'image-job.json'
        if path.exists() and not selected(json.loads(path.read_text()),box,role):raise RuntimeError('operation belongs to another update target')
        record=status(box,operation,role)
        if record['state']=='complete':return record
        with (directory/'worker.lock').open('a') as worker_lock:
            try:fcntl.flock(worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return record
            # A new interpreter loads the updated modules. Joining a live worker
            # must not change the checkout from which it is preparing inputs.
            inputs.update_checkout(cycle.source.REPO)
        reason='checking that '+box+'-air tmux sessions are detached' if role=='air' else 'checking that no other update work is ongoing'
        record=dict(operation=operation,box=box,role=role,state='waiting',reason=reason,checks=[],created_at=time.time())
        save(path,record)
        with (directory/'image-job.log').open('ab') as log:
            subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'bin/vm-image-update'),
                              'worker','--box',box,'--role',role,'--operation',operation],stdin=subprocess.DEVNULL,
                             stdout=log,stderr=log,start_new_session=True,close_fds=True)
        return record


def wait_idle(box,path,record):
    """Wait until the runner has no attached client or active work."""
    target=box+'-air'
    while True:
        cycle.source.require_controller()
        activity=air_maintenance.remote(box,'image-idle')
        if activity['idle']:
            record['checks']=[target+' tmux sessions are detached: OK','Discussions on '+target+' are not active: OK']
            break
        attached=activity.get('attached',0);busy=len(activity.get('busy_pids',[]))
        if attached:reason=target+' tmux sessions are attached: waiting'
        elif activity.get('reason'):
            # A refused inspection leaves busy_pids unfiltered; its count would mislead.
            reason=activity['reason']+': waiting';busy=0
        elif busy:reason='Discussions or other work on '+target+' are active: waiting'
        else:reason=target+' is not idle: waiting'
        record.update(state='waiting',reason=reason,checks=[target+' tmux sessions are detached: OK'] if not attached else [],
                      attached=attached,busy_processes=busy)
        save(path,record)
        time.sleep(30)
    record.pop('attached',None);record.pop('busy_processes',None)


def worker(box,operation,role='air'):
    import signal
    signal.signal(signal.SIGALRM,lambda *_: (_ for _ in ()).throw(RuntimeError('update job reached its twelve-hour bound; resume operation')))
    signal.alarm(43200)
    path=cycle.ROOT/operation/'image-job.json';record=status(box,operation,role)
    target=box+'-'+role;record['checks']=[]
    for key in ('progress','progress_at','check_times'):record.pop(key,None)
    with (path.parent/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            cycle.source.require_controller()
            maintenance=path.parent/('update-'+role+'.json')
            saved=json.loads(maintenance.read_text()) if maintenance.exists() else {}
            # A recorded VM operation resumes; it must not wait for an idle runner again.
            if role=='air' and not saved.get('vm_operation'):wait_idle(box,path,record)
            # The shared lock gives no holder detail, so report only what is known.
            record.update(state='waiting',reason='checking that no other update work is ongoing');save(path,record)
            with cycle.runtime.vm_update_installation_lock(wait=True):
                record['checks']=record.get('checks',[])+['No other update work is ongoing: OK']
                record.update(reason='checking the installed '+target+' service');save(path,record)
                if role in INFRASTRUCTURE and not saved.get('vm_operation') and not cycle.service.step(box,role,'status').get('installed'):
                    raise RuntimeError(target+' requires explicit retained-data migration before requested updates')
            # Update code in this process and its frozen worker publishes named stages here.
            progress=path.parent/'progress.json';progress.unlink(missing_ok=True)
            os.environ['KLOKAST_PROGRESS']=str(progress)
            record.update(state='updating',reason='preparing and verifying the replacement image');save(path,record)
            result=cycle.step('update',box,operation,role,wait_for_lock=True)
            if result['state']!='complete':
                raise RuntimeError(target+' update is '+str(result['state'])+': '+str(result.get('reason') or 'resume after other work finishes'))
            reason='update verified' if result.get('replaced',True) else 'image unchanged; health checks passed'
            if 'workspace' in result:reason+='; tmux workspace '+result['workspace']
            record.update(state='complete',reason=reason,result=result);save(path,record)
        except BaseException as error:
            record.update(state='failed',error=str(error));save(path,record);raise
