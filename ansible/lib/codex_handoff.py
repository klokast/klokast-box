#!/usr/bin/env python3
"""Bounded runner-side operations for one selected Codex discussion."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import shlex
import shutil
import socket
import subprocess
import sys
import time

from codex_discussion import Store, atomic, canonical, digest, identifier, validate_bundle, writers
import platform_proxy

HOME=Path('/home/agent/.codex')
ROOT=Path('/home/agent/.local/state/klokast/handoffs')
WORKSPACE=Path('/var/lib/klokast/air')


def codex_environment():
    return dict(os.environ, **platform_proxy.environment())


def codex_binary():
    # Match the managed interactive shell even when controller su or OpenSSH
    # starts a non-login command with the system-only PATH.
    path=str(HOME.parent/'.local/bin')+os.pathsep+os.environ.get('PATH','/usr/local/bin:/usr/bin:/bin')
    binary=shutil.which('codex',path=path)
    if not binary:raise RuntimeError('Codex is not installed for the runner account')
    return binary


class RPC:
    def __enter__(self):
        # The daemon proxy carries WebSocket bytes. Use the supported JSONL
        # stdio transport for these bounded, read-only account checks.
        self.process=subprocess.Popen([codex_binary(),'app-server','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,env=codex_environment())
        self.number=0
        try:self.call('initialize',{'clientInfo':{'name':'klokast-handoff','version':'1'},'capabilities':{'experimentalApi':True}})
        except BaseException:
            self.__exit__();raise
        self.process.stdin.write(b'{"method":"initialized"}\n');self.process.stdin.flush()
        return self

    def call(self,method,params):
        self.number+=1
        self.process.stdin.write((json.dumps({'id':self.number,'method':method,'params':params})+'\n').encode());self.process.stdin.flush()
        deadline=time.monotonic()+20
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout,selectors.EVENT_READ)
            while time.monotonic()<deadline:
                if not selector.select(max(0,deadline-time.monotonic())):break
                line=self.process.stdout.readline()
                if not line:break
                value=json.loads(line)
                if value.get('id')!=self.number:continue
                if 'error' in value:raise RuntimeError('Codex refused '+method+'; inspect the selected discussion')
                return value['result']
        raise RuntimeError('Codex control request timed out: '+method)

    def __exit__(self,*args):
        self.process.stdin.close()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=3)
        self.process.stdout.close()


def quiesce(ids,wait):
    # Archive is not conditional on idle in 0.163.0. Its shutdown can abort a
    # turn started after a status check. Wait for native shutdown instead.
    # An offline discussion needs no app-server and must not start one.
    deadline=time.monotonic()+wait
    while True:
        try:
            with writers(HOME,ids):return
        except RuntimeError:
            if time.monotonic()>=deadline:
                raise RuntimeError('selected discussion still has a native writer; let its turn and background work finish, close its Codex client, then resume this handoff') from None
            time.sleep(min(2,max(0,deadline-time.monotonic())))


def ready(workspace=None):
    binary=codex_binary()
    if subprocess.check_output([binary,'--version'],text=True,timeout=15).strip()!='codex-cli 0.163.0':
        raise RuntimeError('Codex version has no qualified discussion adapter; keep both copies')
    login=subprocess.run([binary,'login','status'],capture_output=True,text=True,timeout=20)
    if login.returncode:raise RuntimeError('destination needs its own OpenAI sign-in; run codex login --device-auth')
    with Store(HOME).databases():pass
    if workspace and (not Path(workspace).is_dir() or not Path(workspace).resolve().is_relative_to(Path('/home/agent'))):
        raise RuntimeError('bootstrap the selected discussion workspace on this runner before handoff')
    with RPC() as rpc:
        account=rpc.call('account/read',{'refreshToken':True})
        identity=account.get('account') or {}
        if identity.get('type')!='chatgpt' or not identity.get('email'):
            raise RuntimeError('runner needs its own ChatGPT account sign-in')
        rpc.call('account/rateLimits/read',{})
    fingerprint=hashlib.sha256(identity['email'].strip().casefold().encode()).hexdigest()
    return {'adapter':'0.163.0','free_bytes':shutil.disk_usage(HOME).free,'authenticated':True,
            'account_fingerprint':fingerprint}


def inspect(session):
    c=Store(HOME).connect('state_5.sqlite')
    try:
        Store(HOME).validate(c,'state_5.sqlite')
        row=c.execute('SELECT cwd FROM threads WHERE id=?',(session,)).fetchone()
        if not row:raise RuntimeError('selected source discussion is absent; resume its recorded handoff')
        return {'session':session,'cwd':row['cwd']}
    finally:c.close()


def export(session,operation,wait):
    store=Store(HOME);directory=ROOT/operation;receipt=directory/'source.json';backup=directory/'export.json'
    ledger=ROOT/'threads'/(session+'.json')
    if receipt.exists():
        prior=json.loads(receipt.read_text());bundle=json.loads(backup.read_text())
        if bundle['session']!=session or digest(bundle)!=prior['sha256']:raise RuntimeError('saved source export changed; reconcile both copies')
        moved=json.loads(ledger.read_text()) if ledger.exists() else None
        if moved and moved['operation']!=operation:raise RuntimeError('discussion has a later handoff; refuse the stale export')
    else:
        c=store.connect('state_5.sqlite')
        try:store.validate(c,'state_5.sqlite');ids=store.ids(c,session)
        finally:c.close()
        quiesce(ids,wait)
        with writers(HOME,ids):
            bundle=store.snapshot(session);validate_bundle(bundle)
            if shutil.disk_usage(ROOT).free<len(canonical(bundle))*2+64*1024*1024:raise RuntimeError('source lacks space for its retained discussion copy')
            atomic(backup,bundle)
            prior={'phase':'sealing','sha256':digest(bundle),'session':session};atomic(receipt,prior)
    with writers(HOME,bundle['threads']):
        store.remove(bundle)
        prior.update(phase='moved',threads=bundle['threads'],completed_at=int(time.time()));atomic(receipt,prior)
        atomic(ledger,{'operation':operation,'phase':'moved','sha256':prior['sha256']})
    return bundle


def receive(bundle,operation):
    validate_bundle(bundle);session=bundle['session'];ledger=ROOT/'threads'/(session+'.json')
    previous=json.loads(ledger.read_text()) if ledger.exists() else None
    if previous and previous['operation']==operation and previous['phase']=='moved':
        raise RuntimeError('this operation has already moved away; refuse an old import retry')
    if previous and previous['phase']=='moved':
        source=ROOT/previous['operation']/'export.json'
        if not source.is_file() or digest(json.loads(source.read_text()))!=previous['sha256']:
            raise RuntimeError('earlier source copy changed; return transfer requires reconciliation')
    if shutil.disk_usage(HOME).free<len(canonical(bundle))*2+64*1024*1024:raise RuntimeError('destination has insufficient free space')
    receipt=ROOT/operation/'destination.json'
    if receipt.exists():
        old=json.loads(receipt.read_text())
        if old['phase'] in ('imported','starting','started'):
            if old['sha256']!=digest(bundle) or previous!={'operation':operation,'phase':'here'}:
                raise RuntimeError('discussion has a later handoff; preserve its current state')
            return old
    with writers(HOME,bundle['threads']):
        value=Store(HOME).install(bundle,receipt)
        atomic(ledger,{'operation':operation,'phase':'here'})
    return value


def start(session,operation):
    ledger=json.loads((ROOT/'threads'/(session+'.json')).read_text())
    if ledger!={'operation':operation,'phase':'here'}:raise RuntimeError('discussion ownership moved; refuse this stale continuation')
    path=ROOT/operation/'destination.json';receipt=json.loads(path.read_text())
    if receipt['session']!=session:raise RuntimeError('destination receipt selects another discussion')
    window='handoff-'+operation
    windows=subprocess.run(['tmux','list-windows','-t','=main','-F','#{window_name}'],capture_output=True,text=True)
    exists=window in windows.stdout.splitlines()
    if receipt['phase'] in ('starting','started'):
        if exists and running_window(session,window):
            receipt['phase']='started';atomic(path,receipt)
            subprocess.run(['tmux','select-window','-t','main:'+window],check=True,timeout=10)
            return receipt
        raise RuntimeError('destination start is uncertain; preserve both copies and inspect Codex before retry')
    if receipt['phase']!='imported':raise RuntimeError('finish the discussion import before continuation')
    snapshot=Store(HOME).snapshot(session)
    if digest(snapshot)!=receipt['baseline']:raise RuntimeError('destination history changed after import; reconcile before starting')
    cwd=next(r['cwd'] for r in snapshot['databases']['state_5.sqlite']['threads'] if r['id']==session)
    if not Path(cwd).is_dir() or not Path(cwd).resolve().is_relative_to(Path('/home/agent')):
        raise RuntimeError('bootstrap the selected discussion workspace before handoff')
    environment=codex_environment()
    # Existing tmux servers retain their original environment. Supply the
    # current managed proxy to this window without changing other windows.
    window_environment=[]
    for key in sorted(platform_proxy.KEYS):
        if key in environment:window_environment+=['-e',key+'='+environment[key]]
    receipt['phase']='starting';atomic(path,receipt)
    if subprocess.run(['tmux','has-session','-t','=main'],capture_output=True).returncode:
        subprocess.run(['tmux','new-session','-d','-s','main','-c',cwd],check=True,env=environment)
    subprocess.run(['tmux','new-window','-d','-t','main:','-n',window,'-c',cwd,*window_environment,
        shlex.join([codex_binary(),'resume',session])],check=True)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        try:
            if running_window(session,window):
                receipt['phase']='started';atomic(path,receipt)
                subprocess.run(['tmux','select-window','-t','main:'+window],check=True)
                return receipt
        except (RuntimeError,OSError):pass
        time.sleep(2)
    raise RuntimeError('destination start is uncertain; preserve both copies and inspect its handoff tmux window')


def running_window(session,window):
    panes=subprocess.run(['tmux','list-panes','-t','main:'+window,'-F',
        '#{pane_dead}\t#{pane_current_command}\t#{pane_start_command}'],capture_output=True,text=True,timeout=10)
    if panes.returncode:return False
    matches=False
    for line in panes.stdout.splitlines():
        fields=line.split('\t',2)
        if len(fields)!=3 or fields[:2]!=['0','codex']:continue
        words=shlex.split(fields[2])
        if len(words)==1:words=shlex.split(words[0])
        if len(words)==3 and Path(words[0]).name=='codex' and words[1:]==['resume',session]:
            matches=True
    if not matches:return False
    try:
        with writers(HOME,[session]):return False
    except RuntimeError as error:
        if 'live writer' in str(error):return True
        raise


@contextlib.contextmanager
def workspace_access(action):
    """Serialize discussion writes against the runner's existing stop gate."""
    if action not in ('export','import','start'):
        yield;return
    with (WORKSPACE/'workspace.lock').open('r') as lock:
        fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        path=WORKSPACE/'maintenance.json'
        record=json.loads(path.read_text()) if path.exists() else None
        if record and (record['phase']=='pending' or (action!='export' and record['expires_at']>time.time())):
            raise RuntimeError('runner is reserved or replacement is pending; preserve both discussion copies and retry later')
        placement=WORKSPACE/'placement.json'
        if action=='start' and placement.exists():
            current=json.loads(placement.read_text())
            if current['phase']!='ready' or current['current']!=socket.gethostname().split('-air')[0]:
                raise RuntimeError('this runner is not the valid workspace placement; use the controller workspace move')
        yield


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('ready','inspect','export','import','start','workspace-inspect','workspace-close','workspace-restore','workspace-manifest','workspace-ready','workspace-fixture'))
    parser.add_argument('--workspace')
    parser.add_argument('--tmux-session',help='limit a qualification move to one named tmux session')
    parser.add_argument('--session');parser.add_argument('--operation');parser.add_argument('--wait',type=int,default=300)
    args=parser.parse_args(argv)
    try:
        if os.geteuid()!=pwd.getpwnam('agent').pw_uid:raise RuntimeError('run discussion handoff as agent')
        if args.action not in ('ready','inspect'):
            if not args.operation or not re.fullmatch('[0-9a-f]{24}',args.operation):raise RuntimeError('handoff needs a valid operation ID')
            if args.session:identifier(args.session)
            if not 0<=args.wait<=900:raise RuntimeError('handoff wait must be at most 900 seconds')
        ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
        with (ROOT/'operation.lock').open('a') as lock, workspace_access(args.action):
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if args.action=='ready':value=ready(args.workspace)
            elif args.action=='inspect':value=inspect(identifier(args.session))
            elif args.action=='export':value=export(identifier(args.session),args.operation,args.wait)
            elif args.action.startswith('workspace-'):
                import codex_workspace as workspace
                if args.action=='workspace-inspect':value=workspace.inventory(args.operation,args.tmux_session)
                elif args.action=='workspace-close':value=workspace.close(args.operation,args.wait)
                elif args.action=='workspace-manifest':value=workspace.manifest(args.operation)
                elif args.action=='workspace-fixture':value=workspace.fixture(args.operation,identifier(args.session))
                elif args.action=='workspace-ready':
                    if workspace.tmux('list-panes','-a','-F','#{pane_current_command}',check=False).stdout.splitlines().count('codex'):
                        raise RuntimeError('destination still has open Codex clients; preserve them before moving this workspace')
                    value={'ready':True}
                else:
                    data=sys.stdin.buffer.read(1024*1024+1)
                    if len(data)>1024*1024:raise RuntimeError('workspace manifest exceeds its bound')
                    value=workspace.restore(json.loads(data),args.operation)
            elif args.action=='import':
                data=sys.stdin.buffer.read(350*1024*1024+1)
                if len(data)>350*1024*1024:raise RuntimeError('handoff payload is too large')
                value=receive(json.loads(data),args.operation)
            else:value=start(identifier(args.session),args.operation)
        sys.stdout.buffer.write(canonical(value)+b'\n')
        return 0
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,subprocess.SubprocessError) as error:
        print('Codex handoff: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
