"""MacBook relay for development Platform maintenance and discussion handoff."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

import air

HELPER='/usr/local/bin/klokast-codex-handoff'
REMOTE_REPO='/home/smith/src/klokast/klokast-box'


def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix='.operation-',delete=False) as stream:
        p=Path(stream.name);json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
    try:p.replace(path)
    finally:p.unlink(missing_ok=True)


def remote(user,host,argv,*,data=None,timeout=90,last_json=False,show=False):
    # show: let the remote progress and error lines reach this terminal.
    result=subprocess.run([*air.SSH,user+'@'+host,shlex.join(argv)],input=data,
        stdout=subprocess.PIPE,stderr=None if show else subprocess.PIPE,timeout=timeout)
    if result.returncode:
        raise RuntimeError(host+': '+(result.stderr.decode(errors='replace').strip()[-1500:] if not show else
            'the command failed with exit status %d; see its output above'%result.returncode))
    try:return json.loads(result.stdout.strip().splitlines()[-1] if last_json else result.stdout)
    except ValueError:raise RuntimeError(host+' returned an invalid operation result') from None


def controller(instance):
    value=air.read_json(Path(instance)/'klokast-instance.json')
    controllers=value.get('controllers',{})
    candidates=[controllers[k] for k in ('active','standby') if k in controllers]
    if any(not isinstance(b,str) or not air.BOX.fullmatch(b) for b in candidates):
        raise ValueError('Instance must declare valid controllers')
    active=[]
    for box in candidates:
        try:
            state=remote('smith',box+'-ops',['/usr/local/sbin/klokast-controller-guard','--status','--json','--require-active'])
            if state.get('active') and state.get('role')=='active' and state.get('hostname')==box+'-ops':active.append(box)
        except (RuntimeError,subprocess.SubprocessError):pass
    if len(active)!=1:raise RuntimeError('exactly one reachable active controller is required; reconcile controller authority')
    return active[0]


def authorize(instance,origin,destination,session):
    box=controller(instance)
    result=remote('smith',box+'-ops',[REMOTE_REPO+'/ansible/bin/air-handoff-check',
        '--from',origin,'--to',destination,'--session',session])
    if result!={'authorized':True,'from':origin,'to':destination,'session':session}:
        raise RuntimeError('controller handoff preflight differs from the request')


def compatible_accounts(origin,destination,cwd):
    source=remote('agent',origin+'-air',[HELPER,'ready','--workspace',cwd],timeout=120)
    target=remote('agent',destination+'-air',[HELPER,'ready','--workspace',cwd],timeout=120)
    fingerprint=source.get('account_fingerprint')
    if not fingerprint or fingerprint!=target.get('account_fingerprint'):
        raise RuntimeError('both runners must be signed in to the same ChatGPT account before handoff')


def handoff(record,path,instance,suffix,*,attach=True):
    origin,destination,session,operation=(record[k] for k in ('from','to','session','operation'))
    authorize(instance,origin,destination,session)
    if record['phase']=='prepared':
        metadata=remote('agent',origin+'-air',[HELPER,'inspect','--session',session])
        record['cwd']=metadata['cwd'];atomic(path,record)
        compatible_accounts(origin,destination,record['cwd'])
        record['phase']='exporting';atomic(path,record)
    if record['phase']=='exporting':
        # The runner retains the durable export. The Mac has only a transient
        # in-memory relay; no archive, auth, repository or credential is copied.
        compatible_accounts(origin,destination,record['cwd'])
        print('Waiting for the selected source writer to close; operation '+operation,flush=True)
        bundle=remote('agent',origin+'-air',[HELPER,'export','--session',session,'--operation',operation,'--wait','300'],timeout=390)
        remote('agent',destination+'-air',[HELPER,'import','--operation',operation],data=json.dumps(bundle).encode(),timeout=180)
        record['phase']='imported';atomic(path,record)
    if record['phase']=='imported':
        remote('agent',destination+'-air',[HELPER,'start','--session',session,'--operation',operation],timeout=90)
        record['phase']='complete';atomic(path,record)
    if record['phase']!='complete':raise RuntimeError('unknown handoff phase; inspect the device operation record')
    active=controller(instance)
    remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/air-handoff-check','--from',origin,
        '--to',destination,'--session',session,'--completed-operation',operation])
    with air.settings(suffix) as settings:settings['last']=destination
    print('Discussion '+session+' resumed on '+destination+'-air; operation '+operation,flush=True)
    return air.attach(destination,True) if attach else 0


def maintenance_step(instance,record,action,role=None):
    active=controller(instance)
    argv=[REMOTE_REPO+'/ansible/bin/air-maintenance',action,'--box',record['box'],'--operation',record['operation']]
    if role:argv+=['--role',role]
    print('Maintenance '+action+(' '+role if role else '')+' on '+record['box']+' through '+active+'-ops',flush=True)
    return remote('smith',active+'-ops',argv,timeout=14500 if action=='update' else 120)


def switch(record,path,origin,destination):
    key='switch-'+origin+'-'+destination
    operation=record.setdefault(key,secrets.token_hex(12));atomic(path,record)
    value=remote('smith',origin+'-ops',[REMOTE_REPO+'/ansible/bin/ops-controller-ha','switchover',
        '--old-active',origin,'--new-active',destination,'--operation-id',operation],timeout=1810,last_json=True)
    if value.get('state')!='complete' or value.get('active')!=destination:
        raise RuntimeError('controller switch is not verified; resume this maintenance operation')


def maintain(record,path,instance,suffix,boxes):
    directory=path.parent
    if record['phase']=='prepared':
        active=controller(instance)
        record['original_controller']=active
        record['peer_controller']=next(b for b in (air.read_json(Path(instance)/'klokast-instance.json')['controllers'].values()) if b!=active)
        # Select a declared, available destination without changing preferences.
        with air.settings(suffix) as settings:preference=settings['prefer'][:]
        destination,_=air.choose([b for b in boxes if b!=record['box']],preference,None)
        record['destination']=destination
        record['outbound']=secrets.token_hex(12);record['inbound']=secrets.token_hex(12)
        record['phase']='reserve';atomic(path,record)
    if record['phase']=='reserve':
        maintenance_step(instance,record,'reserve')
        record['phase']='handoff';atomic(path,record)
    if record['phase']=='handoff':
        outbound=directory/(record['outbound']+'.json')
        move=air.read_json(outbound) if outbound.exists() else dict(action='handoff',phase='prepared',
            operation=record['outbound'],session=record['session'],**{'from':record['box'],'to':record['destination']})
        handoff(move,outbound,instance,suffix,attach=False)
        record['phase']='update-air' if 'air' in record['roles'] else 'switch';atomic(path,record)
    if record['phase']=='update-air':
        result=maintenance_step(instance,record,'update','air')
        if result['state']!='complete':
            raise RuntimeError('runner still has work; resume after clients detach and other work finishes')
        record['phase']='switch';atomic(path,record)
    if record['phase']=='switch':
        if 'ops' in record['roles'] and record['box']==record['original_controller']:
            switch(record,path,record['original_controller'],record['peer_controller'])
        record['phase']='update-ops';atomic(path,record)
    if record['phase']=='update-ops':
        if 'ops' in record['roles']:
            result=maintenance_step(instance,record,'update','ops')
            if result['state']!='complete':raise RuntimeError('controller replacement is incomplete; resume this operation')
        record['phase']='restore-controller';atomic(path,record)
    if record['phase']=='restore-controller':
        if 'ops' in record['roles'] and record['box']==record['original_controller']:
            switch(record,path,record['peer_controller'],record['original_controller'])
        record['phase']='return';atomic(path,record)
    if record['phase']=='return':
        maintenance_step(instance,record,'clear')
        if record['return']:
            inbound=directory/(record['inbound']+'.json')
            move=air.read_json(inbound) if inbound.exists() else dict(action='handoff',phase='prepared',
                operation=record['inbound'],session=record['session'],**{'from':record['destination'],'to':record['box']})
            handoff(move,inbound,instance,suffix,attach=False)
        record['phase']='complete';atomic(path,record)
    if record['phase']!='complete':raise RuntimeError('unknown maintenance phase')
    print('Maintenance verified: '+record['operation'],flush=True)
    return record['box'] if record['return'] else record['destination']


POLL_SECONDS=15
REPEAT_SECONDS=300
ROLES=('air','bak','dmz','iot','router','vpn-egress','ops')
LIST_HINT='List the machines with: kk status'
LOCK_STAGE='check that no other update work is ongoing (shared Platform update lock)'
STAGES=(LOCK_STAGE,'prepare the image, then replace, boot and verify the VM when the image changed')
AIR_STAGES=('check that the runner tmux sessions are detached and its discussions are not active',LOCK_STAGE,
            'prepare, boot and verify the replacement image, then restore the tmux workspace')


def table(rows):
    """Print rows in aligned columns. The last column is not padded."""
    widths=[max(len(r[i]) for r in rows) for i in range(len(rows[0])-1)]
    for r in rows:print(('  '.join(v.ljust(w) for v,w in zip(r,widths))+'  '+r[-1]).rstrip())


def declared_boxes(instance):
    if not instance:raise ValueError('select a private Instance worktree with --instance PATH or KLOKAST_INSTANCE')
    boxes=air.read_json(Path(instance).expanduser()/'klokast-instance.json').get('boxes')
    if not isinstance(boxes,dict):raise ValueError('Instance boxes must be an object')
    return sorted(boxes)


def update_target(instance,target):
    """Split BOX-ROLE. The controller checks the declaration and authority."""
    boxes=declared_boxes(instance)
    for role in ROLES:
        box=target[:-len(role)-1]
        if target.endswith('-'+role) and box in boxes and air.BOX.fullmatch(box):return box,role
    raise ValueError('update needs a BOX-ROLE target on a declared box; roles: '+', '.join(ROLES)+'. '+LIST_HINT)


PROMOTE_ROLES=('air','ops')


def promote_peers(instance,role):
    """Return the declared boxes that can hold this singleton role."""
    if role=='air':return air.declarations(instance)[1]
    declared_boxes(instance)
    controllers=air.read_json(Path(instance).expanduser()/'klokast-instance.json').get('controllers')
    if not isinstance(controllers,dict):raise ValueError('Instance controllers must be an object')
    boxes=[controllers.get(k) for k in ('active','standby') if controllers.get(k) is not None]
    if any(not isinstance(b,str) or not air.BOX.fullmatch(b) for b in boxes):raise ValueError('Instance declares an invalid controller box')
    return sorted(boxes)


def workspace_placement(boxes,resume=None):
    """Read the published workspace placement receipt from the runners."""
    placements=[p for p in (air.probe(box).get('placement') for box in boxes) if p]
    for placement in placements:
        if resume and placement.get('operation')==resume:return placement
    if not placements:
        raise RuntimeError('no runner publishes a workspace placement. Check the runners with: kk air --status. '
            'Make the first workspace move on the active controller; see klokast-ops/runbooks/07-codex-on-ops.md')
    if any(p!=placements[0] for p in placements) or placements[0].get('phase')!='ready':
        unfinished=next((p for p in placements if p.get('phase')!='ready'),placements[0])
        raise RuntimeError('a workspace move is unfinished. Resume it: kk promote %s-air --resume %s'%(
            unfinished.get('destination'),unfinished.get('operation')))
    return placements[0]


def promote(args):
    target=args.target
    role=next((r for r in PROMOTE_ROLES if target.endswith('-'+r)),None)
    box=target[:-len(role)-1] if role else ''
    if not role or box not in promote_peers(args.instance,role):
        raise ValueError('promote needs a declared BOX-ops controller or BOX-air runner. '+LIST_HINT)
    if args.resume and not re.fullmatch('[0-9a-f]{24}',args.resume):raise ValueError('invalid promote operation ID')
    operation=args.resume or secrets.token_hex(12)
    resume='Resume after an interruption: kk promote '+target+' --resume '+operation+'\nList the unfinished operations: kk promote --status'
    if role=='ops':
        peers=[b for b in promote_peers(args.instance,role) if b!=box]
        if len(peers)!=1:raise ValueError('a controller switch needs a declared active and standby controller')
        old=peers[0]
        if not args.resume:
            print('Finding the active controller...',flush=True)
            if controller(args.instance)==box:print(target+' is already the active controller.',flush=True);return 0
        print('Switching the active controller from '+old+'-ops to '+target+'; operation '+operation+'\n'+resume+
              '\nThe switch waits for running Platform operations. It can take 30 minutes.',flush=True)
        value=remote('smith',old+'-ops',[REMOTE_REPO+'/ansible/bin/ops-controller-ha','switchover',
            '--old-active',old,'--new-active',box,'--operation-id',operation],timeout=1810,last_json=True,show=True)
        if value.get('state')!='complete' or value.get('active')!=box:
            raise RuntimeError('the controller switch is not verified. '+resume)
        print(target+' is the active controller. Run git pull in the Instance worktree; the switch published the new placement.',flush=True)
        return 0
    print('Reading the workspace placement from the runners...',flush=True)
    placement=workspace_placement(promote_peers(args.instance,role),args.resume)
    if placement.get('phase')=='ready' and placement.get('current')==box:
        print('The shared workspace is already on '+target+'. Open it with: kk air',flush=True);return 0
    origin=placement.get('origin') if placement.get('operation')==args.resume else placement.get('current')
    if args.resume and placement.get('operation')==args.resume and placement.get('destination')!=box:
        raise ValueError('operation '+args.resume+' moves the workspace to '+str(placement.get('destination'))+'-air, not to '+target)
    if not isinstance(origin,str) or not air.BOX.fullmatch(origin) or origin==box:
        raise RuntimeError('the published workspace placement names no valid source runner; inspect: kk air --status')
    print('Finding the active controller...',flush=True)
    active=controller(args.instance)
    print('Moving the shared workspace from '+origin+'-air to '+target+' through '+active+'-ops; operation '+operation+'\n'+resume+
          '\nThe move waits until the open discussions are idle, then closes the source clients.',flush=True)
    value=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/air-workspace','move','--from',origin,'--to',box,
        '--operation',operation],timeout=3600,last_json=True,show=True)
    if value.get('state')!='complete' or value.get('current')!=box:
        raise RuntimeError('the workspace move is not verified. '+resume)
    print('The shared workspace is on '+target+' (%s discussions moved). Open it with: kk air'%value.get('discussions'),flush=True)
    return 0


def utc(seconds):
    return time.strftime('%Y-%m-%d %H:%M',time.gmtime(seconds)) if type(seconds) is int else 'unknown'


def promote_operations(instance,target=None):
    """Read the unfinished promote operations that the declared controllers record."""
    rows=[];notes=[]
    for box in promote_peers(instance,'ops'):
        try:
            found=remote('smith',box+'-ops',[REMOTE_REPO+'/ansible/bin/promote-status'],timeout=60)
            if not isinstance(found,list) or any(not isinstance(r,dict) for r in found):raise RuntimeError('invalid operation list')
        except (RuntimeError,subprocess.SubprocessError) as error:
            notes.append(box+'-ops: its records are not shown. Check that the controller is reachable and that its public '
                         'checkout contains ansible/bin/promote-status. Cause: '+str(error)[-300:]);continue
        for record in found:
            name,operation,error=record.get('target'),str(record.get('operation')),record.get('error')
            if target and name!=target:continue
            state='running' if record.get('running') else 'failed' if error else 'interrupted'
            detail='phase '+str(record.get('phase'))+', recorded on '+box+'-ops'+(
                ': '+str(error) if error else '' if state=='running' else '; no error is recorded')
            started=record.get('created_at') if type(record.get('created_at')) is int else None
            progress=record.get('updated_at') if type(record.get('updated_at')) is int else None
            rows.append((started or progress or 0,(name or 'unknown',operation,utc(started),utc(progress),state,detail)))
    return [r for _,r in sorted(rows,key=lambda item:item[0])],notes


def promote_status(instance,target=None,*,announce=True):
    """Print the unfinished promote operations. Return the number that need attention."""
    if announce:print('Reading the operation records of the controllers...',flush=True)
    rows,notes=promote_operations(instance,target)
    resume=['  kk promote '+r[0]+' --resume '+r[1] for r in rows if r[0]!='unknown' and r[4]!='running']
    if not rows:print('No unfinished promote operation'+(' for '+target if target else '')+'.')
    else:table([('TARGET','OPERATION','STARTED (UTC)','LAST PROGRESS','STATE','DETAIL'),*rows])
    if resume:print('\nResume an operation that is not running:\n'+'\n'.join(resume))
    sys.stdout.flush()
    for note in notes:print('kk promote: '+note,file=sys.stderr)
    return len(notes)+len([r for r in rows if r[4]!='running'])


def platform_status(instance):
    """Print a read-only summary of the controllers, runners and unfinished operations."""
    attention=0
    print('Controllers',flush=True)
    rows=[('TARGET','STATE')];active=[];causes=[]
    for box in promote_peers(instance,'ops'):
        try:
            state=remote('smith',box+'-ops',['/usr/local/sbin/klokast-controller-guard','--status','--json'])
            if not isinstance(state,dict) or state.get('hostname')!=box+'-ops':raise RuntimeError('invalid controller status')
            if state.get('active') and state.get('role')=='active':active.append(box)
            rows.append((box+'-ops',str(state.get('role'))))
        except (RuntimeError,subprocess.SubprocessError) as error:
            attention+=1;rows.append((box+'-ops','unreachable'));causes.append(box+'-ops is unreachable. Cause: '+str(error)[-300:])
    table(rows)
    for cause in causes:print(cause)
    if len(active)!=1:attention+=1;print('Exactly one active controller is required; %d found. Reconcile controller authority.'%len(active))
    print('\nRunners',flush=True)
    try:runners=promote_peers(instance,'air')
    except ValueError as error:runners=[];print('Not shown: '+str(error))
    rows=[('TARGET','STATE','WORKSPACE','CLIENTS','WORK','DETAIL')]
    for box in runners:
        status=air.probe(box)
        if status.get('error'):
            attention+=1;rows.append((box+'-air','unreachable','unknown','-','-',status['error']));continue
        placement=status.get('placement') or {}
        workspace=(('active' if placement.get('current')==box else 'standby') if placement.get('phase')=='ready' else
                   'moving' if placement else 'present' if status['workspace'] else 'none')
        detail=[]
        if not status['available']:attention+=1;detail.append(air.unavailable_reason(status))
        if workspace=='moving':attention+=1;detail.append('workspace move '+str(placement.get('operation'))+' is in phase '+str(placement.get('phase')))
        if status.get('pending'):detail.append('replacement pending, operation '+str((status.get('reservation') or {}).get('operation','unknown')))
        elif status.get('reservation') if placement else status['maintenance']:detail.append('maintenance reservation is active')
        rows.append((box+'-air','available' if status['available'] else 'unavailable',workspace,str(status['attached']),
                     str(len(status['busy_pids'])),'; '.join(detail)))
    if runners:table(rows)
    print('\nMachines',flush=True)
    if len(active)!=1:print('Not shown: the active controller reads the machine state.')
    else:
        try:attention+=show_machines(remote('smith',active[0]+'-ops',[REMOTE_REPO+'/ansible/bin/machine-status','list'],timeout=180))
        except (RuntimeError,subprocess.SubprocessError,TypeError,AttributeError) as error:
            attention+=1;print('Not shown. Check that the public checkout of '+active[0]+'-ops contains ansible/bin/machine-status. '
                               'Cause: '+str(error)[-300:])
    print('\nApplications',flush=True)
    rows=[(name,state,'available' if client else 'none') for name,state,client in applications(instance)]
    if rows:table([('APPLICATION','DECLARED','CLIENT'),*rows])
    else:print('The Instance declares no application.')
    print('\nPromote operations (unfinished)',flush=True)
    attention+=promote_status(instance,announce=False)
    print('\nUpdate jobs (running, failed or interrupted)',flush=True)
    if len(active)!=1:print('Not shown: the job records are on the active controller.')
    else:
        try:
            jobs=remote('smith',active[0]+'-ops',[REMOTE_REPO+'/ansible/bin/vm-image-update','list'],timeout=60)
            if not isinstance(jobs,list) or any(not isinstance(j,dict) for j in jobs):raise RuntimeError('invalid job list')
            rows=[(str(j.get('target')),str(j.get('operation')),utc(j.get('created_at')),str(j.get('state')),str(j.get('detail') or '')) for j in jobs]
            stopped=[r for r in rows if r[3] in ('failed','interrupted')];attention+=len(stopped)
            if not rows:print('No unfinished update job.')
            else:table([('TARGET','OPERATION','STARTED (UTC)','STATE','DETAIL'),*rows])
            if stopped:print('\nRetry a job that is not running:\n'+'\n'.join('  kk update '+r[0]+' --resume '+r[1] for r in stopped))
        except (RuntimeError,subprocess.SubprocessError) as error:
            attention+=1;print('Not shown. Check that the public checkout of '+active[0]+'-ops contains the vm-image-update list '
                               'command. Cause: '+str(error)[-300:])
    print('\nNightly update (latest scheduled run on the active controller)',flush=True)
    if len(active)!=1:print('Not shown: the run record is on the active controller.')
    else:
        try:
            run=remote('smith',active[0]+'-ops',[REMOTE_REPO+'/ansible/bin/ops-controller-nightly','--status'],timeout=60)
            if not isinstance(run,dict) or not isinstance(run.get('state'),str):raise RuntimeError('invalid nightly run record')
            when=lambda v:v[:16].replace('T',' ') if isinstance(v,str) else '-'
            if run['state']=='none':print('No scheduled run is recorded on '+active[0]+'-ops.')
            else:
                table([('STATE','STARTED (UTC)','FINISHED (UTC)','DETAIL'),
                       (run['state'],when(run.get('started')),when(run.get('finished')),str(run.get('error') or run.get('reason') or ''))])
                if run['state'] in ('failed','interrupted'):
                    attention+=1;print('Read the run log on '+active[0]+'-ops: '+str(run.get('log')))
        except (RuntimeError,subprocess.SubprocessError) as error:
            attention+=1;print('Not shown. Check that the public checkout of '+active[0]+'-ops contains ops-controller-nightly '
                               '--status. Cause: '+str(error)[-300:])
    print('\n'+('No problem found.' if not attention else '%d item(s) need attention; see above.'%attention)+
          '\nRead the details of one machine or application with: kk status TARGET',flush=True)
    return 1 if attention else 0


def applications(instance):
    """List the Instance applications as (name, declared state, client is available)."""
    apps=air.read_json(Path(instance).expanduser()/'klokast-instance.json').get('apps',{})
    if not isinstance(apps,dict):raise ValueError('Instance apps must be an object')
    rows=[]
    for name in sorted(apps):
        state=apps[name].get('desired-state') if isinstance(apps[name],dict) else None
        if not re.fullmatch('[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',name) or state not in ('present','absent'):
            raise ValueError('Instance contains an invalid application binding')
        rows.append((name,state,(Path(__file__).resolve().parents[2]/'apps'/name/'bin'/(name+'-client')).is_file()))
    return rows


def show_machines(report):
    """Print one line for each declared machine. Return the number that need attention."""
    machines=report.get('machines')
    if not isinstance(machines,list) or any(not isinstance(m,dict) for m in machines):raise RuntimeError('invalid machine list')
    rows=[(str(m.get('target')),str(m.get('state')),utc(m.get('updated_at')) if m.get('state')=='reachable' else '-',
           str(m.get('alpine','-')),str(m.get('packages','-'))) for m in machines]
    table([('TARGET','STATE','LAST UPDATE (UTC)','ALPINE','PACKAGES'),*rows])
    failed=[m for m in machines if m.get('state')=='unreachable']
    for m in failed:print(str(m.get('target'))+' is unreachable. Cause: '+str(m.get('error'))[-300:])
    return len(failed)


def target_status(instance,target):
    """Print the details of one application or one machine."""
    for name,state,client in applications(instance):
        if name!=target:continue
        print('Application: '+name+'\nDeclared state: '+state+'\nClient: '+('available' if client else 'none')+
              '\nThe Platform records no update time and no package list for an application.'+
              ('\nRead the commands of this application with: kk app '+name+' --help' if client else ''),flush=True)
        return 0
    if not re.fullmatch('[a-z][a-z0-9-]{0,62}',target):raise ValueError('status needs a machine or application name. '+LIST_HINT)
    print('Finding the active controller...',flush=True)
    active=controller(instance)
    print('Reading the state of '+target+' through '+active+'-ops...',flush=True)
    machine=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/machine-status','show','--target',target],timeout=120)
    if not isinstance(machine,dict):raise RuntimeError('invalid machine status')
    state=machine.get('state');code=1 if state=='unreachable' else 0
    print('\nMachine: '+target+'\nDeclared state: '+str(machine.get('declared'))+'\nState: '+str(state))
    if state=='unreachable':print('Cause: '+str(machine.get('error'))[-300:])
    packages=machine.get('packages') if isinstance(machine.get('packages'),list) else []
    if state=='reachable':
        print('Last update: '+utc(machine.get('updated_at'))+' UTC'+
              ('\nImage built: '+utc(machine['image_built_at'])+' UTC' if machine.get('image_built_at') is not None else '')+
              '\nLast boot: '+utc(machine.get('booted_at'))+' UTC\nAlpine release: '+str(machine.get('alpine'))+
              '\nPackages: %d'%len(packages))
    role=next((r for r in ROLES if target.endswith('-'+r)),None)
    if role:
        print('\nUpdate job',flush=True)
        job=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/vm-image-update','status','--box',target[:-len(role)-1],'--role',role],timeout=120)
        if job.get('operation'):print('Update operation: '+str(job['operation']))
        show_checks(job);show_stage(job,describe(job,target))
        if job.get('state')=='failed':
            code=1
            if job.get('operation'):print('Retry the same job: kk update '+target+' --resume '+str(job['operation']))
    if packages:
        print('\nPackages');table([('PACKAGE','VERSION','BUILT (UTC)'),*[(str(p.get('name')),str(p.get('version')),utc(p.get('built_at'))) for p in packages]])
        print('\nBUILT is the time at which the distribution built this package version.')
    if state=='reachable':print('Last update is the last change of the root file system identity or of the package database.')
    sys.stdout.flush()
    return code


def describe(record,target):
    state=record.get('state','unknown')
    if state=='absent':return 'no update job exists for '+target
    if state=='failed':return 'failed: '+str(record.get('error') or 'no error detail')
    text=state+': '+str(record.get('progress') if state=='updating' and record.get('progress') else record.get('reason') or 'no detail')
    if state=='waiting' and (record.get('attached') or record.get('busy_processes')):
        text+='; attached clients: %s, busy processes: %s. Detach from %s and let its work finish'%(
            record.get('attached',0),record.get('busy_processes',0),target)
    result=record.get('result')
    if state=='complete' and isinstance(result,dict):text+='; image '+str(result.get('image'))
    return text


def say(text,*,stream=None):
    print(time.strftime('%H:%M:%S UTC ',time.gmtime())+text,file=stream or sys.stdout,flush=True)


def say_at(at,text):
    """Print a line with the UTC time that the controller recorded for it."""
    if type(at) not in (int,float):print('--:--:-- UTC '+text,flush=True);return
    # A job can continue after midnight; then the time of day is not sufficient.
    day=time.strftime('%Y-%m-%d ',time.gmtime(at))
    print(('' if day==time.strftime('%Y-%m-%d ',time.gmtime()) else day)+time.strftime('%H:%M:%S UTC ',time.gmtime(at))+text,flush=True)


def show_checks(record,shown=()):
    """Print the passed checks that were not printed yet."""
    checks=[str(c) for c in record.get('checks') or []]
    times=record.get('check_times') if isinstance(record.get('check_times'),dict) else {}
    for check in checks:
        if check not in shown:say_at(times.get(check),check)
    return set(shown)|set(checks)


def show_stage(record,text):
    """Print the job stage with the time at which the job entered it."""
    published=record.get('state')=='updating' and record.get('progress')
    say_at(record.get('progress_at' if published else 'changed_at'),text)


def follow(active,target,operation,record,box,role):
    """Print each job stage until the controller job ends."""
    argv=[REMOTE_REPO+'/ansible/bin/vm-image-update','status','--box',box,'--role',role,'--operation',operation]
    checks=show_checks(record);last=describe(record,target);shown=time.monotonic();failures=0;show_stage(record,last)
    while record.get('state') in ('waiting','updating'):
        time.sleep(POLL_SECONDS)
        try:record=remote('smith',active+'-ops',argv,timeout=120);failures=0
        except (RuntimeError,subprocess.SubprocessError) as error:
            failures+=1
            if failures>=5:raise RuntimeError('lost contact with '+active+'-ops; the job continues there. Check it: kk status '+target) from error
            say('status check failed, retrying: '+str(error)[-300:],stream=sys.stderr);continue
        checks=show_checks(record,checks);text=describe(record,target)
        if text!=last:show_stage(record,text);last=text;shown=time.monotonic()
        elif time.monotonic()-shown>=REPEAT_SECONDS:say('still '+text);shown=time.monotonic()
    return 1 if record.get('state')!='complete' else 0


def image_update(args):
    box,role=update_target(args.instance,args.target)
    if args.resume and not re.fullmatch('[0-9a-f]{24}',args.resume):raise ValueError('invalid update operation ID')
    print('Finding the active controller...',flush=True)
    active=controller(args.instance)
    requested=operation=args.resume or secrets.token_hex(12)
    argv=[REMOTE_REPO+'/ansible/bin/vm-image-update','start','--box',box,'--role',role,'--operation',operation]
    print('Requesting the '+args.target+' image update from '+active+'-ops...',flush=True)
    result=remote('smith',active+'-ops',argv,timeout=120)
    operation=result.get('operation') or operation
    if operation!=requested:print('Joined the existing update job for '+args.target+'.',flush=True)
    print('Update operation: '+operation+'\nResume: kk update '+args.target+' --resume '+operation+
          '\nThe controller will:\n'+''.join('  %d. %s\n'%(i,s) for i,s in enumerate(AIR_STAGES if role=='air' else STAGES,1))+
          'The job runs on '+active+'-ops. It continues if this command stops or the MacBook sleeps.',flush=True)
    if args.detach:
        show_checks(result);show_stage(result,describe(result,args.target));print('Check progress: kk status '+args.target,flush=True)
        return 1 if result.get('state')=='failed' else 0
    print('Following progress. Press Ctrl-C to stop following.',flush=True)
    try:code=follow(active,args.target,operation,result,box,role)
    except KeyboardInterrupt:
        print('\nStopped following. The job continues. Check it: kk status '+args.target,flush=True);return 130
    if code:print('Inspect: kk status '+args.target+'. Retry the same job: kk update '+args.target+' --resume '+operation,flush=True)
    return code


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance',default=os.environ.get('KLOKAST_INSTANCE',''))
    sub=parser.add_subparsers(dest='action',required=True)
    u=sub.add_parser('update',help='request a safe VM image replacement')
    u.prog='kk update'
    u.add_argument('target',metavar='BOX-ROLE');u.add_argument('--resume')
    u.add_argument('--detach',action='store_true',help='request the update and return without following progress')
    m=sub.add_parser('promote',help='make one controller or runner workspace placement the active one')
    m.prog='kk promote'
    m.add_argument('target',nargs='?',metavar='BOX-ROLE');g=m.add_mutually_exclusive_group()
    g.add_argument('--resume',metavar='OPERATION_ID',help='resume an interrupted promotion')
    g.add_argument('--status',action='store_true',help='list the unfinished promote operations, for all targets or for BOX-ROLE')
    setup=sub.add_parser('setup');setup.add_argument('--update-time',required=True)
    t=sub.add_parser('status',help='show the machines, the applications and the unfinished operations, or the details of TARGET')
    t.prog='kk status';t.add_argument('target',nargs='?',metavar='TARGET',help='a machine (BOX-ROLE or BOX-dom0) or an application')
    t.add_argument('--update-time',action='store_true')
    p=sub.add_parser('handoff',help='relay only one discussion through this device')
    p.add_argument('--session');p.add_argument('--from',dest='origin');p.add_argument('--to',dest='destination')
    p.add_argument('--resume',help='resume a bounded handoff operation')
    p=sub.add_parser('maintain',help='handoff, safely replace selected roles, and optionally return')
    p.add_argument('--box');p.add_argument('--roles',default='air,ops');p.add_argument('--session')
    p.add_argument('--return',dest='return_session',action='store_true')
    p.add_argument('--resume',help='resume a bounded maintenance operation')
    args=parser.parse_args(argv)
    if args.action=='promote' and not args.status and not args.target:m.error('a BOX-ROLE target is required. '+LIST_HINT)
    try:
        if args.action=='setup' or (args.action=='status' and args.update_time):
            if args.action=='status' and args.target:raise ValueError('--update-time cannot accompany a status target')
            value=args.update_time if args.action=='setup' else None
            if value and not re.fullmatch(r'([01][0-9]|2[0-3]):[0-5][0-9]',value):raise ValueError('use HH:MM in UTC, from 00:00 through 23:59')
            active=controller(args.instance)
            result=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/platform-instance','update-time',*([value] if value else [])],timeout=600,show=True,last_json=True)
            print('Nightly updates: '+result['time']+' UTC'+(' (default)' if result.get('default') else ''))
            return 0
        if args.action=='update':return image_update(args)
        if args.action=='status':return target_status(args.instance,args.target) if args.target else platform_status(args.instance)
        if args.action=='promote':
            if args.status:return 1 if promote_status(args.instance,args.target) else 0
            return promote(args)
        suffix,boxes=air.declarations(args.instance)
        directory=air.config_path().parent/'operations'/suffix
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (directory/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if args.resume:
                if not re.fullmatch('[0-9a-f]{24}',args.resume):raise ValueError('invalid operation ID')
                if any((args.session,getattr(args,'origin',None),getattr(args,'destination',None),getattr(args,'box',None))):raise ValueError('--resume uses the saved discussion and runners')
                path=directory/(args.resume+'.json');record=air.read_json(path)
                if record['operation']!=args.resume or record['action']!=args.action:raise ValueError('operation does not match this command')
            else:
                if not args.session or str(uuid.UUID(args.session))!=args.session:raise ValueError('--session needs a canonical discussion UUID')
                if args.action=='handoff':
                    if args.origin not in boxes or args.destination not in boxes or args.origin==args.destination:
                        raise ValueError('--from and --to must name different declared runners')
                elif args.box not in boxes or args.roles not in ('air','ops','air,ops','ops,air'):
                    raise ValueError('maintenance needs a declared --box and --roles air, ops, or air,ops')
                for p in directory.glob('*.json'):
                    old=air.read_json(p)
                    if old['phase']!='complete':raise ValueError('resume unfinished operation '+old['operation'])
                operation=secrets.token_hex(12);path=directory/(operation+'.json')
                record=dict(action=args.action,operation=operation,phase='prepared',session=args.session)
                if args.action=='handoff':record.update({'from':args.origin,'to':args.destination})
                else:record.update(box=args.box,roles=args.roles.split(','),**{'return':args.return_session})
                atomic(path,record)
            print('Resume command: kk platform '+args.action+' --resume '+record['operation'],flush=True)
            # The operation lock covers transfer, not the terminal attach.
            if args.action=='handoff':
                handoff(record,path,args.instance,suffix,attach=False);selected=record['to']
            else:selected=maintain(record,path,args.instance,suffix,boxes)
        return air.attach(selected,True)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as error:
        print('kk '+(args.action if args.action in ('update','promote','status') else 'platform')+': '+str(error),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
