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


def remote(user,host,argv,*,data=None,timeout=90):
    result=subprocess.run([*air.SSH,user+'@'+host,shlex.join(argv)],input=data,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if result.returncode:
        raise RuntimeError(host+': '+result.stderr.decode(errors='replace').strip()[-1500:])
    try:return json.loads(result.stdout)
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


def handoff(record,path,instance,suffix,*,attach=True):
    origin,destination,session,operation=(record[k] for k in ('from','to','session','operation'))
    authorize(instance,origin,destination,session)
    if record['phase']=='prepared':
        metadata=remote('agent',origin+'-air',[HELPER,'inspect','--session',session])
        record['cwd']=metadata['cwd'];atomic(path,record)
        remote('agent',destination+'-air',[HELPER,'ready','--workspace',record['cwd']],timeout=120)
        record['phase']='exporting';atomic(path,record)
    if record['phase']=='exporting':
        # The runner retains the durable export. The Mac has only a transient
        # in-memory relay; no archive, auth, repository or credential is copied.
        remote('agent',destination+'-air',[HELPER,'ready','--workspace',record['cwd']],timeout=120)
        print('Waiting for the selected source writer to close; operation '+operation,flush=True)
        bundle=remote('agent',origin+'-air',[HELPER,'export','--session',session,'--operation',operation,'--wait','300'],timeout=390)
        remote('agent',destination+'-air',[HELPER,'import','--operation',operation],data=json.dumps(bundle).encode(),timeout=180)
        record['phase']='imported';atomic(path,record)
    if record['phase']=='imported':
        remote('agent',destination+'-air',[HELPER,'start','--session',session,'--operation',operation],timeout=90)
        record['phase']='complete';atomic(path,record)
    if record['phase']!='complete':raise RuntimeError('unknown handoff phase; inspect the device operation record')
    with air.settings(suffix) as settings:settings['last']=destination
    print('Discussion '+session+' resumed on '+destination+'-air; operation '+operation,flush=True)
    return air.attach(destination,True) if attach else 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance',default=os.environ.get('KLOKAST_INSTANCE',''))
    sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('handoff',help='relay only one discussion through this device')
    p.add_argument('--session');p.add_argument('--from',dest='origin');p.add_argument('--to',dest='destination')
    p.add_argument('--resume',help='resume a bounded handoff operation')
    args=parser.parse_args(argv)
    try:
        suffix,boxes=air.declarations(args.instance)
        directory=air.config_path().parent/'operations'/suffix
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (directory/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if args.resume:
                if not re.fullmatch('[0-9a-f]{24}',args.resume):raise ValueError('invalid operation ID')
                if any((args.session,args.origin,args.destination)):raise ValueError('--resume uses the saved discussion and runners')
                path=directory/(args.resume+'.json');record=air.read_json(path)
                if record['operation']!=args.resume or record['action']!=args.action:raise ValueError('operation does not match this command')
            else:
                if not args.session or str(uuid.UUID(args.session))!=args.session:raise ValueError('--session needs a canonical discussion UUID')
                if args.origin not in boxes or args.destination not in boxes or args.origin==args.destination:
                    raise ValueError('--from and --to must name different declared runners')
                for p in directory.glob('*.json'):
                    old=air.read_json(p)
                    if old['phase']!='complete':raise ValueError('resume unfinished operation '+old['operation'])
                operation=secrets.token_hex(12);path=directory/(operation+'.json')
                record=dict(action='handoff',operation=operation,phase='prepared',session=args.session,
                    **{'from':args.origin,'to':args.destination})
                atomic(path,record)
            print('Resume command: kk platform handoff --resume '+record['operation'],flush=True)
            # The operation lock covers transfer, not the terminal attach.
            handoff(record,path,args.instance,suffix,attach=False)
        return air.attach(record['to'],True)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as error:
        print('kk platform: '+str(error),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
