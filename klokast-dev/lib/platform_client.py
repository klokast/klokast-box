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


def remote(user,host,argv,*,data=None,timeout=90,last_json=False):
    result=subprocess.run([*air.SSH,user+'@'+host,shlex.join(argv)],input=data,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if result.returncode:
        raise RuntimeError(host+': '+result.stderr.decode(errors='replace').strip()[-1500:])
    try:return json.loads(result.stdout.strip().splitlines()[-1] if last_json else result.stdout)
    except ValueError:raise RuntimeError(host+' returned an invalid operation result') from None


def controller(instance):
    value=air.read_json(Path(instance)/'klokast-instance.json')
    controllers=value.get('controllers',{})
    candidates=[controllers.get('active'),controllers.get('standby')]
    if any(not isinstance(b,str) or not air.BOX.fullmatch(b) for b in candidates):
        raise ValueError('Instance must declare active and standby controllers')
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
ACTIVE_NOTE='active controller; switch the controllers before an update (command below)'
LIST_HINT='List the accepted targets with: kk update --list'
LOCK_STAGE='check that no other update work is ongoing (shared Platform update lock)'
STAGES=(LOCK_STAGE,'prepare the image, then replace, boot and verify the VM when the image changed')
AIR_STAGES=('check that the runner tmux sessions are detached and its discussions are not active',LOCK_STAGE,
            'prepare, boot and verify the replacement image, then restore the tmux workspace')


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


def update_targets(instance):
    """List the Instance-declared targets as (target, state, note).

    This is an offline preview of the controller target check, which remains
    the only selection authority for a request.
    """
    if not instance:raise ValueError('select a private Instance worktree with --instance PATH or KLOKAST_INSTANCE')
    value=air.read_json(Path(instance).expanduser()/'klokast-instance.json')
    boxes,controllers,runners=value.get('boxes'),value.get('controllers'),value.get('airunners')
    if not isinstance(boxes,dict) or not isinstance(controllers,dict) or not isinstance(runners,list):
        raise ValueError('Instance needs boxes, controllers and airunners; run klokast check on the Instance')
    rows=[]
    for box in sorted(boxes):
        declared=boxes[box]
        if not isinstance(declared,dict):raise ValueError('Instance box '+box+' must be an object')
        for role in sorted(ROLES):
            state,note='running',''
            if role=='ops':
                if controllers.get('active')==box:note=ACTIVE_NOTE
                elif controllers.get('standby')!=box:continue
            elif role=='air':
                if box+'-air' not in runners:continue
            elif role=='vpn-egress':
                if not declared.get('vpn-egress'):continue
            else:
                state=declared.get('substrate',{}).get('shared-guests',{}).get(role,{}).get('runtime-state','running')
                if state=='stopped':note='declared stopped; change Instance before an update'
            rows.append((box+'-'+role,str(state),note))
    return rows


def list_targets(instance):
    rows=[('TARGET','DECLARED','NOTE'),*update_targets(instance)]
    widths=[max(len(r[i]) for r in rows) for i in (0,1)]
    for target,state,note in rows:print((target.ljust(widths[0])+'  '+state.ljust(widths[1])+'  '+note).rstrip())
    controllers={note==ACTIVE_NOTE:target[:-4] for target,_,note in rows[1:] if target.endswith('-ops')}
    if True in controllers and False in controllers:
        active,standby=controllers[True],controllers[False]
        print('\nTo update '+active+'-ops, first make '+standby+' the active controller:\n  '+shlex.join(
            ['ssh','smith@'+active+'-ops',REMOTE_REPO+'/ansible/bin/ops-controller-ha','switchover',
             '--old-active',active,'--new-active',standby])+
            '\nThen run git pull in the Instance worktree; the switch publishes the new placement.')
    elif True in controllers:
        print('\n'+controllers[True]+'-ops has no declared standby controller; a controller switch is not possible.')
    print('\nSource: the selected Instance worktree. Synchronize it with git pull.\n'
          'Read the live job state of one target with: kk update BOX-ROLE --status',flush=True)
    return 0


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


def show_checks(record,shown=()):
    """Print the passed checks that were not printed yet."""
    checks=[str(c) for c in record.get('checks') or []]
    for check in checks:
        if check not in shown:say(check)
    return set(shown)|set(checks)


def follow(active,target,operation,record,box,role):
    """Print each job stage until the controller job ends."""
    argv=[REMOTE_REPO+'/ansible/bin/vm-image-update','status','--box',box,'--role',role,'--operation',operation]
    checks=show_checks(record);last=describe(record,target);shown=time.monotonic();failures=0;say(last)
    while record.get('state') in ('waiting','updating'):
        time.sleep(POLL_SECONDS)
        try:record=remote('smith',active+'-ops',argv,timeout=120);failures=0
        except (RuntimeError,subprocess.SubprocessError) as error:
            failures+=1
            if failures>=5:raise RuntimeError('lost contact with '+active+'-ops; the job continues there. Check it: kk update '+target+' --status') from error
            say('status check failed, retrying: '+str(error)[-300:],stream=sys.stderr);continue
        checks=show_checks(record,checks);text=describe(record,target)
        if text!=last:say(text);last=text;shown=time.monotonic()
        elif time.monotonic()-shown>=REPEAT_SECONDS:say('still '+text);shown=time.monotonic()
    return 1 if record.get('state')!='complete' else 0


def image_update(args):
    box,role=update_target(args.instance,args.target)
    if args.resume and not re.fullmatch('[0-9a-f]{24}',args.resume):raise ValueError('invalid update operation ID')
    print('Finding the active controller...',flush=True)
    active=controller(args.instance)
    requested=operation=args.resume or (None if args.status else secrets.token_hex(12))
    argv=[REMOTE_REPO+'/ansible/bin/vm-image-update','status' if args.status else 'start','--box',box,'--role',role]
    if operation:argv+=['--operation',operation]
    print(('Reading update job status' if args.status else 'Requesting the '+args.target+' image update')+' from '+active+'-ops...',flush=True)
    result=remote('smith',active+'-ops',argv,timeout=120)
    operation=result.get('operation') or operation
    if args.status:
        if operation:print('Update operation: '+operation,flush=True)
        show_checks(result);say(describe(result,args.target))
        return 1 if result.get('state')=='failed' else 0
    if operation!=requested:print('Joined the existing update job for '+args.target+'.',flush=True)
    print('Update operation: '+operation+'\nResume: kk update '+args.target+' --resume '+operation+
          '\nThe controller will:\n'+''.join('  %d. %s\n'%(i,s) for i,s in enumerate(AIR_STAGES if role=='air' else STAGES,1))+
          'The job runs on '+active+'-ops. It continues if this command stops or the MacBook sleeps.',flush=True)
    if args.detach:
        show_checks(result);say(describe(result,args.target));print('Check progress: kk update '+args.target+' --status',flush=True)
        return 1 if result.get('state')=='failed' else 0
    print('Following progress. Press Ctrl-C to stop following.',flush=True)
    try:code=follow(active,args.target,operation,result,box,role)
    except KeyboardInterrupt:
        print('\nStopped following. The job continues. Check it: kk update '+args.target+' --status',flush=True);return 130
    if code:print('Inspect: kk update '+args.target+' --status. Retry the same job: kk update '+args.target+' --resume '+operation,flush=True)
    return code


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance',default=os.environ.get('KLOKAST_INSTANCE',''))
    sub=parser.add_subparsers(dest='action',required=True)
    u=sub.add_parser('update',help='request a safe VM image replacement')
    u.prog='kk update'
    u.add_argument('target',nargs='?',metavar='BOX-ROLE');g=u.add_mutually_exclusive_group();g.add_argument('--status',action='store_true');g.add_argument('--resume')
    g.add_argument('--list',action='store_true',help='list the targets declared in the Instance and exit')
    u.add_argument('--detach',action='store_true',help='request the update and return without following progress')
    p=sub.add_parser('handoff',help='relay only one discussion through this device')
    p.add_argument('--session');p.add_argument('--from',dest='origin');p.add_argument('--to',dest='destination')
    p.add_argument('--resume',help='resume a bounded handoff operation')
    p=sub.add_parser('maintain',help='handoff, safely replace selected roles, and optionally return')
    p.add_argument('--box');p.add_argument('--roles',default='air,ops');p.add_argument('--session')
    p.add_argument('--return',dest='return_session',action='store_true')
    p.add_argument('--resume',help='resume a bounded maintenance operation')
    args=parser.parse_args(argv)
    if args.action=='update':
        if args.list and (args.target or args.detach):u.error('--list accepts no target and no --detach')
        if not args.list and not args.target:u.error('a BOX-ROLE target is required. '+LIST_HINT)
    try:
        if args.action=='update':return list_targets(args.instance) if args.list else image_update(args)
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
        print('kk platform: '+str(error),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
