"""MacBook relay for development Platform maintenance and discussion handoff."""
import argparse
import contextlib
import fcntl
import hashlib
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
import controller_setup

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


def declared_controllers(instance):
    """Return the controller boxes of Instance; their markers select the active one."""
    path=Path(instance).expanduser()/'klokast-instance.json'
    boxes=air.read_json(path).get('controllers')
    if isinstance(boxes,dict):
        # The removed placement form; the published Instance has the box list.
        raise ValueError('%s has the old controllers form {"active": ..., "standby": ...}. '
                         'Update this Instance checkout: git -C %s pull --ff-only'%(path,shlex.quote(str(path.parent))))
    if (not isinstance(boxes,list) or not 1<=len(boxes)<=2 or len(set(boxes))!=len(boxes) or
            any(not isinstance(b,str) or not air.BOX.fullmatch(b) for b in boxes)):
        raise ValueError('%s: controllers must list one or two distinct boxes, for example ["k001", "k002"]'%path)
    return sorted(boxes)


def controller(instance):
    """Return the one active controller box; else name the result of each declared box."""
    active,reports=[],[]
    for box in declared_controllers(instance):
        host=box+'-ops'
        try:
            state=remote('smith',host,['/usr/local/sbin/klokast-controller-guard','--status','--json','--require-active'])
        except subprocess.TimeoutExpired:
            reports.append(host+': no answer in 90 seconds');continue
        except (RuntimeError,subprocess.SubprocessError) as error:
            # The guard reports a standby on stderr; ssh reports its error on its last line.
            lines=str(error).removeprefix(host+': ').strip().splitlines()
            reports.append(host+': '+(lines[-1] if lines else 'the command failed without an error message'));continue
        if state.get('active') and state.get('role')=='active' and state.get('hostname')==host:
            active.append(box);reports.append(host+': active')
        else:reports.append(host+': unexpected guard result role=%s hostname=%s'%(state.get('role'),state.get('hostname')))
    if len(active)==1:return active[0]
    detail='\n  '+'\n  '.join(reports)
    if active:
        raise RuntimeError('more than one controller reports that it is active. Do not start Platform operations. '
            'Fence one controller as doc/platform-deploy.md#controller-recovery tells.'+detail)
    raise RuntimeError('no active controller answered, so the command cannot start.'+detail+
        '\nIf a controller is unreachable, test the connection from this MacBook: ssh smith@BOX-ops true'
        '\nIf all controllers answer and none is active, see doc/platform-deploy.md#controller-recovery.')


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
        record['peer_controller']=next(b for b in declared_controllers(instance) if b!=active)
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
REPEAT_SECONDS=120
ROLES=('air','bak','dmz','iot','router','vpn-egress','ops')
LIST_HINT='List the machines with: kk status'


def table(rows):
    """Print rows in aligned columns. The last column is not padded."""
    widths=[max(len(r[i]) for r in rows if r) for i in range(len(rows[0])-1)]
    for r in rows:
        if not r:print();continue
        print(('  '.join(v.ljust(w) for v,w in zip(r,widths))+'  '+r[-1]).rstrip())


def declared_boxes(instance):
    if not instance:raise ValueError('select a private Instance worktree with --instance PATH or KLOKAST_INSTANCE')
    boxes=air.read_json(Path(instance).expanduser()/'klokast-instance.json').get('boxes')
    if not isinstance(boxes,dict):raise ValueError('Instance boxes must be an object')
    return sorted(boxes)


def update_target(instance,target):
    """Split BOX-ROLE. The controller checks the declaration and authority."""
    boxes=declared_boxes(instance)
    for role in ('dom0',)+ROLES:
        box=target[:-len(role)-1]
        if target.endswith('-'+role) and box in boxes and air.BOX.fullmatch(box):return box,role
    raise ValueError('update needs a BOX-ROLE target on a declared box; roles: '+', '.join(ROLES)+'. '+LIST_HINT)


PROMOTE_ROLES=('air','ops')


def promote_peers(instance,role):
    """Return the declared boxes that can hold this singleton role."""
    if role=='air':return air.declarations(instance)[1]
    declared_boxes(instance)
    return declared_controllers(instance)


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


def utc(seconds,*,full=False):
    return time.strftime('%Y-%m-%d %H:%M:%S' if full else '%Y-%m-%d %H:%M',time.gmtime(seconds)) if type(seconds) is int else 'unknown'


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


def section(title):
    print('\n# '+title+'\n',flush=True)


def bullet(text):
    print('- '+text,flush=True)


def report_footer():
    print('\n------------- '+time.strftime('%a %Y-%m-%d %H:%M:%S UTC',time.gmtime())+' -------------',flush=True)


def summary(issues):
    """Print the Summary section: each problem on its own line."""
    section('Summary')
    if not issues:bullet('No problem found.');return
    bullet(('1 item needs attention:' if len(issues)==1 else '%d items need attention:'%len(issues)))
    for issue in issues:print('  - '+issue,flush=True)


def platform_status(instance):
    """Print a read-only report, from machines to operations and attention points."""
    issues=[];help_lines=[];logs=[]
    print('---------------------- kk status ----------------------',flush=True)
    controller_rows=[('TARGET','STATE')];active=[];causes=[];reachable=[];switch_times=[]
    for box in promote_peers(instance,'ops'):
        try:
            state=remote('smith',box+'-ops',['/usr/local/sbin/klokast-controller-guard','--status','--json'])
            if not isinstance(state,dict) or state.get('hostname')!=box+'-ops':raise RuntimeError('invalid controller status')
            if state.get('active') and state.get('role')=='active':active.append(box)
            controller_rows.append((box+'-ops',str(state.get('role'))));reachable.append(box)
        except (RuntimeError,subprocess.SubprocessError) as error:
            issues.append(box+'-ops is unreachable.')
            controller_rows.append((box+'-ops','unreachable'));causes.append(box+'-ops is unreachable. Cause: '+str(error)[-300:])
    section('Machines')
    drift=None
    if len(active)!=1:bullet('Not shown: no single active controller.')
    else:
        try:
            report=remote('smith',active[0]+'-ops',[REMOTE_REPO+'/ansible/bin/machine-status','list'],timeout=180)
            issues+=show_machines(report);drift=report.get('drift')
            if report.get('drift_error'):
                issues.append('The running VMs and containers are not compared with the declaration.')
                bullet('Not compared with the declaration: '+str(report['drift_error'])[-300:])
        except (RuntimeError,subprocess.SubprocessError,TypeError,AttributeError) as error:
            issues.append('The machine list is not shown.');bullet('Not shown: '+str(error)[-300:])
    section('Credentials')
    for box in promote_peers(instance,'ops'):
        print('\n## '+box+'-ops\n',flush=True)
        if box not in reachable:bullet('Not shown: the controller is unreachable.');continue
        try:
            value=controller_setup.report(box+'-ops')
            rows=[(controller_setup.mark(entry),controller_setup.LABELS.get(entry['id'],entry['id']),entry['detail'])
                  for entry in value['items']]
            if rows:table([('STATE','CREDENTIAL','DETAIL'),*rows])
            if not value['ready']:
                issues.append(box+'-ops misses a required credential, so it cannot become the active controller.')
                bullet('Not ready for a controller switch.')
                help_lines.append('`kk setup --controller '+box+'-ops` for missing credentials')
        except controller_setup.SetupError as error:
            issues.append('The credentials of '+box+'-ops are not shown.');bullet('Not shown: '+str(error)[-300:])
    section('Applications')
    show_applications(instance)
    issues+=show_containers(drift)
    section('Controllers')
    table(controller_rows)
    for cause in causes:bullet(cause)
    if len(active)!=1:
        issues.append('Exactly one active controller is required; %d found.'%len(active))
        bullet('Exactly one active controller is required; %d found. Reconcile controller authority.'%len(active))
    else:issues+=show_instance_source(instance,active[0])
    for box in reachable:
        try:
            value=remote('smith',box+'-ops',[REMOTE_REPO+'/ansible/bin/promote-status','--last-switch'],timeout=60)
            if not isinstance(value,dict) or 'completed_at' not in value or (value.get('completed_at') is not None and type(value['completed_at']) is not int):
                raise RuntimeError('invalid controller switch time')
            if value.get('completed_at') is not None:switch_times.append(value['completed_at'])
        except (RuntimeError,subprocess.SubprocessError) as error:
            issues.append('The controller switch history of '+box+'-ops is not available.')
            bullet('Switch history unavailable on '+box+'-ops: '+str(error)[-300:])
    bullet('Last controller switch: '+(utc(max(switch_times)) if switch_times else 'unknown'))
    section('Runners')
    try:runners=promote_peers(instance,'air')
    except ValueError as error:runners=[];issues.append('The runners are not shown.');bullet('Not shown: '+str(error))
    rows=[('TARGET','STATE','WORKSPACE','CLIENTS','WORK','DETAIL')]
    for box in runners:
        status=air.probe(box)
        if status.get('error'):
            issues.append(box+'-air is unreachable.')
            rows.append((box+'-air','unreachable','unknown','-','-',status['error']));continue
        placement=status.get('placement') or {}
        workspace=(('active' if placement.get('current')==box else 'standby') if placement.get('phase')=='ready' else
                   'moving' if placement else 'present' if status['workspace'] else 'none')
        detail=[]
        if not status['available']:
            detail.append(air.unavailable_reason(status));issues.append(box+'-air is unavailable: '+detail[-1]+'.')
        if workspace=='moving':
            detail.append('workspace move '+str(placement.get('operation'))+' is in phase '+str(placement.get('phase')))
            issues.append('The workspace move to '+box+'-air is unfinished.')
        if status.get('pending'):detail.append('replacement pending, operation '+str((status.get('reservation') or {}).get('operation','unknown')))
        elif status.get('reservation') if placement else status['maintenance']:detail.append('maintenance reservation is active')
        rows.append((box+'-air','available' if status['available'] else 'unavailable',workspace,str(status['attached']),
                     str(len(status['busy_pids'])),'; '.join(detail)))
    if runners:table(rows)
    section('Promotions')
    rows,notes=promote_operations(instance)
    if rows:table([('TARGET','OPERATION','STARTED','PROGRESS','STATE','DETAIL'),*rows])
    else:bullet('No unfinished promote operation.')
    for note in notes:bullet(note);issues.append('The promote records of '+note.split(':',1)[0]+' are not shown.')
    issues+=['The promotion of '+r[0]+' (operation '+r[1]+') is '+r[4]+'.' for r in rows if r[4]!='running']
    help_lines+=['`kk promote '+r[0]+' --resume '+r[1]+'` to resume' for r in rows if r[0]!='unknown' and r[4]!='running']
    section('Updates')
    rows=[];notes=[];runs={}
    if len(active)!=1:notes.append('Jobs not shown: no single active controller.')
    else:
        try:
            jobs=remote('smith',active[0]+'-ops',[REMOTE_REPO+'/ansible/bin/vm-image-update','list'],timeout=60)
            if not isinstance(jobs,list) or any(not isinstance(j,dict) for j in jobs):raise RuntimeError('invalid job list')
            for job in jobs:
                target,operation,state=(str(job.get(k)) for k in ('target','operation','state'))
                work='reboot' if job.get('reboot') else 'update'
                rows.append((target,utc(job.get('created_at')),'-',
                    ' - '.join(v for v in (state,operation,str(job.get('detail') or '')) if v)))
                if state in ('failed','interrupted'):
                    issues.append('The '+target+' '+work+' is '+state+(': '+str(job['detail'])[-200:] if job.get('detail') else '.'))
                    help_lines.append('`kk '+work+' '+target+'` to restart job '+operation)
        except (RuntimeError,subprocess.SubprocessError) as error:
            issues.append('The update jobs are not shown.');notes.append('Jobs not shown: '+str(error)[-300:])
    when=lambda v:v[:16].replace('T',' ') if isinstance(v,str) else '-'
    for box in reachable:
        try:
            run=remote('smith',box+'-ops',[REMOTE_REPO+'/ansible/bin/ops-controller-nightly','--status'],timeout=60)
            if not isinstance(run,dict) or not isinstance(run.get('state'),str):raise RuntimeError('invalid nightly run record')
        except (RuntimeError,subprocess.SubprocessError) as error:
            issues.append('The scheduled update of '+box+'-ops is not shown.')
            notes.append('Nightly run not shown for '+box+'-ops: '+str(error)[-300:]);continue
        runs[box]=run
        detail=str(run.get('error') or run.get('reason') or ('No scheduled run recorded.' if run['state']=='none' else ''))
        rows.append((box+'-ops',when(run.get('started')),when(run.get('finished')),
                     run['state']+(' - '+detail if detail else '')))
        if run['state'] in ('failed','interrupted'):
            issues.append('The scheduled update of '+box+'-ops '+run['state']+(': '+detail[-200:] if detail else '.'))
            if run.get('log'):logs.append(box+'-ops:'+str(run['log']))
    if rows:table([('TARGET','STARTED','FINISHED','STATUS'),*rows])
    else:bullet('No update recorded.')
    for note in notes:bullet(note)
    if len(active)==1 and active[0] in runs:issues+=show_alternation(runs[active[0]],active[0])
    section('Help')
    bullet('`kk status TARGET` for details')
    for line in help_lines:bullet(line)
    if logs:
        bullet('Relevant full logs:')
        for log in logs:print('  - `'+log+'`')
    summary(issues)
    report_footer()
    return 1 if issues else 0


def show_instance_source(instance,box):
    """Compare the Instance of the active controller with the local one. Return the problems found."""
    path=Path(instance).expanduser()/'klokast-instance.json'
    published=hashlib.sha256(path.read_bytes()).hexdigest()
    commit=subprocess.run(['git','-C',str(path.parent),'rev-parse','--short=12','HEAD'],capture_output=True,text=True)
    local='your Instance checkout'+(' (commit '+commit.stdout.strip()+')' if commit.returncode==0 else '')
    try:
        used=remote('smith',box+'-ops',['/usr/local/sbin/platform-source','controllers'],timeout=120).get('instance_sha256')
    except (RuntimeError,subprocess.SubprocessError,AttributeError) as error:
        bullet('Instance: not compared. '+box+'-ops did not report its Instance. Cause: '+str(error)[-300:])
        return [box+'-ops did not report its Instance.']
    if used==published:
        bullet('Instance: '+box+'-ops uses the same desired state as '+local+'.');return []
    bullet('Instance: '+box+'-ops uses another desired state than '+local+', so the reports can be out of date. '
           'Run git pull in your Instance checkout. If they still differ, update the controller copy: '
           'git -C /home/smith/private/klokast/instance pull --ff-only')
    return [box+'-ops uses another Instance than your checkout.']


def show_alternation(run,box):
    """Print the controller switch and the standby update of one nightly run. Return the problems found."""
    switch=run.get('alternation') if isinstance(run.get('alternation'),dict) else {}
    if run.get('after_switch'):
        bullet('Controller switch before this run: complete (operation '+str(run['after_switch'])+'); it made '+box+'-ops active.')
    elif switch.get('state') in ('not due','due'):
        bullet('Controller switch before this run: '+switch['state']+' ('+str(switch.get('reason'))+').')
    elif switch.get('state')=='abandoned':
        bullet('Controller switch before this run: abandoned; '+box+'-ops stayed active. Cause: '+str(switch.get('error'))[-300:])
    if run.get('standby') and run.get('standby_update'):
        bullet('Standby update of '+str(run['standby'])+'-ops: '+str(run['standby_update'])+
              ('. The next scheduled run can make it active first.' if run['standby_update']=='replaced' else '.'))
    return ['The controller switch before the scheduled run was abandoned.'] if switch.get('state')=='abandoned' else []


def applications(instance):
    """List the Instance applications as (name, declared state, client is available)."""
    apps=air.read_json(Path(instance).expanduser()/'klokast-instance.json').get('apps',{})
    if not isinstance(apps,dict):raise ValueError('Instance apps must be an object')
    rows=[]
    for name in sorted(apps):
        state=apps[name].get('desired-state') if isinstance(apps[name],dict) else None
        if not re.fullmatch('[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',name) or state not in ('present','absent'):
            raise ValueError('Instance contains an invalid application binding')
        rows.append((name,state,state=='present' and
                     (Path(__file__).resolve().parents[2]/'apps'/name/'bin'/(name+'-client')).is_file()))
    return rows


def application_name(name):
    """Use the dedicated VM suffix when the application has one VM resource."""
    import yaml
    path=Path(__file__).resolve().parents[2]/'apps'/name/'platform-resources.yml'
    if not path.is_file():return name
    manifest=yaml.safe_load(path.read_text())
    guests=[r.get('hostname_suffix',r['id']) for r in manifest.get('resources',{}).get('compute',[])
            if r.get('type')=='app_vm']
    return guests[0] if len(guests)==1 else name


def show_applications(instance):
    """Show declared placement for each application, with one column per box."""
    value=air.read_json(Path(instance).expanduser()/'klokast-instance.json')
    boxes=declared_boxes(instance);rows=[]
    for name,state,client in applications(instance):
        placement=value['apps'][name].get('placement') or {}
        present=set()
        if state=='present':
            if placement.get('mode')=='multi-box':present=set(placement.get('boxes',[]))
            elif placement.get('mode')=='single-box':present={placement.get('box')}
            elif placement.get('mode')=='active-passive':present={placement.get('active')}
        states=['present' if box in present else
                'passive' if state=='present' and placement.get('mode')=='active-passive' and box==placement.get('passive') else
                'absent' for box in boxes]
        rows.append((application_name(name),*states,'kk app '+name if client else '-'))
    if not rows:bullet('No declared application.');return
    table([('NAME',*boxes,'COMMAND'),(),*sorted(rows)])
    print(flush=True)
    bullet('The box columns show the placement that the Instance declares: present, passive (the standby '
           'copy of an active-passive application) or absent. They do not test the running service.')
    bullet('COMMAND is the MacBook command of the application; - means that it has none.')


def show_containers(drift):
    """Point out the containers that run but that no declared application lists. Return the problems."""
    if not isinstance(drift,dict):
        bullet('The running containers are not compared with the declaration.');return []
    found=['%s runs the container %s, which no declared application lists.'%(c.get('target'),c.get('container'))
           for c in drift.get('containers') or []]
    for line in found:bullet(line)
    if not found:bullet('Each running container belongs to a declared application.')
    return found


def show_machines(report):
    """Print one line for each declared machine. Return the problems found."""
    machines=report.get('machines')
    if not isinstance(machines,list) or any(not isinstance(m,dict) for m in machines):raise RuntimeError('invalid machine list')
    groups={}
    for m in machines:
        role=m.get('role',str(m.get('target')).rsplit('-',1)[-1])
        groups.setdefault(m.get('profile',role),[]).append(m)
    def group_order(profile):
        counts=[m['packages'] for m in groups[profile] if type(m.get('packages')) is int]
        return (min(counts) if counts else float('inf'),profile)
    rows=[('TARGET','STATE','LAST UPDATE','ALPINE','PACKAGES')]
    for profile in sorted(groups,key=group_order):
        rows.append(())
        for m in sorted(groups[profile],key=lambda m:(m.get('role',''),str(m.get('target')))):
            rows.append((str(m.get('target')),str(m.get('state')),utc(m.get('updated_at')) if m.get('state')=='reachable' else '-',
                         str(m.get('alpine','-')),str(m.get('packages','-'))))
    table(rows)
    failed=[m for m in machines if m.get('state')=='unreachable']
    for m in failed:bullet(str(m.get('target'))+' is unreachable. Cause: '+str(m.get('error'))[-300:])
    # An application that the Instance does not declare shows only as such VMs or containers.
    undeclared=['%s-dom0 runs the VM %s, which the Platform does not declare.'%(v.get('box'),v.get('guest'))
                for v in (report.get('drift') or {}).get('vms') or []]
    for line in undeclared:bullet(line)
    return [str(m.get('target'))+' is unreachable ('+str(m.get('error'))[-200:]+').' for m in failed]+undeclared


def target_status(instance,target):
    """Print the details of one application or one machine."""
    print('---------------------- kk status ----------------------',flush=True)
    for name,state,client in applications(instance):
        if target not in (name,application_name(name)):continue
        section('Application')
        bullet('Application: '+name);bullet('Declared state: '+state)
        bullet('MacBook command: '+('kk app '+name if client else 'none'))
        if client:
            section('Help');bullet('`kk app '+name+' --help` for commands')
        summary([])
        report_footer()
        return 0
    if not re.fullmatch('[a-z][a-z0-9-]{0,62}',target):raise ValueError('status needs a machine or application name. '+LIST_HINT)
    active=controller(instance)
    machine=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/machine-status','show','--target',target],timeout=120)
    if not isinstance(machine,dict):raise RuntimeError('invalid machine status')
    state=machine.get('state');issues=[target+' is unreachable.'] if state=='unreachable' else [];help_lines=[]
    section('Machine')
    for line in ('Machine: '+target,'Declared state: '+str(machine.get('declared')),'State: '+str(state)):bullet(line)
    if state=='unreachable':bullet('Cause: '+str(machine.get('error'))[-300:])
    packages=machine.get('packages') if isinstance(machine.get('packages'),list) else []
    if state=='reachable':
        bullet('Last update: '+utc(machine.get('updated_at')))
        if machine.get('image_built_at') is not None:bullet('Image built: '+utc(machine['image_built_at']))
        bullet('Last boot: '+utc(machine.get('booted_at')))
        bullet('Alpine release: '+str(machine.get('alpine')));bullet('Packages: %d'%len(packages))
    if packages:
        section('Packages')
        table([('PACKAGE','VERSION','BUILT'),*[(str(p.get('name')),str(p.get('version')),utc(p.get('built_at'))) for p in packages]])
    role=next((r for r in ('dom0',)+ROLES if target.endswith('-'+r)),None)
    if role:
        section('Updates')
        job=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/vm-image-update','status','--box',target[:-len(role)-1],'--role',role],timeout=120)
        if job.get('operation'):bullet('Operation: '+str(job['operation']))
        checks=job.get('checks') or [];times=job.get('check_times') or {}
        for check in checks:bullet(utc(times.get(check),full=True)+': '+str(check))
        published=job.get('state')=='updating' and job.get('progress')
        at=utc(job.get('progress_at' if published else 'changed_at'),full=True)
        for line in [describe(job,target),*warnings(job)]:bullet(at+': '+line)
        if job.get('state') in ('failed','interrupted'):
            issues.append('The '+target+' update is '+str(job['state'])+'.')
            if job.get('operation'):
                help_lines.append('`kk '+('reboot' if job.get('reboot') else 'update')+' '+target+'` to restart job '+
                                  str(job['operation']))
    if help_lines:
        section('Help')
        for line in help_lines:bullet(line)
    summary(issues)
    report_footer()
    return 1 if issues else 0


def describe(record,target):
    state=record.get('state','unknown')
    if state=='absent':return 'No update job exists for '+target
    if state=='failed':return 'Failed: '+str(record.get('error') or 'no error detail')
    if state=='cancelled':return 'Cancelled: '+str(record.get('reason') or 'no detail')
    # A running job shows only its stage; the other states name themselves.
    detail=str(record.get('progress') if state=='updating' and record.get('progress') else record.get('reason') or 'no detail')
    text=detail[:1].upper()+detail[1:] if state in ('updating','complete') else state.capitalize()+': '+detail
    if state=='waiting' and (record.get('attached') or record.get('busy_processes')):
        text+='; attached clients: %s, busy processes: %s. Detach from %s and let its work finish'%(
            record.get('attached',0),record.get('busy_processes',0),target)
    result=record.get('result')
    if state=='complete' and isinstance(result,dict) and result.get('image'):text+='; image '+str(result['image'])
    return text


def warnings(record):
    """Return the warnings of a complete job. Each warning is a separate event."""
    result=record.get('result')
    if record.get('state')!='complete' or not isinstance(result,dict):return []
    return ['Warning: '+str(warning) for warning in result.get('warnings') or []]


def say(text,*,stream=None):
    print(time.strftime('%H:%M:%S ',time.gmtime())+text,file=stream or sys.stdout,flush=True)


def say_at(at,text):
    """Print a line with the time that the controller recorded for it."""
    if type(at) not in (int,float):print('--:--:-- '+text,flush=True);return
    # A job can continue after midnight; then the time of day is not sufficient.
    day=time.strftime('%Y-%m-%d ',time.gmtime(at))
    print(('' if day==time.strftime('%Y-%m-%d ',time.gmtime()) else day)+time.strftime('%H:%M:%S ',time.gmtime(at))+text,flush=True)


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
    at=record.get('progress_at' if published else 'changed_at')
    for line in [text,*warnings(record)]:say_at(at,line)


def show_stages(record,shown):
    """Print the recent job stages that were not printed yet. Return the printed stages and if one is new."""
    stages=[(at,str(text)) for at,text in record.get('stages') or [] if record.get('state')=='updating']
    new=[stage for stage in stages if stage not in shown]
    for at,text in new:say_at(at,text[:1].upper()+text[1:])
    return shown|set(new),bool(new)


def follow(active,target,operation,record,box,role):
    """Print each job stage until the controller job ends."""
    argv=[REMOTE_REPO+'/ansible/bin/vm-image-update','status','--box',box,'--role',role,'--operation',operation]
    checks=show_checks(record);last=describe(record,target);shown=entered=time.monotonic();failures=0
    # A controller older than the stage list publishes only the current stage.
    stages,new=show_stages(record,set())
    if not new:show_stage(record,last)
    while record.get('state') in ('waiting','updating'):
        time.sleep(POLL_SECONDS)
        try:record=remote('smith',active+'-ops',argv,timeout=120);failures=0
        except (RuntimeError,subprocess.SubprocessError) as error:
            failures+=1
            if failures>=5:raise RuntimeError('lost contact with '+active+'-ops; the job continues there. Check it: kk status '+target) from error
            say('status check failed, retrying: '+str(error)[-300:],stream=sys.stderr);continue
        checks=show_checks(record,checks);text=describe(record,target);stages,new=show_stages(record,stages)
        if new:last=text;shown=entered=time.monotonic()
        elif text!=last:show_stage(record,text);last=text;shown=entered=time.monotonic()
        elif time.monotonic()-shown>=REPEAT_SECONDS:
            # A long stage still prints a line, so a stalled terminal is visible.
            say(text+' (%d min in this stage)'%((time.monotonic()-entered)//60));shown=time.monotonic()
    return 1 if record.get('state')!='complete' else 0


def announce(record,target,active,work):
    """Print one line that names the selected job; the command to follow it again ends the line."""
    operation=str(record.get('operation'))
    if record.get('request')=='joined':head='Joined the running '+work+' job '+operation+' of '+target
    elif record.get('request')=='restarted':
        head='Restarting the '+str(record.get('previous') or 'failed')+' '+work+' job '+operation+' of '+target
    else:head=('Rebooting ' if work=='reboot' else 'Updating ')+target+'; job '+operation
    text=head+' on '+active+'-ops. Ctrl-C or closing this terminal does not stop the '+work+'. Follow it again: kk '+work+' '+target
    if type(record.get('requested_at')) in (int,float):say_at(record['requested_at'],text)
    else:say(text)


def ended(code,target,work):
    if code:print('Inspect: kk status '+target+'. Restart the same job: kk '+work+' '+target,flush=True)
    return code


def dom0_update(args,box,reboot=False):
    # A reboot uses the same checks and job, without a new boot generation.
    target=box+'-dom0';work='reboot' if reboot else 'update'
    if args.cancel:return cancel_update(args.instance,target,box,'dom0','kk '+work+' '+target)
    active=controller(args.instance)
    # The controller selects the job. A running job, or a failed job of the same
    # kind, continues; a reboot can have already stopped the runner, and the
    # job checks it while the reboot has not started.
    prior=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/vm-image-update','status','--box',box,'--role','dom0'],timeout=120)
    if not (prior.get('state') in ('waiting','updating') or prior.get('state')=='failed' and bool(prior.get('reboot'))==reboot):
        # The reboot stops only the runner of this box.
        runner=box in air.declarations(args.instance)[1]
        say('Checking that no agent session is open on '+box+'-air.' if runner else 'Checking that '+target+' can be '+work+'ed.')
        remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/platform-update','dom0-preflight','--box',box],timeout=240)
        say('All checks passed.')
    if active==box:
        peers=[peer for peer in declared_controllers(args.instance) if peer!=box]
        if len(peers)!=1:raise ValueError('dom0 update needs another declared controller')
        destination=peers[0];switch=secrets.token_hex(12)
        say('Switching the active controller to '+destination+'-ops; operation '+switch+
            '. If interrupted, inspect kk promote --status before retrying.')
        value=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/ops-controller-ha','switchover',
            '--old-active',active,'--new-active',destination,'--operation-id',switch],
            timeout=1810,last_json=True,show=True)
        if value.get('state')!='complete' or value.get('active')!=destination:
            raise RuntimeError('controller switch is incomplete; inspect kk promote --status')
        active=controller(args.instance)
        if active!=destination:raise RuntimeError('promoted controller authority could not be verified')
    record=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/vm-image-update','start','--box',box,'--role','dom0',
                                         *(['--reboot'] if reboot else [])],timeout=120)
    announce(record,target,active,work)
    if args.detach:
        show_checks(record);show_stage(record,describe(record,target))
        return 1 if record.get('state')=='failed' else 0
    try:code=follow(active,target,str(record.get('operation')),record,box,'dom0')
    except KeyboardInterrupt:
        print('\nStopped following. The '+work+' continues on '+active+'-ops. Check it: kk status '+target,flush=True);return 130
    return ended(code,target,work)


def dom0_reboot(args):
    box,role=update_target(args.instance,args.target)
    if role!='dom0':raise ValueError('kk reboot accepts only a BOX-dom0 target. To replace a VM, use: kk update '+args.target)
    return dom0_update(args,box,reboot=True)


def image_update(args):
    box,role=update_target(args.instance,args.target)
    if role=='dom0':return dom0_update(args,box)
    if getattr(args,'cancel',False):return cancel_update(args.instance,args.target,box,role)
    active=controller(args.instance)
    record=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/vm-image-update','start','--box',box,'--role',role],timeout=120)
    announce(record,args.target,active,'update')
    if args.detach:
        show_checks(record);show_stage(record,describe(record,args.target));print('Check progress: kk status '+args.target,flush=True)
        return 1 if record.get('state')=='failed' else 0
    try:code=follow(active,args.target,str(record.get('operation')),record,box,role)
    except KeyboardInterrupt:
        print('\nStopped following. The job continues. Check it: kk status '+args.target,flush=True);return 130
    return ended(code,args.target,'update')


def cancel_update(instance,target,box,role,command=None):
    command=command or 'kk update '+target
    print('Finding the active controller...',flush=True)
    active=controller(instance)
    print('Cancelling the waiting '+target+' job on '+active+'-ops...',flush=True)
    record=remote('smith',active+'-ops',[REMOTE_REPO+'/ansible/bin/vm-image-update','cancel','--box',box,'--role',role],timeout=240)
    if not isinstance(record,dict):raise RuntimeError('invalid cancel result')
    print('Update operation: '+str(record.get('operation')),flush=True)
    show_stage(record,describe(record,target))
    print('Request it again: '+command,flush=True)
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance',default=os.environ.get('KLOKAST_INSTANCE',''))
    sub=parser.add_subparsers(dest='action',required=True)
    u=sub.add_parser('update',help='request a safe VM image replacement; join the running job of BOX-ROLE or restart its failed job')
    u.prog='kk update'
    u.add_argument('target',metavar='BOX-ROLE');g=u.add_mutually_exclusive_group()
    g.add_argument('--detach',action='store_true',help='request the update and return without following progress')
    g.add_argument('--cancel',action='store_true',help='stop the job of BOX-ROLE while it still waits; a job that changes the VM continues')
    r=sub.add_parser('reboot',help='reboot one dom0 on its accepted boot generation and verify it, like an update; join or restart its unfinished reboot job')
    r.prog='kk reboot'
    r.add_argument('target',metavar='BOX-dom0');g=r.add_mutually_exclusive_group()
    g.add_argument('--detach',action='store_true',help='request the reboot and return without following progress')
    g.add_argument('--cancel',action='store_true',help='stop the job of BOX-dom0 while it still waits')
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
        if args.action=='reboot':return dom0_reboot(args)
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
        print('kk '+(args.action if args.action in ('update','reboot','promote','status') else 'platform')+': '+str(error),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
