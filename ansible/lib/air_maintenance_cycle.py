"""Bounded maintenance steps; existing VM transactions own replacement state."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

import air_maintenance
import platform_resource_runtime as runtime
import platform_source as source
import vm_service_runtime as service
import vm_service_updates as updates
import vm_service_probe
from platform_maintenance import write

ROOT=Path('/home/smith/private/klokast/air-maintenance')
JOURNAL=Path('/var/lib/klokast/operations-journal/cases/air-maintenance')


def update(box,role,operation,path,record):
    with runtime.vm_update_installation_lock():
        observed=service.step(box,role,'status',operation=record.get('vm_operation',''))
        pending=observed.get('pending',[])
        resume=record.get('vm_operation')
        if record['phase']=='complete':
            installed=observed.get('installed')
            if pending or not installed or installed.get('image')!=record['result'].get('image'):
                raise RuntimeError('accepted maintenance generation changed; inspect current VM state')
            if resume and installed.get('operation_id')!=resume:
                raise RuntimeError('another maintenance generation replaced this operation')
            vm_service_probe.verify(installed['requested_configuration']['before'],vm_service_probe.probe(box,role),installed,unfinished=False)
            return record['result']
        if resume:
            protected=observed.get('operation')
            if not protected and not pending:
                installed=(observed.get('installed') or {}).get('operation_id')
                if installed!=record['previous_generation']:
                    raise RuntimeError('VM generation changed after interrupted preparation; reconcile before resume')
                # The controller lost contact before protected preparation.
                # No disk transaction exists. A new checked attempt is safe.
                resume=None
        elif pending:
            raise RuntimeError('another VM transaction is pending; resume it before this maintenance cycle')
        def started(vm_operation,work,before):
            record.update(vm_operation=vm_operation,inputs=str(work),
                previous_generation=(before.get('installed') or {}).get('operation_id'))
            write(path,record)
        result=updates.execute(box,role,resume=resume,lock_held=True,
            migrate_root_data=not bool(observed.get('installed')) and not resume,
            maintenance=role=='air' and not resume,operation_started=started)
        if result['state']=='complete':
            record['result']=result;record['phase']='complete';write(path,record)
        return result


def step(action,box,operation,role=None):
    source.require_controller()
    if not re.fullmatch('[a-z][a-z0-9-]{0,30}',box) or not re.fullmatch('[0-9a-f]{24}',operation):
        raise RuntimeError('invalid maintenance box or operation ID')
    view=source.snapshot()['instance']
    if box+'-air' not in view['airunners']:raise RuntimeError('maintenance requires a declared native runner')
    if role not in (None,'air','ops'):raise RuntimeError('maintenance supports air and ops roles only')
    directory=ROOT/operation;directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=directory/(action+('-'+role if role else '')+'.json')
    with (directory/'operation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        record=json.loads(path.read_text()) if path.exists() else {
            'operation':operation,'box':box,'role':role,'action':action,'phase':'started','created_at':int(time.time())}
        if any(record[k]!=v for k,v in dict(box=box,role=role,action=action,operation=operation).items()):
            raise RuntimeError('maintenance operation selects different inputs')
        write(path,record)
        if action=='update':
            if role is None:raise RuntimeError('update needs --role air or ops')
            # Reverify an accepted operation on replay through its native resume
            # path. Never treat a controller-only success note as health proof.
            return update(box,role,operation,path,record)
        with runtime.vm_update_installation_lock():
            if action=='reserve':return air_maintenance.remote(box,'reserve',operation=operation)
            if action=='clear':return air_maintenance.remote(box,'clear',operation=operation)
            if action=='activity':return air_maintenance.remote(box,'idle')
        raise RuntimeError('unknown maintenance step')


@contextlib.contextmanager
def logged(operation,action,role):
    JOURNAL.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=JOURNAL/(operation+'-'+action+('-'+role if role else '')+'-'+str(time.time_ns())+'.log')
    # Include child Ansible output without sending private evidence to the Mac.
    out=os.dup(1);err=os.dup(2)
    with path.open('xb',buffering=0) as log:
        os.chmod(path,0o600)
        try:
            os.dup2(log.fileno(),1);os.dup2(log.fileno(),2)
            yield path
        finally:
            __import__('sys').stdout.flush();__import__('sys').stderr.flush()
            os.dup2(out,1);os.dup2(err,2);os.close(out);os.close(err)
