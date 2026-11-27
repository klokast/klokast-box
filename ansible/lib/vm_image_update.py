"""Bounded controller jobs for requested VM image replacement and dom0 host updates."""
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
from vm_service_updates import RUNNER_BUSY
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
        stage=json.loads(progress.read_text())
        record.update(progress=stage['progress'],progress_at=stage.get('updated_at'),stages=stage.get('stages',[]))
    if record['state'] in ('waiting','updating') and (path.parent/CANCEL).exists():record['cancel_requested']=True
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
        if record['state'] in ('complete','cancelled'):continue
        # A waiting or updating record without its worker process was interrupted.
        state=record['state'] if record['state']=='failed' or running(record['operation']) else 'interrupted'
        rows.append(dict(target=box+'-'+role,operation=record['operation'],state=state,created_at=int(record['created_at']),
            detail=str(record.get('error') or record.get('reason') or '')[:500],**({'reboot':True} if record.get('reboot') else {})))
    return rows


def kind(reboot):
    return 'reboot' if reboot else 'update'


def start(box,role='air',reboot=False):
    """Join the running job of the target, restart its interrupted or failed job, or start a new job."""
    cycle.source.require_controller()
    if reboot and role!='dom0':raise RuntimeError('only a dom0 job can reboot without an update')
    # The common target check is the only selection authority for a request.
    if role=='dom0':
        import dom0_updates
        dom0_updates.authority(box)
    elif cycle.service.target(cycle.source.snapshot(),box,role)=='stopped':
        raise RuntimeError(box+'-'+role+' is declared stopped; change Instance before an update')
    cycle.ROOT.mkdir(parents=True,mode=0o700,exist_ok=True)
    # Serialize selection and launch so repeated requests join the same job.
    with (cycle.ROOT/'launch.lock').open('a') as launch:
        fcntl.flock(launch,fcntl.LOCK_EX)
        records=[r for r in (json.loads(p.read_text()) for p in cycle.ROOT.glob('*/image-job.json')) if selected(r,box,role)]
        active=[r for r in records if r['state'] in ('waiting','updating')]
        latest=max(records,key=lambda r:r['created_at']) if records else None
        # Only the operation of a failed job holds its recorded VM or host
        # transaction; a new operation cannot continue it.
        if active:previous=min(active,key=lambda r:r['created_at'])
        elif latest and latest['state']=='failed' and latest.get('reboot',False)==reboot:previous=latest
        else:previous=None
        operation=previous['operation'] if previous else secrets.token_hex(12)
        record,launched=launch_job(box,operation,role,reboot)
    # The client prints the request with the controller time, like each stage.
    if not previous:return dict(record,request='new',requested_at=time.time())
    if not launched:return dict(record,request='joined',requested_at=time.time())
    return dict(record,request='restarted',previous='failed' if previous['state']=='failed' else 'interrupted',requested_at=time.time())


def launch_job(box,operation,role='air',reboot=False):
    """Start a worker for the operation. Return the record and if a new worker started."""
    directory=cycle.ROOT/operation;directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    with (directory/'launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=directory/'image-job.json'
        if path.exists() and not selected(json.loads(path.read_text()),box,role):raise RuntimeError('operation belongs to another update target')
        record=status(box,operation,role)
        if record['state']!='absent' and record.get('reboot',False)!=reboot:
            # A request never turns a running update into a reboot, or back.
            raise RuntimeError('job %s is the running %s job of %s-%s; follow it with kk %s %s-%s'%(
                operation,kind(record.get('reboot')),box,role,kind(record.get('reboot')),box,role))
        if record['state']=='complete':return record,False
        with (directory/'worker.lock').open('a') as worker_lock:
            try:fcntl.flock(worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return record,False
            # A new interpreter loads the updated modules. Joining a live worker
            # must not change the checkout from which it is preparing inputs.
            inputs.update_checkout(cycle.source.REPO)
        (directory/CANCEL).unlink(missing_ok=True)
        reason='checking that no agent runs on '+box+'-air' if role=='air' else 'checking that no other update work is ongoing'
        record=dict(operation=operation,box=box,role=role,state='waiting',reason=reason,checks=[],created_at=time.time(),
                    **({'reboot':True} if reboot else {}))
        save(path,record)
        with (directory/'image-job.log').open('ab') as log:
            subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'bin/vm-image-update'),
                              'worker','--box',box,'--role',role,'--operation',operation],stdin=subprocess.DEVNULL,
                             stdout=log,stderr=log,start_new_session=True,close_fds=True)
        return record,True


CANCEL='cancel-requested'
# The job writes this file just before its first change of the target; a cancel then refuses.
CHANGING='target-changing.json'


class Cancelled(Exception):
    pass


# A runner that stays busy this long needs a person; the job stops before it changes the VM.
IDLE_WAIT=900


class IdleTimeout(Exception):
    """The runner stayed busy for the whole idle wait of the attempt."""


class Restart(Exception):
    """Start the worker again: the controller source moved, or an ended VM attempt needs a new one."""


def check_cancel(path):
    if (path.parent/CANCEL).exists():raise Cancelled()


@contextlib.contextmanager
def cancel_lock(directory):
    """Serialize a cancel request with the last check before the job changes its target."""
    with (directory/'cancel.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def before_change(directory):
    """Return the check that the job runs just before its first change of the target.

    The check stops a job that has a cancel request. Otherwise it records that
    the change starts, and each later cancel request refuses.
    """
    def check():
        with cancel_lock(directory):
            check_cancel(directory/'image-job.json')
            write(directory/CHANGING,{'at':time.time()})
    return check


def changes_target(directory,record):
    """Tell if the job changed, or can have changed, its target."""
    if (directory/CHANGING).exists():return True
    # A dom0 job writes the arguments of each host step before it runs it. This
    # also covers a job that code without the marker started.
    if record.get('role')=='dom0':
        import dom0_updates
        return any((dom0_updates.STATE/record['operation']/(action+'-args.json')).exists() for action in ('prepare','test-reboot'))
    return False


def cancel(box,role='air'):
    """Request the stop of a job before it changes its target.

    The job does not stop its current stage, for example an image build. It
    stops at the next check, at the latest just before its first change of the
    target. A job that changes its target always runs to its end.
    """
    cycle.source.require_controller()
    with (cycle.ROOT/'launch.lock').open('a') as launch:
        fcntl.flock(launch,fcntl.LOCK_EX)
        record=status(box,None,role);target=box+'-'+role
        if record['state'] not in ('waiting','updating'):
            raise RuntimeError('no waiting or running update job exists for '+target+'; nothing to cancel')
        directory=cycle.ROOT/record['operation'];path=directory/'image-job.json'
        thing='host' if role=='dom0' else 'VM'
        with cancel_lock(directory):
            saved=directory/('update-'+role+'.json')
            if saved.exists() and json.loads(saved.read_text()).get('vm_operation'):
                raise RuntimeError('the '+target+' job resumes a recorded VM operation and cannot stop safely; let it finish')
            if changes_target(directory,record):
                raise RuntimeError('the '+target+' job already changes the '+thing+' and cannot stop safely; follow it: kk status '+target)
            if not running(record['operation']):
                record.update(state='cancelled',reason='cancelled on request; the job was not running');save(path,record)
                return record
            (directory/CANCEL).touch()
    # A waiting worker checks the request at least once each second, except
    # while it waits for the shared update lock.
    deadline=time.monotonic()+150
    while time.monotonic()<deadline:
        record=status(box,record['operation'],role)
        if record['state']!='waiting':break
        time.sleep(1)
    # The request stays until the job stops; the job checks it before it changes the target.
    return record


def fenced(box):
    """True when dom0 fenced the runner after a failed update: it is stopped."""
    return (cycle.service.step(box,'air','status').get('assignment') or {}).get('stage')=='failed'


def wait_idle(box,path,record):
    """Wait until no agent runs on the runner, at most IDLE_WAIT seconds in all.

    The attempt keeps its waited time when its worker restarts, so repeated
    restarts cannot extend the bound. An attached tmux client does not block:
    the final runner check detaches it.
    """
    target=box+'-air'
    waited=record.get('idle_waited',0);started=time.monotonic()
    while True:
        cycle.source.require_controller()
        activity=air_maintenance.remote(box,'image-idle')
        record['idle_waited']=int(waited+time.monotonic()-started)
        if activity['idle']:
            record['checks']=['Discussions on '+target+' are not active: OK']
            break
        attached=activity.get('attached',0);busy=len(activity.get('busy_pids',[]))
        if activity.get('reason'):
            # A refused inspection leaves busy_pids unfiltered; its count would mislead.
            reason=activity['reason']+': waiting';busy=0
        elif busy:
            work=activity.get('busy_work')
            reason='Discussions or other work on '+target+' are active'+(' ['+'; '.join(work)+']' if work else '')+': waiting'
        # A runner image older than this check waits for its clients to detach.
        elif attached:reason=target+' tmux sessions are attached: waiting'
        else:reason=target+' is not idle: waiting'
        record.update(state='waiting',reason=reason,checks=[],attached=attached,busy_processes=busy)
        save(path,record)
        if record['idle_waited']>=IDLE_WAIT:raise IdleTimeout(reason.removesuffix(': waiting'))
        for _ in range(30):check_cancel(path);time.sleep(1)
    record.pop('attached',None);record.pop('busy_processes',None)


def dom0_worker(box,operation,path,record):
    """Run the recorded dom0 host update. Its runner check refuses instead of waiting."""
    import dom0_updates
    import platform_proxy
    # The same download settings as platform-update, which runs this update by hand.
    os.environ.update(platform_proxy.environment())
    target=box+'-dom0';reboot=record.get('reboot',False)
    with (path.parent/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            cycle.source.require_controller()
            record.update(state='waiting',reason='checking that no other update work is ongoing');save(path,record)
            with cycle.runtime.vm_update_installation_lock(wait=True):
                check_cancel(path)
                record['checks']=['No other update work is ongoing: OK']
                progress=path.parent/'progress.json';progress.unlink(missing_ok=True)
                os.environ['KLOKAST_PROGRESS']=str(progress)
                record.update(state='updating',reason=('rebooting ' if reboot else 'updating ')+target);save(path,record)
                result=dom0_updates.execute(box,operation=operation,manual=True,lock_held=True,
                                            before_change=before_change(path.parent),
                                            **({'verify_reboot':True} if reboot else {}))
            if result['state']=='unchanged':reason='image unchanged; '+target+' did not reboot'
            # The client adds the image from the result.
            elif result['state']=='complete':reason=kind(reboot)+' of '+target+' is complete'
            else:raise RuntimeError(target+' update is '+str(result['state'])+': '+str(result.get('reason') or 'no detail'))
            record.update(state='complete',reason=reason,result=result);save(path,record)
        except Cancelled:
            record.update(state='cancelled',reason='cancelled on request before the '+kind(reboot)+' changed the host');save(path,record)
            (path.parent/CANCEL).unlink(missing_ok=True)
        except BaseException as error:
            record.update(state='failed',error=str(error));save(path,record);raise


def job_error(error,maintenance,box,role):
    """Drop the controller resume command of the recorded VM operation: a new request restarts this job, which resumes it."""
    text=str(error)
    saved=json.loads(maintenance.read_text()) if maintenance.exists() else {}
    if saved.get('vm_operation'):
        text=text.replace('; resume with: ansible/bin/platform-update update --box %s --role %s --resume %s'%(
            box,role,saved['vm_operation']),'')
    return text


def worker(box,operation,role='air'):
    import signal
    signal.signal(signal.SIGALRM,lambda *_: (_ for _ in ()).throw(RuntimeError('update job reached its twelve-hour bound; request the update again to restart this job')))
    path=cycle.ROOT/operation/'image-job.json';record=status(box,operation,role)
    # A worker that restarts on fresher source keeps the bound of its job.
    signal.alarm(max(60,int(43200-(time.time()-record.get('created_at',time.time())))))
    target=box+'-'+role;record['checks']=[]
    for key in ('progress','progress_at','check_times'):record.pop(key,None)
    if role=='dom0':return dom0_worker(box,operation,path,record)
    maintenance=path.parent/('update-'+role+'.json')
    with (path.parent/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            cycle.source.require_controller()
            saved=json.loads(maintenance.read_text()) if maintenance.exists() else {}
            # A recorded VM operation resumes; it must not wait for an idle runner again.
            if role=='air' and not saved.get('vm_operation'):
                if fenced(box):record['checks']=[target+' is fenced after a failed update, so no agent runs on it: OK']
                else:wait_idle(box,path,record)
            # The shared lock gives no holder detail, so report only what is known.
            record.update(state='waiting',reason='checking that no other update work is ongoing');save(path,record)
            with cycle.runtime.vm_update_installation_lock(wait=True):
                check_cancel(path)
                # The update requires the checkout at upstream main. A long wait can
                # outlast later pushes; no other update work runs while this lock is held.
                if not saved.get('vm_operation'):
                    before=inputs.revision(cycle.source.REPO)
                    latest=inputs.update_checkout(cycle.source.REPO)
                    if latest!=before:
                        record.update(reason='controller source moved to '+latest[:12]+' while the job waited; '
                                      'restarting the worker on it');save(path,record)
                        raise Restart()
                record['checks']=record.get('checks',[])+['No other update work is ongoing: OK']
                record.update(reason='checking the installed '+target+' service');save(path,record)
                if role in INFRASTRUCTURE and not saved.get('vm_operation'):
                    observed=cycle.service.step(box,role,'status')
                    # A fenced generation already uses retained data; its replacement needs no migration.
                    if not observed.get('installed') and not observed.get('failed'):
                        raise RuntimeError(target+' requires explicit retained-data migration before requested updates')
            # Update code in this process and its frozen worker publishes named stages here.
            progress=path.parent/'progress.json';progress.unlink(missing_ok=True)
            os.environ['KLOKAST_PROGRESS']=str(progress)
            check_cancel(path)
            record.update(state='updating',reason='preparing and verifying the replacement image');save(path,record)
            result=cycle.step('update',box,operation,role,wait_for_lock=True,before_change=before_change(path.parent))
            if result['state']=='abandoned':
                # The new attempt waits again for an idle runner and fresh source.
                # Dom0 kept the old generation, so a cancel can stop the new attempt.
                record.pop('idle_waited',None);(path.parent/CHANGING).unlink(missing_ok=True)
                record.update(state='waiting',reason=result['reason']+'; starting a new attempt');save(path,record)
                raise Restart()
            if role=='air' and result['state']=='skipped' and result.get('reason')==RUNNER_BUSY:
                # Work started after the idle check; wait again within the same bound.
                record.update(state='waiting',reason=target+' became busy before the image step; waiting again');save(path,record)
                raise Restart()
            if result['state']!='complete':
                raise RuntimeError(target+' update is '+str(result['state'])+': '+str(result.get('reason') or 'resume after other work finishes'))
            reason='update of '+target+' is complete' if result.get('replaced',True) else 'image and configuration unchanged; health checks passed'
            if 'workspace' in result:reason+='; tmux workspace '+result['workspace']
            record.update(state='complete',reason=reason,result=result);save(path,record)
        except Cancelled:
            record.update(state='cancelled',reason='cancelled on request before the update changed the VM');save(path,record)
            (path.parent/CANCEL).unlink(missing_ok=True)
            return
        except IdleTimeout as busy:
            record.update(state='cancelled',reason='stopped after %d minutes: %s. The update did not change the VM. '
                          'When the runner is idle, start a new job: kk update %s'%(IDLE_WAIT//60,busy,target));save(path,record)
            return
        except Restart:
            pass
        except BaseException as error:
            record.update(state='failed',error=job_error(error,maintenance,box,role));save(path,record);raise
        else:
            return
    # The locks are released. A new interpreter loads the fast-forwarded modules
    # instead of driving new playbooks with old code.
    print('Restarting the update worker: '+record['reason'],file=sys.stderr,flush=True)
    os.execv(sys.executable,[sys.executable,str(Path(__file__).resolve().parents[1]/'bin/vm-image-update'),
                             'worker','--box',box,'--role',role,'--operation',operation])
