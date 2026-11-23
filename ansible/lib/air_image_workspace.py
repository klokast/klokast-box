"""Same-machine workspace checkpoints; native history remains on retained storage."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import codex_handoff as handoff
import codex_workspace as workspace
from codex_live import LiveRPC
from codex_discussion import atomic


def inspect():
    """Refuse running agents only. Image replacement ends every other process."""
    codex=inspect_codex()
    value=dict(codex,claude=inspect_claude(),claude_background=inspect_claude('bg'))
    inspect_work(codex)
    return value


def claude_idle(client):
    """A client that runs no turn. Claude Code writes its status after the first turn.

    Without a status, a client with a conversation can run its first turn.
    A client without one, for example on the session picker, has nothing to lose.
    """
    return bool(client['session']) and (client['status']=='idle' or
        client['status']=='unknown' and not workspace.claude_transcript(client['session']))


def inspect_claude(kind='interactive'):
    from air_workspace import claude_clients, processes
    rows=processes(os.geteuid());clients=claude_clients(rows)
    blocked=[(pid,c) for pid,c in sorted(clients.items()) if not claude_idle(c)]
    if blocked:
        panes=tmux_panes()
        blocked=['PID %d %s %s'%(pid,claude_window(pid,rows,panes),claude_activity(c)) for pid,c in blocked]
        raise RuntimeError('Claude Code discussion is active or needs attention: '+', '.join(blocked)+
                           '; answer or close it')
    return sorted(c['session'] for c in clients.values() if c['kind']==kind)


def inspect_work(codex):
    from air_workspace import agent_work, claude_clients, processes
    rows=processes(os.geteuid());clients=claude_clients(rows)
    work=agent_work(rows,clients,codex['threads'],codex.get('fork_parents',[]),os.getpid())
    if not work:return
    panes=tmux_panes()
    def label(pid,owner):
        name=''.join(c for c in rows[pid]['name'] if c.isprintable())[:16]
        if owner is None:return 'PID %d %s is a Codex run without an open discussion'%(pid,name)
        where='in a background session' if clients[owner]['kind']=='bg' else claude_window(owner,rows,panes)
        return 'PID %d %s runs for Claude Code PID %d %s'%(pid,name,owner,where)
    raise RuntimeError('agent work runs outside a turn: '+', '.join(label(*w) for w in work[:3])+
                       (' and %d more'%(len(work)-3) if len(work)>3 else '')+'; wait for it or stop it')


def stop_claude_background():
    # Background sessions have no tmux pane to restore. Their transcripts stay
    # on retained storage; `claude --resume` reopens them.
    result=subprocess.run([workspace.CLAUDE_BINARY,'daemon','stop','--any'],capture_output=True,text=True,timeout=90)
    if result.returncode:
        raise RuntimeError('cannot stop idle Claude Code background sessions: '+result.stderr.strip()[-400:])


def stop_claude_clients(sessions):
    """End idle interactive clients outside the workspace; their transcripts stay for `claude --resume`."""
    from air_workspace import claude_clients, processes
    for pid,client in claude_clients(processes(os.geteuid())).items():
        if client['session'] in sessions and client['kind']=='interactive':
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass


def inspect_codex():
    binary=handoff.codex_binary()
    try:
        daemon=json.loads(subprocess.check_output([binary,'app-server','daemon','version'],text=True,stderr=subprocess.PIPE,timeout=15))
    except subprocess.CalledProcessError:
        if workspace.live_writers():raise RuntimeError('Codex control is unavailable while native writers exist')
        subprocess.check_output([binary,'--version'],text=True,timeout=15)
        return {'threads':[], 'latest':0}
    if daemon.get('status')!='running':
        return {'threads':[], 'latest':0}
    with LiveRPC(binary) as rpc:
        threads=workspace.loaded(rpc)
        blocked=[t for t in threads if t.get('status',{}).get('type')!='idle']
        if blocked:
            panes=tmux_panes()
            raise RuntimeError('Codex discussion is active or needs attention: '+', '.join(
                codex_window(t,panes)+' '+codex_activity(t.get('status',{})) for t in blocked)+'; answer or close it')
        for thread in threads:
            jobs=rpc.call('thread/backgroundTerminals/list',{'threadId':thread['id']})
            if jobs.get('data') or jobs.get('nextCursor'):
                raise RuntimeError('Codex has background terminals: '+codex_window(thread,tmux_panes()))
    # A forked TUI keeps its parent's ID in argv after it loads the new thread.
    return {'threads':[t['id'] for t in threads],
            'fork_parents':sorted({t['forkedFromId'] for t in threads if t.get('forkedFromId')}),
            'latest':max([t.get('updatedAt',0) for t in threads]+[0])}



def tmux_panes():
    """Pane process, window and title of each tmux pane; a refusal names its window."""
    result=workspace.tmux('list-panes','-a','-F',
        '#{pane_pid}\t#{session_name}:#{window_index}\t#{window_name}\t#{pane_current_path}\t#{pane_title}\t#{session_attached}',
        check=False)
    if result.returncode:return []
    return [row for row in (line.split('\t') for line in result.stdout.splitlines()) if len(row)==6]


def window_label(row):
    name=''.join(c for c in row[2] if c.isprintable())[:40]
    if row[5]!='0':return 'tmux window %s "%s"'%(row[1],name)
    # A detached session does not show in the user's terminal; name the command that shows it.
    session=''.join(c for c in row[1].rpartition(':')[0] if c.isprintable())[:40]
    return 'tmux window %s "%s" of detached session "%s" (tmux attach -t %s)'%(row[1],name,session,session)


def codex_window(thread,panes):
    name,cwd=thread.get('name'),thread.get('cwd')
    for row in panes:
        if name and cwd==row[3] and row[4].endswith(name+' | '+Path(cwd).name):return window_label(row)
    return 'discussion %s (no tmux window)'%str(thread.get('id',''))[:8]


def claude_activity(client):
    if not client['session']:return 'has no session record'
    # A client writes its status after its first turn.
    if client['status']=='unknown':return 'records no status yet and has a conversation: its first turn can run'
    return 'is '+''.join(c for c in client['status'] if c.isprintable())[:40]


def claude_window(pid,rows,panes):
    windows={row[0]:row for row in panes}
    for _ in range(16):
        if str(pid) in windows:return 'in '+window_label(windows[str(pid)])
        pid=rows.get(pid,{}).get('parent')
        if not pid:break
    return '(no tmux window)'


def codex_activity(status):
    flags=status.get('activeFlags') or []
    if 'waitingOnApproval' in flags:return 'waits for an approval'
    if 'waitingOnUserInput' in flags:return 'waits for an answer'
    return {'active':'runs a turn','systemError':'stopped on a system error',
            'notLoaded':'is not loaded'}.get(status.get('type'),'has unknown status '+str(status.get('type'))[:40])

def stop_daemon(binary):
    # The native CLI writes its own JSON status. Keep the workspace protocol's
    # stdout limited to the single result emitted by main().
    result=subprocess.run([binary,'app-server','daemon','stop'],capture_output=True,text=True,timeout=90)
    if result.returncode:
        raise RuntimeError('cannot stop the idle Codex daemon: '+result.stderr.strip()[-400:])


def checkpoint(operation):
    path=handoff.ROOT/'workspaces'/operation/'image.json'
    if path.exists():
        saved=json.loads(path.read_text())
        if saved['phase']=='closed':return saved
        if saved['phase']=='closing':
            observed=inspect();background=observed.get('claude_background',[])
            if observed['threads'] or set(observed['claude'])&pane_claude(saved['workspace']):
                raise RuntimeError('interrupted closure still has native writers; preserve the checkpoint')
            if background:stop_claude_background()
            outside=set(observed['claude'])
            if outside:stop_claude_clients(outside)
            wait_closed(set(background)|outside)
            discard_previous_start(path.parent)
            saved['phase']='closed';atomic(path,saved);return saved
    # A resumed operation replaces the image again. Refuse before closing
    # panes if its previous start did not finish.
    previous_start(path.parent)
    observed=inspect();background=observed.get('claude_background',[])
    value=workspace.inventory(operation,refresh=True,persist=False,same_host=True)
    # A native fork keeps its idle parent loaded without a pane. Its history
    # stays on disk; the open child is the discussion to resume.
    if set(observed['threads'])-set(value['discussions'])-set(observed.get('fork_parents',[])):
        raise RuntimeError('Codex has discussions outside the saved tmux workspace')
    # A Claude Code client without a conversation has no discussion; closing
    # it loses nothing. An idle client outside tmux ends with the old VM; its
    # transcript stays for `claude --resume`.
    unused=set(value.get('claude_unused',[]));clients=pane_claude(value)
    rows=[p for s in value['sessions'] for w in s['windows'] for p in w['panes']]
    panes=[p['source_pane'] for p in rows if p['discussion']]
    claude=[p['source_pane'] for p in rows if p.get('claude') or p.get('claude_unused')]
    with workspace.frozen_tuis(panes),workspace.frozen_tuis(claude,'claude'):
        # Check again after preventing TUI input; never interrupt a turn.
        fenced=inspect()
        if (set(fenced['claude'])!=set(observed['claude']) or
                fenced.get('claude_background',[])!=background or
                any(workspace.claude_transcript(sid) for sid in unused)):
            raise RuntimeError('Claude Code discussions changed while fencing; retry when they are idle')
        saved={'phase':'closing','workspace':value}
        if background:saved['claude_background']=background
        atomic(path,saved)
        # The previous start receipt describes the previous checkpoint. If it
        # stays, restore only verifies the new workspace and never starts it.
        discard_previous_start(path.parent)
        stop_daemon(handoff.codex_binary())
        # Claude Code appends each message to its transcript. Closing the
        # idle pane ends the client; native --resume continues it later.
        for p in rows:
            if p['discussion'] or p.get('claude') or p.get('claude_unused'):
                workspace.tmux('respawn-pane','-k','-t',p['source_pane'],'-c',p['cwd'],'/bin/bash -l')
        if background:stop_claude_background()
        outside=set(fenced['claude'])-clients
        if outside:stop_claude_clients(outside)
    wait_closed(clients|outside|set(value.get('claude',[]))|set(background))
    saved['phase']='closed';atomic(path,saved)
    return saved


def pane_claude(value):
    """The Claude Code client of each workspace pane. A client that parked its conversation resumes another session."""
    rows=[p for s in value['sessions'] for w in s['windows'] for p in w['panes']]
    return {p.get('claude_client',p['claude']) for p in rows if p.get('claude')}|set(value.get('claude_unused',[]))


def previous_start(directory):
    receipt=directory/'restored.json'
    if not receipt.exists():return None
    if json.loads(receipt.read_text()).get('phase')!='complete':
        raise RuntimeError('the previous workspace start did not finish; preserve recovery state')
    return receipt


def discard_previous_start(directory):
    receipt=previous_start(directory)
    if receipt:receipt.unlink()


def wait_closed(sessions):
    deadline=time.monotonic()+30
    while sessions&workspace.claude_live():
        if time.monotonic()>=deadline:raise RuntimeError('a closed Claude Code client is still running; preserve the checkpoint')
        time.sleep(0.5)


def restore(operation):
    path=handoff.ROOT/'workspaces'/operation/'image.json'
    # The runner closure decides whether a missing checkpoint is a defect.
    if not path.exists():return {'restored':False}
    saved=json.loads(path.read_text())
    if saved['phase'] not in ('closed','restored'):raise RuntimeError('workspace checkpoint did not finish; preserve recovery state')
    receipt=path.parent/'restored.json'
    if saved['phase']!='restored' and not receipt.exists():
        select_image_package()
    saved['workspace']['same_host']=True
    result=workspace.restore(saved['workspace'],operation,same_host=True)
    saved['phase']='restored';atomic(path,saved)
    return result


def select_image_package():
    """Select verified image bytes only after the old runtime has stopped."""
    record=json.loads(Path('/etc/klokast-codex.json').read_text())
    def version(v):return tuple(map(int,v.split('.')))
    target=handoff.HOME/'packages/app-server-daemon/current'
    if (target/'bin/codex').exists():
        old=subprocess.check_output([str(target/'bin/codex'),'--version'],text=True,timeout=15).strip().split()[-1]
        if version(old)>version(record['version']):raise RuntimeError('image would downgrade a retained Codex package')
    stop_daemon(handoff.CODEX)
    # The retained Codex home names its app-server package; it is the image package.
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and not target.is_symlink():raise RuntimeError('unmanaged Codex path requires explicit recovery: '+str(target))
    temporary=target.with_name(target.name+'.image-selection')
    if temporary.is_symlink():temporary.unlink()
    temporary.symlink_to('/opt/codex');temporary.replace(target)
    settings=handoff.HOME/'app-server-daemon/settings.json'
    value=json.loads(settings.read_text()) if settings.exists() else {}
    value.setdefault('updater',{})['autoUpdateEnabled']=False
    atomic(settings,value)


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=('inspect','checkpoint','restore'))
    p.add_argument('--operation');args=p.parse_args()
    if args.action!='inspect':
        import re
        if not args.operation or not re.fullmatch('[0-9a-f]{24}',args.operation):raise RuntimeError('invalid image workspace operation')
    print(json.dumps(inspect() if args.action=='inspect' else globals()[args.action](args.operation)))


if __name__=='__main__':
    import sys
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print('AIR image workspace: '+str(error),file=sys.stderr);sys.exit(1)
