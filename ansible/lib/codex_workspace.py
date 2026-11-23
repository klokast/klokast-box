"""The tmux workspace of this runner and the native discussions in its panes.

An AIR image replacement saves the workspace before the stop and starts it
again after the boot: Codex and Claude Code panes resume their discussions
natively from the retained home. Discussions never leave their runner.
"""
import json
import contextlib
import os
from pathlib import Path
import re
import selectors
import signal
import shlex
import subprocess
import tempfile
import time
import uuid

from codex_live import LiveRPC
import platform_proxy

HOME=Path('/home/agent/.codex')
# The checkpoints keep this earlier directory name: workspaces/OPERATION/.
ROOT=Path('/home/agent/.local/state/klokast/handoffs')
CODEX='/usr/local/bin/codex'
CLAUDE_BINARY='/usr/local/bin/claude'


def codex_environment():
    return dict(os.environ, **platform_proxy.environment())


def codex_binary():
    # The image installs the only Codex; the retained home holds no executable.
    if not os.access(CODEX,os.X_OK):raise RuntimeError('Codex is not installed in the runner image: '+CODEX)
    return CODEX


def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent,mode='wb',prefix='.workspace-',delete=False) as stream:
        temporary=Path(stream.name)
        stream.write(json.dumps(value,sort_keys=True,separators=(',',':')).encode());stream.flush();os.fsync(stream.fileno())
    try:
        temporary.replace(path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:temporary.unlink(missing_ok=True)


class RPC:
    """A bounded Codex app-server over stdio, for requests that need no running daemon."""
    def __enter__(self):
        self.process=subprocess.Popen([codex_binary(),'app-server','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,env=codex_environment())
        self.number=0
        try:self.call('initialize',{'clientInfo':{'name':'klokast-workspace','version':'1'},'capabilities':{'experimentalApi':True}})
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
                if 'error' in value:raise RuntimeError('Codex refused '+method+'; inspect the discussion')
                return value['result']
        raise RuntimeError('Codex control request timed out: '+method)

    def __exit__(self,*args):
        self.process.stdin.close()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=3)
        self.process.stdout.close()


def tmux(*args,check=True):
    # Non-login controller SSH uses the C locale. Native tmux otherwise
    # replaces tabs and Unicode titles, including the field separators.
    p=subprocess.run(['tmux','-u',*map(str,args)],text=True,capture_output=True,timeout=20)
    if check and p.returncode:raise RuntimeError('tmux workspace operation failed: '+str(args[0])+': '+p.stderr.strip()[-400:])
    return p


def loaded(rpc):
    return [rpc.call('thread/read',{'threadId':sid,'includeTurns':False})['thread']
            for sid in rpc.call('thread/loaded/list')['data']]


def match_discussion(title,cwd,threads):
    # The native TUI changes threads after a fork without changing its process
    # argv. Match its live title, then discard retained ancestor threads.
    candidates=[t for t in threads if t.get('name') and t.get('cwd')==cwd and
                title.endswith(t['name']+' | '+Path(cwd).name)]
    parents={t.get('forkedFromId') for t in candidates}
    candidates=[t for t in candidates if t['id'] not in parents]
    if len(candidates)!=1:
        raise RuntimeError('cannot identify one open Codex discussion from its native tmux title')
    return candidates[0]['id']


def inventory(operation,session=None,frozen=()):
    """The tmux sessions, windows and panes, and the discussion that each agent pane resumes."""
    result=tmux('list-panes','-a','-F',
        '#{session_name}\t#{window_index}\t#{window_name}\t#{window_layout}\t#{window_active}\t#{pane_index}\t#{pane_id}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_title}\t#{pane_active}\t#{window_width}\t#{window_height}',check=False)
    sessions={};selected=[]
    if result.returncode and any(t in result.stderr for t in ('no server running','error connecting to','failed to connect')):
        result.stdout=''
    elif result.returncode:raise RuntimeError('cannot inspect tmux workspace')
    fields=[line.split('\t') for line in result.stdout.splitlines()]
    if any(len(row)!=13 for row in fields):raise RuntimeError('unsupported tmux workspace fields')
    needs_rpc=any(row[7]=='codex' for row in fields)
    claude=[];unused=[]
    if any(row[7]=='claude' for row in fields):
        from air_workspace import claude_clients, processes
        rows=processes(os.geteuid());clients=claude_clients(rows)
    with LiveRPC(codex_binary()) if needs_rpc else contextlib.nullcontext(None) as rpc:
        threads=loaded(rpc) if rpc else []
        for line in result.stdout.splitlines():
            fields=line.split('\t')
            if len(fields)!=13:raise RuntimeError('unsupported tmux workspace fields')
            name,index,wname,layout,active,pindex,pid,command,cwd,title,pactive,width,height=fields
            if session and name!=session:continue
            if pid in frozen:command='codex'
            if any(c in name for c in ':\n\r\t') or len(name)>80:raise RuntimeError('unsupported tmux session name')
            # Image replacement ends every process; only a discussion needs its pane restored.
            agent=command in ('codex','claude')
            if not Path(cwd).is_dir() or not Path(cwd).resolve().is_relative_to(Path('/home/agent')):
                # Only the runner home is retained. A shell pane restores in the home.
                if agent:raise RuntimeError('workspace directory is outside the runner home')
                cwd='/home/agent'
            sid=match_discussion(title,cwd,threads) if command=='codex' else None
            if sid:selected.append(sid)
            current=sessions.setdefault(name,{'name':name,'windows':{}})
            window=current['windows'].setdefault(index,{'index':int(index),'name':wname,'layout':layout,
                'active':active=='1','width':int(width),'height':int(height),'panes':[]})
            pane={'index':int(pindex),'source_pane':pid,'cwd':cwd,'discussion':sid,'active':pactive=='1'}
            if command=='claude':
                client,sid=claude_discussion(pid,rows,clients)
                if sid:
                    pane['claude']=sid;claude.append(sid)
                    if sid!=client:pane['claude_client']=client
                else:
                    pane['claude_unused']=client;unused.append(client)
            window['panes'].append(pane)
    if len(selected)!=len(set(selected)):raise RuntimeError('the same discussion is open in multiple panes')
    if len(claude+unused)!=len(set(claude+unused)):raise RuntimeError('the same Claude Code discussion is open in multiple panes')
    if len(selected)+len(claude)+len(unused)>32 or len(sessions)>16:raise RuntimeError('workspace exceeds its bounded size: 32 discussions and 16 sessions')
    value={'version':1,'operation':operation,'sessions':[], 'discussions':sorted(selected),'phase':'inventoried','scope':session}
    if claude:value['claude']=sorted(claude)
    if unused:value['claude_unused']=sorted(unused)
    for item in sessions.values():
        item['windows']=sorted(item['windows'].values(),key=lambda v:v['index'])
        for window in item['windows']:window['panes'].sort(key=lambda v:v['index'])
        value['sessions'].append(item)
    return value


def claude_discussion(pane,rows,clients):
    """The session of the one Claude Code client in a pane and the conversation that the pane resumes.

    A client that moved its conversation to the background shows the agent
    view; its pane resumes the parked conversation. A client without a
    conversation has nothing to resume; its pane restores as a shell.
    """
    parent=int(tmux('display-message','-p','-t',pane,'#{pane_pid}').stdout.strip())
    found=pane_clients(parent,rows,'claude')
    if len(found)!=1 or not clients.get(found[0],{}).get('session'):
        raise RuntimeError('cannot identify one Claude Code discussion in a workspace pane')
    client=clients[found[0]]
    for sid in (claude_parked(client.get('parked')),client['session']):
        if sid and claude_transcript(sid):return client['session'],sid
    return client['session'],None


def claude_parked(job):
    """The session of the background job to which a client moved its conversation."""
    if not job or not re.fullmatch('[0-9a-f]{8}',job):return None
    path=HOME.parent/'.claude/jobs'/job/'state.json'
    try:
        if path.is_symlink() or path.stat().st_size>65536:return None
        sid=json.loads(path.read_text()).get('resumeSessionId')
        return sid if str(uuid.UUID(sid))==sid else None
    except (OSError,ValueError,TypeError,AttributeError):
        return None


def claude_transcript(sid):
    """A transcript with a user message. `claude --resume` finds no conversation in one with only metadata."""
    for path in (HOME.parent/'.claude/projects').glob('*/'+sid+'.jsonl'):
        try:
            with path.open('rb') as stream:
                for line in stream:
                    if b'"type":"user"' not in line:continue
                    try:
                        if json.loads(line).get('type')=='user':return True
                    except (ValueError,AttributeError):
                        continue  # A partly written line does not hide the others.
        except OSError:
            continue
    return False


def claude_live():
    from air_workspace import claude_clients, processes
    return {c['session'] for c in claude_clients(processes(os.geteuid())).values() if c['session']}


def workspace_shape(value):
    """Visible structure, independent of each tmux server's pane IDs."""
    def layout(text):
        return re.sub(r'(\d+x\d+,\d+,\d+),\d+',r'\1',text.split(',',1)[1])
    return [{'name':s['name'],'windows':[
        {k:w[k] for k in ('index','name','active')} | {'layout':layout(w['layout']),'panes':[
            {k:p[k] for k in ('index','cwd','discussion','active')} | {'claude':p.get('claude')} for p in w['panes']]}
        for w in s['windows']]} for s in value['sessions']]


@contextlib.contextmanager
def frozen_tuis(panes,program='codex'):
    from air_workspace import processes
    paused=[];rows=processes(os.geteuid())
    try:
        for pane in panes:
            parent=int(tmux('display-message','-p','-t',pane,'#{pane_pid}').stdout.strip())
            clients=pane_clients(parent,rows,program)
            if len(clients)!=1:raise RuntimeError('cannot fence exactly one native TUI client in a workspace pane')
            # Interactive Bash must not reap a stopped foreground job and move
            # it into the background. Freeze its parent shells first.
            ancestors=[];pid=rows[clients[0]]['parent']
            while pid in rows:
                if pid==parent:
                    ancestors.append(pid);break
                if parent==clients[0]:break
                ancestors.append(pid);pid=rows[pid]['parent']
            if parent!=clients[0]:
                for pid in reversed(ancestors):os.kill(pid,signal.SIGSTOP);paused.append(pid)
            os.kill(clients[0],signal.SIGSTOP);paused.append(clients[0])
        yield
    finally:
        for pid in reversed(paused):
            try:os.kill(pid,signal.SIGCONT)
            except ProcessLookupError:pass


def pane_clients(parent,rows,program='codex'):
    """The TUI clients below a pane. The Claude Code agent view starts the background supervisor, which is no client."""
    from air_workspace import claude_helper
    descendants={parent}
    for _ in range(16):descendants|={p for p,r in rows.items() if r['parent'] in descendants}
    return [p for p in descendants if p in rows and rows[p]['argv'] and
        Path(rows[p]['argv'][0]).name==program and (program=='claude' and not claude_helper(rows[p]['argv']) or
            program=='codex' and (len(rows[p]['argv'])==1 or rows[p]['argv'][1] not in ('app-server','exec-server','exec')))]


def unarchive_if_needed(rpc,sid):
    thread=rpc.call('thread/read',{'threadId':sid,'includeTurns':False})['thread']
    if not thread.get('path'):raise RuntimeError('discussion has no history path')
    if Path(thread['path']).is_relative_to(HOME/'archived_sessions'):
        rpc.call('thread/unarchive',{'threadId':sid})


def restore(value,operation):
    """Start the saved workspace again in staged sessions, then give them their names."""
    if value.get('version')!=1 or value.get('operation')!=operation:raise RuntimeError('workspace checkpoint differs from its operation')
    directory=ROOT/'workspaces'/operation
    receipt=directory/'restored.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        saved['workspace']=value
        if saved['phase']=='complete':
            verify_restored(saved);return saved
        if saved['phase']!='starting':raise RuntimeError('workspace start has an unsupported recorded phase')
        expected=set(saved['discussions'])|set(saved.get('claude',[]))
        running=set(saved['discussions'])&live_writers()|set(saved.get('claude',[]))&claude_live()
        if running:
            if running!=expected or len(saved['sessions'])!=len(value['sessions']):
                raise RuntimeError('workspace has a partial native start; preserve its staged sessions for reconciliation')
            return finish_restore(saved,receipt)
        # An interrupted empty stage has no discussion writer. Recreate only
        # operation-owned, unattached idle shells. Never replace native work.
        for number in range(len(value['sessions'])):
            stage='restore-'+operation[:12]+'-'+str(number)
            if tmux('has-session','-t','='+stage,check=False).returncode:continue
            commands=tmux('list-panes','-s','-t','='+stage,'-F','#{pane_current_command}').stdout.splitlines()
            attached=tmux('display-message','-p','-t','='+stage+':','#{session_attached}').stdout.strip()
            if attached!='0' or not commands or any(c not in ('bash','sh','ash','zsh','fish') for c in commands):
                raise RuntimeError('interrupted workspace stage contains work or a client; preserve it for reconciliation')
            tmux('kill-session','-t','='+stage)
    saved={'phase':'starting','operation':operation,'discussions':value['discussions'],'sessions':[],'workspace':value}
    if value.get('claude'):saved['claude']=value['claude']
    atomic(receipt,saved)
    # Save the start intent first. An archived discussion cannot resume.
    with RPC() if value['discussions'] else contextlib.nullcontext(None) as rpc:
        for sid in value['discussions']:unarchive_if_needed(rpc,sid)
    env=codex_environment();options=[]
    for key in sorted(platform_proxy.KEYS):
        if key in env:options+=['-e',key+'='+env[key]]
    for number,session in enumerate(value['sessions']):
        stage='restore-'+operation[:12]+'-'+str(number)
        if tmux('has-session','-t','='+stage,check=False).returncode==0:
            raise RuntimeError('staged workspace already exists; inspect the interrupted start')
        first=session['windows'][0];cwd=first['panes'][0]['cwd']
        tmux('new-session','-d','-s',stage,'-x',first['width'],'-y',first['height'],'-c',cwd)
        initial=int(tmux('display-message','-p','-t','='+stage+':','#{window_index}').stdout.strip())
        if initial!=first['index']:tmux('move-window','-s',stage+':'+str(initial),'-t',stage+':'+str(first['index']))
        for wi,window in enumerate(session['windows']):
            target=stage+':'+str(window['index'])
            if wi:tmux('new-window','-d','-t',target,'-c',window['panes'][0]['cwd'])
            tmux('rename-window','-t',target,window['name'])
            tmux('set-option','-w','-t',target,'automatic-rename','off')
            tmux('set-option','-w','-t',target,'remain-on-exit','on')
            pane_ids=[]
            for pi,pane in enumerate(window['panes']):
                command=(shlex.join([codex_binary(),'resume',pane['discussion']]) if pane['discussion'] else
                         shlex.join([CLAUDE_BINARY,'--resume',pane['claude']]) if pane.get('claude') else '/bin/bash -l')
                if pi==0:
                    pane_id=tmux('list-panes','-t',target,'-F','#{pane_id}').stdout.strip()
                    tmux('respawn-pane','-k','-t',pane_id,'-c',pane['cwd'],*options,command)
                else:
                    pane_id=tmux('split-window','-d','-P','-F','#{pane_id}','-t',target,'-c',pane['cwd'],*options,command).stdout.strip()
                pane_ids.append(pane_id)
            tmux('select-layout','-t',target,window['layout'])
            for pi,pane in enumerate(window['panes']):
                if pane['active']:tmux('select-pane','-t',pane_ids[pi])
            if window['active']:active=target
        tmux('select-window','-t',active)
        saved['sessions'].append({'name':session['name'],'stage':stage})
        atomic(receipt,saved)
    deadline=time.monotonic()+90
    while not (set(saved['discussions']).issubset(live_writers()) and set(saved.get('claude',[])).issubset(claude_live())):
        if time.monotonic()>=deadline:raise RuntimeError('the restored discussions did not start in 90 seconds; their native histories are retained')
        time.sleep(1)
    return finish_restore(saved,receipt)


def finish_restore(saved,receipt):
    operation=saved['operation']
    for session in saved['sessions']:
        name=session['name']
        if tmux('has-session','-t','='+session['stage'],check=False).returncode:
            if tmux('has-session','-t','='+name,check=False).returncode:
                raise RuntimeError('interrupted restore lost its staged workspace')
            continue  # A previous attempt already renamed this completed stage.
        if tmux('has-session','-t','='+name,check=False).returncode==0:
            # Keep an existing session of the same name instead of killing it.
            tmux('rename-session','-t','='+name,name+'-before-'+operation[:8])
        tmux('rename-session','-t','='+session['stage'],name)
    saved['phase']='complete';atomic(receipt,saved)
    verify_restored(saved)
    for session in saved['sessions']:
        for window in saved['workspace']['sessions']:
            if window['name']!=session['name']:continue
            for item in window['windows']:
                tmux('set-option','-w','-t',session['name']+':'+str(item['index']),'remain-on-exit','off')
    return saved


def live_writers():
    from air_workspace import codex_writers
    return set(codex_writers(HOME))


def verify_restored(saved):
    if not set(saved['discussions']).issubset(live_writers()):raise RuntimeError('restored Codex discussions are not running')
    if not set(saved.get('claude',[])).issubset(claude_live()):raise RuntimeError('restored Claude Code discussions are not running')
    for s in saved['sessions']:
        if tmux('has-session','-t','='+s['name'],check=False).returncode:raise RuntimeError('restored tmux workspace is missing')
    expected=saved['workspace']
    deadline=time.monotonic()+30
    while True:
        try:
            observed={'sessions':[]}
            for session in expected['sessions']:
                observed['sessions']+=inventory(saved['operation'],session['name'])['sessions']
            if workspace_shape(observed)!=workspace_shape(expected):
                raise RuntimeError('restored tmux windows, layout or discussions differ')
            return
        except RuntimeError:
            if time.monotonic()>=deadline:raise
            time.sleep(1)
