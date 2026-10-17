"""Move the open native Codex discussions and their tmux workspace."""
import json
import contextlib
import os
from pathlib import Path
import re
import signal
import shlex
import subprocess
import time

import codex_handoff as handoff
from codex_discussion import Store, atomic, digest, lineage_threads, validate_bundle
from codex_live import LiveRPC


def tmux(*args,check=True):
    # Non-login controller SSH uses the C locale. Native tmux otherwise
    # replaces tabs and Unicode titles, including the field separators.
    p=subprocess.run(['tmux','-u',*map(str,args)],text=True,capture_output=True,timeout=20)
    if check and p.returncode:raise RuntimeError('tmux workspace operation failed: '+str(args[0]))
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


def inventory(operation,session=None,refresh=False,persist=True,frozen=()):
    path=handoff.ROOT/'workspaces'/operation/'manifest.json'
    if path.exists() and not refresh:return json.loads(path.read_text())
    result=tmux('list-panes','-a','-F',
        '#{session_name}\t#{window_index}\t#{window_name}\t#{window_layout}\t#{window_active}\t#{pane_index}\t#{pane_id}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_title}\t#{pane_active}\t#{window_width}\t#{window_height}')
    sessions={};selected=[]
    with LiveRPC(handoff.codex_binary()) as rpc:
        threads=loaded(rpc)
        for line in result.stdout.splitlines():
            fields=line.split('\t')
            if len(fields)!=13:raise RuntimeError('unsupported tmux workspace fields')
            name,index,wname,layout,active,pindex,pid,command,cwd,title,pactive,width,height=fields
            if session and name!=session:continue
            if pid in frozen:command='codex'
            if any(c in name for c in ':\n\r\t') or len(name)>80:raise RuntimeError('unsupported tmux session name')
            if command not in ('codex','bash','sh','ash','zsh','fish'):
                raise RuntimeError('workspace contains non-Codex running work; keep it on its source')
            if not Path(cwd).is_dir() or not Path(cwd).resolve().is_relative_to(Path('/home/agent')):
                raise RuntimeError('workspace directory is outside the runner home')
            sid=match_discussion(title,cwd,threads) if command=='codex' else None
            if sid:selected.append(sid)
            current=sessions.setdefault(name,{'name':name,'windows':{}})
            window=current['windows'].setdefault(index,{'index':int(index),'name':wname,'layout':layout,
                'active':active=='1','width':int(width),'height':int(height),'panes':[]})
            window['panes'].append({'index':int(pindex),'source_pane':pid,'cwd':cwd,'discussion':sid,'active':pactive=='1'})
    if not selected:raise RuntimeError('the selected tmux workspace has no open Codex discussions')
    if len(selected)!=len(set(selected)):raise RuntimeError('the same discussion is open in multiple panes')
    if len(selected)>32 or len(sessions)>16:raise RuntimeError('workspace exceeds the bounded move size')
    value={'version':1,'operation':operation,'sessions':[], 'discussions':sorted(selected),'phase':'inventoried','scope':session}
    for item in sessions.values():
        item['windows']=sorted(item['windows'].values(),key=lambda v:v['index'])
        for window in item['windows']:window['panes'].sort(key=lambda v:v['index'])
        value['sessions'].append(item)
    if persist:atomic(path,value)
    return value


def workspace_shape(value):
    """Visible structure, independent of each tmux server's pane IDs."""
    def layout(text):
        return re.sub(r'(\d+x\d+,\d+,\d+),\d+',r'\1',text.split(',',1)[1])
    return [{'name':s['name'],'windows':[
        {k:w[k] for k in ('index','name','active')} | {'layout':layout(w['layout']),'panes':[
            {k:p[k] for k in ('index','cwd','discussion','active')} for p in w['panes']]}
        for w in s['windows']]} for s in value['sessions']]


def manifest(operation):
    value=json.loads((handoff.ROOT/'workspaces'/operation/'manifest.json').read_text())
    if value.get('operation')!=operation or value.get('version')!=1:raise RuntimeError('workspace move manifest differs')
    return value


@contextlib.contextmanager
def frozen_tuis(panes):
    from air_workspace import processes
    paused=[];rows=processes(os.geteuid())
    try:
        for pane in panes:
            parent=int(tmux('display-message','-p','-t',pane,'#{pane_pid}').stdout.strip())
            clients=pane_clients(parent,rows)
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


def pane_clients(parent,rows):
    descendants={parent}
    for _ in range(16):descendants|={p for p,r in rows.items() if r['parent'] in descendants}
    return [p for p in descendants if p in rows and rows[p]['argv'] and
        Path(rows[p]['argv'][0]).name=='codex' and (len(rows[p]['argv'])==1 or rows[p]['argv'][1] not in ('app-server','exec-server','exec'))]


def empty_destination():
    from air_workspace import processes
    rows=processes(os.geteuid())
    result=tmux('list-panes','-a','-F','#{pane_id}\t#{pane_pid}\t#{pane_dead}',check=False)
    if result.returncode and not any(s in result.stderr for s in ('no server running','error connecting to','failed to connect to server')):
        raise RuntimeError('cannot inspect destination tmux clients')
    panes=[fields[0] for line in result.stdout.splitlines() if
        (fields:=line.split('\t'))[2]=='0' and pane_clients(int(fields[1]),rows)]
    if not panes:return []
    with LiveRPC(handoff.codex_binary()) as rpc:
        for thread in loaded(rpc):
            c=Store(handoff.HOME).connect('state_5.sqlite')
            try:persisted=c.execute('SELECT 1 FROM threads WHERE id=?',(thread['id'],)).fetchone()
            finally:c.close()
            if persisted or thread.get('name') or thread.get('preview') or thread.get('status',{}).get('type')!='idle':
                raise RuntimeError('destination has an open discussion; preserve it before moving this workspace')
            jobs=rpc.call('thread/backgroundTerminals/list',{'threadId':thread['id']})
            if jobs.get('data') or jobs.get('nextCursor'):raise RuntimeError('destination has background work')
    return panes


def park_empty_destination():
    panes=empty_destination()
    if panes:
        with frozen_tuis(panes):
            if empty_destination()!=panes:raise RuntimeError('destination changed while fencing empty clients')
            for pane in panes:tmux('respawn-pane','-k','-t',pane,'/bin/bash -l')
        subprocess.run([handoff.codex_binary(),'app-server','daemon','stop'],check=True,stdout=subprocess.DEVNULL,timeout=60)
    # Failed qualification panes contain no running discussion. Keep their
    # diagnostics in the private operation, then remove only those dead stages.
    for receipt in (handoff.ROOT/'workspaces').glob('*/restored.json'):
        saved=json.loads(receipt.read_text());scope=saved.get('workspace',{}).get('scope')
        if saved['phase']!='starting' or not scope or not scope.startswith('qualification-'):continue
        if set(saved['discussions'])&live_writers():continue
        for session in saved['sessions']:
            stage=session['stage']
            if not re.fullmatch('move-'+re.escape(saved['operation'][:12])+'-[0-9]+',stage):raise RuntimeError('qualification stage differs')
            if tmux('has-session','-t','='+stage,check=False).returncode:continue
            fields=tmux('list-panes','-s','-t','='+stage,'-F','#{pane_id}\t#{pane_dead}').stdout.splitlines()
            if any(line.split('\t')[1]!='1' for line in fields):continue
            if tmux('display-message','-p','-t','='+stage+':','#{session_attached}').stdout.strip()!='0':continue
            errors={line.split('\t')[0]:tmux('capture-pane','-p','-t',line.split('\t')[0],'-S','-100').stdout for line in fields}
            atomic(receipt.parent/'startup-errors.json',errors)
            tmux('kill-session','-t','='+stage)
    return {'ready':True,'closed_empty_clients':len(panes)}


def close(operation,wait):
    value=manifest(operation);path=handoff.ROOT/'workspaces'/operation/'manifest.json'
    if value['phase']=='closed':return value
    if value['phase']=='closing' and not set(value['discussions'])&live_writers():
        finish_source(value)
        value['phase']='closed';atomic(path,value);return value
    with LiveRPC(handoff.codex_binary()) as rpc:
        deadline=time.monotonic()+wait
        while True:
            threads=loaded(rpc)
            relevant=[t for t in threads if not value['scope'] or t['id'] in value['discussions']]
            if not any(t.get('status',{}).get('type')=='active' for t in relevant):break
            if time.monotonic()>=deadline:raise RuntimeError('workspace discussions are still running; resume the move when their turns finish')
            time.sleep(2)
        with frozen_tuis([p['source_pane'] for s in value['sessions'] for w in s['windows'] for p in w['panes'] if p['discussion']]):
            # Freeze only the foreground TUI clients, not the daemon or its
            # workers. This prevents a tab switch/new turn during the snapshot.
            value=inventory(operation,value['scope'],refresh=True,
                frozen={p['source_pane'] for s in value['sessions'] for w in s['windows'] for p in w['panes'] if p['discussion']})
            if any(t.get('status',{}).get('type')=='active' for t in loaded(rpc)
                   if not value['scope'] or t['id'] in value['discussions']):
                raise RuntimeError('a discussion started another turn while fencing; resume the move after it finishes')
            for sid in value['discussions']:
                validate_bundle(Store(handoff.HOME).snapshot(sid))
                jobs=rpc.call('thread/backgroundTerminals/list',{'threadId':sid})
                if jobs.get('data') or jobs.get('nextCursor'):raise RuntimeError('a discussion has running background terminals; wait for them before moving')
            if any(t.get('status',{}).get('type')=='active' for t in loaded(rpc)
                   if not value['scope'] or t['id'] in value['discussions']):
                raise RuntimeError('a discussion started another turn before closure; resume after it finishes')
            # Keep direct Codex panes after shutdown too. A move must not close
            # the operator's tmux connection or recovery shell.
            for session in value['sessions']:
                for window in session['windows']:
                    target=session['name']+':'+str(window['index'])
                    window['source_remain_on_exit']=tmux('show-options','-w','-v','-t',target,'remain-on-exit').stdout.strip()
                    tmux('set-option','-w','-t',target,'remain-on-exit','on')
            value['phase']='closing';atomic(path,value)
            for sid in value['discussions']:rpc.call('thread/archive',{'threadId':sid})
    finish_source(value)
    value['phase']='closed';atomic(path,value)
    return value


def finish_source(value):
    # A whole-runner move also closes retained idle native sessions. Their
    # histories stay here; only the manifest's visible discussions are exported.
    if not value['scope']:
        subprocess.run([handoff.codex_binary(),'app-server','daemon','stop'],check=True,
                       stdout=subprocess.DEVNULL,timeout=60)
    handoff.quiesce(value['discussions'],30)
    # Archive flushes and unloads the native writers. Unarchive their saved
    # histories without starting a turn so both runners keep normal discovery.
    with handoff.RPC() as rpc:
        for sid in value['discussions']:rpc.call('thread/unarchive',{'threadId':sid})
    for session in value['sessions']:
        for window in session['windows']:
            for pane in window['panes']:
                dead=tmux('display-message','-p','-t',pane['source_pane'],'#{pane_dead}',check=False)
                if dead.returncode==0 and dead.stdout.strip()=='1':
                    tmux('respawn-pane','-t',pane['source_pane'],'-c',pane['cwd'],'/bin/bash -l')
            if 'source_remain_on_exit' in window:
                tmux('set-option','-w','-t',session['name']+':'+str(window['index']),
                     'remain-on-exit',window['source_remain_on_exit'])
    seal_workspace(value)


def seal_workspace(value):
    """Save the whole cohort, including open fork ancestors, before copying."""
    dependencies={};selected=set(value['discussions'])
    for sid in value['discussions']:
        child=discussion_operation(value['operation'],sid)
        bundle,_=handoff.seal(sid,child,0)
        dependencies[sid]=set(lineage_threads(bundle))&selected
    ordered=[];remaining=set(selected)
    while remaining:
        ready=sorted(sid for sid in remaining if not dependencies[sid]&remaining)
        if not ready:raise RuntimeError('workspace fork dependencies contain a cycle')
        ordered+=ready;remaining.difference_update(ready)
    value['discussions']=ordered


def restore(value,operation):
    if value.get('version')!=1 or value.get('operation')!=operation:raise RuntimeError('workspace move manifest differs')
    directory=handoff.ROOT/'workspaces'/operation
    receipt=directory/'restored.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        saved['workspace']=value
        if saved['phase']=='complete':
            verify_restored(saved);return saved
        if saved['phase']!='starting':raise RuntimeError('workspace start has an unsupported recorded phase')
        running=set(saved['discussions'])&live_writers()
        if running:
            if running!=set(saved['discussions']) or len(saved['sessions'])!=len(value['sessions']):
                raise RuntimeError('workspace has a partial native start; preserve its staged sessions for reconciliation')
            return finish_restore(saved,receipt)
        # An interrupted empty stage has no discussion writer. Recreate only
        # operation-owned, unattached idle shells. Never replace native work.
        for number in range(len(value['sessions'])):
            stage='move-'+operation[:12]+'-'+str(number)
            if tmux('has-session','-t','='+stage,check=False).returncode:continue
            commands=tmux('list-panes','-s','-t','='+stage,'-F','#{pane_current_command}').stdout.splitlines()
            attached=tmux('display-message','-p','-t','='+stage+':','#{session_attached}').stdout.strip()
            if attached!='0' or not commands or any(c not in ('bash','sh','ash','zsh','fish') for c in commands):
                raise RuntimeError('interrupted workspace stage contains work or a client; preserve it for reconciliation')
            tmux('kill-session','-t','='+stage)
    else:
        for sid in value['discussions']:
            child=discussion_operation(operation,sid)
            r=json.loads((handoff.ROOT/child/'destination.json').read_text())
            snapshot=Store(handoff.HOME).snapshot(sid)
            if digest(snapshot)!=r['baseline']:raise RuntimeError('imported discussion changed before workspace activation')
            # Archive is the native flush/close operation. Restore the original
            # open state through Codex before starting its destination TUI.
            with handoff.RPC() as rpc:rpc.call('thread/unarchive',{'threadId':sid})
    saved={'phase':'starting','operation':operation,'discussions':value['discussions'],'sessions':[],'workspace':value}
    atomic(receipt,saved)
    env=handoff.codex_environment();options=[]
    for key in sorted(handoff.platform_proxy.KEYS):
        if key in env:options+=['-e',key+'='+env[key]]
    for number,session in enumerate(value['sessions']):
        stage='move-'+operation[:12]+'-'+str(number)
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
                command=shlex.join([handoff.codex_binary(),'resume',pane['discussion']]) if pane['discussion'] else '/bin/bash -l'
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
    while not set(saved['discussions']).issubset(live_writers()):
        if time.monotonic()>=deadline:raise RuntimeError('destination discussion startup is uncertain; preserve both copies')
        time.sleep(1)
    return finish_restore(saved,receipt)


def finish_restore(saved,receipt):
    operation=saved['operation']
    for session in saved['sessions']:
        name=session['name']
        if tmux('has-session','-t','='+session['stage'],check=False).returncode:
            if tmux('has-session','-t','='+name,check=False).returncode:
                raise RuntimeError('interrupted destination lost its staged workspace')
            continue  # A previous attempt already renamed this completed stage.
        if tmux('has-session','-t','='+name,check=False).returncode==0:
            # Preserve the destination's existing workspace instead of killing it.
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
    return set(codex_writers(handoff.HOME))


def verify_restored(saved):
    if not set(saved['discussions']).issubset(live_writers()):raise RuntimeError('destination discussions are not running')
    for s in saved['sessions']:
        if tmux('has-session','-t','='+s['name'],check=False).returncode:raise RuntimeError('destination tmux workspace is missing')
    expected=saved['workspace']
    deadline=time.monotonic()+30
    while True:
        try:
            observed={'sessions':[]}
            for session in expected['sessions']:
                observed['sessions']+=inventory(saved['operation'],session['name'],refresh=True,persist=False)['sessions']
            if workspace_shape(observed)!=workspace_shape(expected):
                raise RuntimeError('destination tmux windows, layout or discussions differ')
            return
        except RuntimeError:
            if time.monotonic()>=deadline:raise
            time.sleep(1)


def discussion_operation(operation,sid):
    import hashlib
    return hashlib.sha256((operation+sid).encode()).hexdigest()[:24]


def fixture(operation,seed):
    """Create isolated native forks for a real, model-free round-trip test."""
    path=handoff.ROOT/'workspaces'/operation/'fixture.json'
    if path.exists():return json.loads(path.read_text())
    name='qualification-'+operation[:12];ids=[];origin_seed=seed
    with LiveRPC(handoff.codex_binary()) as rpc:
        for i in range(3):
            t=rpc.call('thread/fork',{'threadId':seed,'excludeTurns':True})['thread']
            rpc.call('thread/name/set',{'threadId':t['id'],'name':'Workspace qualification '+str(i+1)})
            ids.append(t['id'])
            seed=t['id']  # Exercise open ancestors and nested paginated lineage.
    cwd='/home/agent/src/klokast/klokast-box'
    command=lambda sid:shlex.join([handoff.codex_binary(),'resume',sid])
    tmux('new-session','-d','-s',name,'-n','Qualification one','-x','120','-y','40','-c',cwd,'/bin/bash -l')
    first=tmux('list-windows','-t','='+name,'-F','#{window_index}').stdout.strip()
    if first!='0':tmux('move-window','-s',name+':'+first,'-t',name+':0')
    tmux('new-window','-d','-t',name+':2','-n','Qualification split','-c',cwd,'/bin/bash -l')
    tmux('split-window','-d','-t',name+':2','-c',cwd,'/bin/bash -l')
    tmux('new-window','-d','-t',name+':4','-n','Qualification three','-c',cwd,'/bin/bash -l')
    panes=tmux('list-panes','-s','-t','='+name,'-F','#{pane_id}').stdout.splitlines()
    for pane,sid in zip((panes[0],panes[1],panes[3]),ids):
        tmux('send-keys','-l','-t',pane,command(sid));tmux('send-keys','-t',pane,'Enter')
    tmux('select-window','-t',name+':2')
    deadline=time.monotonic()+45
    while True:
        titles=tmux('list-panes','-s','-t','='+name,'-F','#{pane_title}').stdout
        if all('Workspace qualification '+str(i+1) in titles for i in range(3)):break
        if time.monotonic()>=deadline:raise RuntimeError('native qualification TUIs did not become ready')
        time.sleep(1)
    value={'workspace':name,'discussions':ids,'seed':origin_seed}
    atomic(path,value)
    return value
