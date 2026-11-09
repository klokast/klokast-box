"""Same-machine workspace checkpoints; native history remains on retained storage."""
import json
import os
from pathlib import Path
import subprocess
import time

import codex_handoff as handoff
import codex_workspace as workspace
from codex_live import LiveRPC
from codex_discussion import atomic


def inspect():
    return dict(inspect_codex(),claude=inspect_claude())


def inspect_claude():
    from air_workspace import claude_clients, processes
    clients=claude_clients(processes(os.geteuid()))
    blocked=['PID %d is %s'%(pid,c['status'] if c['session'] else 'without a session record')
             for pid,c in sorted(clients.items()) if c['status']!='idle' or not c['session']]
    if blocked:
        raise RuntimeError('Claude Code discussion is active or needs attention: '+', '.join(blocked)+
                           '; answer or close it in its tmux pane')
    return sorted(c['session'] for c in clients.values())


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
        for thread in threads:
            if thread.get('status',{}).get('type')!='idle':
                raise RuntimeError('Codex discussion is active or needs attention')
            jobs=rpc.call('thread/backgroundTerminals/list',{'threadId':thread['id']})
            if jobs.get('data') or jobs.get('nextCursor'):
                raise RuntimeError('Codex has background terminals')
    # A forked TUI keeps its parent's ID in argv after it loads the new thread.
    return {'threads':[t['id'] for t in threads],
            'fork_parents':sorted({t['forkedFromId'] for t in threads if t.get('forkedFromId')}),
            'latest':max([t.get('updatedAt',0) for t in threads]+[0])}


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
            observed=inspect()
            if observed['threads'] or observed['claude']:raise RuntimeError('interrupted closure still has native writers; preserve the checkpoint')
            saved['phase']='closed';atomic(path,saved);return saved
    observed=inspect()
    value=workspace.inventory(operation,refresh=True,persist=False,same_host=True)
    # A native fork keeps its idle parent loaded without a pane. Its history
    # stays on disk; the open child is the discussion to resume.
    if set(observed['threads'])-set(value['discussions'])-set(observed.get('fork_parents',[])):
        raise RuntimeError('Codex has discussions outside the saved tmux workspace')
    if set(observed['claude'])-set(value.get('claude',[])):
        raise RuntimeError('Claude Code has discussions outside the saved tmux workspace')
    rows=[p for s in value['sessions'] for w in s['windows'] for p in w['panes']]
    panes=[p['source_pane'] for p in rows if p['discussion']]
    claude=[p['source_pane'] for p in rows if p.get('claude')]
    with workspace.frozen_tuis(panes),workspace.frozen_tuis(claude,'claude'):
        # Check again after preventing TUI input; never interrupt a turn.
        if set(inspect()['claude'])!=set(value.get('claude',[])):
            raise RuntimeError('Claude Code discussions changed while fencing; retry when they are idle')
        saved={'phase':'closing','workspace':value};atomic(path,saved)
        stop_daemon(handoff.codex_binary())
        # Claude Code appends each message to its transcript. Closing the
        # idle pane ends the client; native --resume continues it later.
        for p in rows:
            if p['discussion'] or p.get('claude'):
                workspace.tmux('respawn-pane','-k','-t',p['source_pane'],'-c',p['cwd'],'/bin/bash -l')
    deadline=time.monotonic()+30
    while set(value.get('claude',[]))&workspace.claude_live():
        if time.monotonic()>=deadline:raise RuntimeError('a closed Claude Code client is still running; preserve the checkpoint')
        time.sleep(0.5)
    saved['phase']='closed';atomic(path,saved)
    return saved


def restore(operation):
    path=handoff.ROOT/'workspaces'/operation/'image.json'
    # The maintenance reservation decides whether a missing checkpoint is a defect.
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
    for binary in (handoff.HOME.parent/'.local/bin/codex',handoff.HOME/'packages/app-server-daemon/current/bin/codex'):
        if binary.exists():
            old=subprocess.check_output([str(binary),'--version'],text=True,timeout=15).strip().split()[-1]
            if version(old)>version(record['version']):raise RuntimeError('image would downgrade a retained Codex package')
    stop_daemon('/usr/local/bin/codex')
    for target,source in ((handoff.HOME.parent/'.local/bin/codex','/usr/local/bin/codex'),
                          (handoff.HOME/'packages/app-server-daemon/current','/opt/codex')):
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and not target.is_symlink():raise RuntimeError('unmanaged Codex path requires explicit recovery: '+str(target))
        temporary=target.with_name(target.name+'.image-selection')
        if temporary.is_symlink():temporary.unlink()
        temporary.symlink_to(source);temporary.replace(target)
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
