"""The resumable image update step; existing VM transactions own replacement state."""
import fcntl
import json
from pathlib import Path
import re
import time

import air_maintenance
import platform_resource_runtime as runtime
import platform_source as source
import vm_service_runtime as service
import vm_service_updates as updates
import vm_service_probe
from platform_maintenance import write
from vm_lifecycle_roles import INFRASTRUCTURE

ROOT=Path('/home/smith/private/klokast/air-maintenance')


def update(box,role,operation,path,record,*,wait_for_lock=False):
    with (runtime.vm_update_installation_lock(wait=True) if wait_for_lock else runtime.vm_update_installation_lock()):
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
        assignment=observed.get('assignment') or {};selected=observed.get('operation') or {}
        abandoned=selected.get('operation_id')==resume and selected.get('stage')=='abandoned'
        recovered=assignment.get('operation_id')==resume and assignment.get('stage')=='recovered' and \
            any(p.get('operation_id')==resume for p in pending)
        if resume and (abandoned or recovered):
            # The pre-boot deadline passed and native recovery restored the old
            # generation, or an operator already abandoned the attempt. It
            # changed nothing that runs; the guarded abandon refuses if the new
            # VM ever booted. The caller starts a new checked attempt, with its
            # own idle and source checks.
            if not abandoned:
                result=updates.execute(box,role,resume=resume,lock_held=True,abandon=True)
                if result.get('state')!='abandoned':
                    raise RuntimeError('VM operation '+resume+' was recovered but could not be abandoned: '+str(result.get('state')))
            for key in ('vm_operation','inputs','previous_generation'):record.pop(key,None)
            record['abandoned']=record.get('abandoned',[])+[resume];write(path,record)
            # The attempt can have closed the runner before it ended. No VM
            # operation is pending now, so a runner closure of a replacement has ended.
            if role=='air':air_maintenance.reopen(box,'air',air_maintenance.remote(box,'status').get('reservation'))
            return {'state':'abandoned','operation_id':resume,
                    'reason':'VM operation '+resume+' ended before the new '+box+'-'+role+' started; dom0 kept the old '
                    'generation. The attempt is abandoned with its disks retained; a new attempt is required'}
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
            migrate_root_data=role in INFRASTRUCTURE and not bool(observed.get('installed')) and not resume,
            operation_started=started)
        if result['state']=='complete':
            record['result']=result;record['phase']='complete';write(path,record)
        return result


def step(action,box,operation,role=None,*,wait_for_lock=False):
    source.require_controller()
    if not re.fullmatch('[a-z][a-z0-9-]{0,30}',box) or not re.fullmatch('[0-9a-f]{24}',operation):
        raise RuntimeError('invalid maintenance box or operation ID')
    view=source.snapshot()['instance']
    if role not in (None,*service.ROLES):raise RuntimeError('maintenance supports the common VM roles only')
    if action!='update' or role is None:raise RuntimeError('maintenance supports only the update of a VM role')
    # A runner update needs the runner; other roles use the common target check.
    if role=='air' and box+'-air' not in view['airunners']:
        raise RuntimeError('maintenance requires a declared native runner')
    directory=ROOT/operation;directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=directory/(action+('-'+role if role else '')+'.json')
    with (directory/'operation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        record=json.loads(path.read_text()) if path.exists() else {
            'operation':operation,'box':box,'role':role,'action':action,'phase':'started','created_at':int(time.time())}
        if any(record[k]!=v for k,v in dict(box=box,role=role,action=action,operation=operation).items()):
            raise RuntimeError('maintenance operation selects different inputs')
        write(path,record)
        # Reverify an accepted operation on replay through its native resume
        # path. Never treat a controller-only success note as health proof.
        return update(box,role,operation,path,record,wait_for_lock=wait_for_lock)
