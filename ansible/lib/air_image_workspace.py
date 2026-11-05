"""Same-machine workspace checkpoints; native history remains on retained storage."""
import json
import os
from pathlib import Path
import subprocess

import codex_handoff as handoff
import codex_workspace as workspace
from codex_live import LiveRPC
from codex_discussion import atomic


def inspect():
    binary=handoff.codex_binary()
    daemon=json.loads(subprocess.check_output([binary,'app-server','daemon','version'],text=True,timeout=15))
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
    return {'threads':[t['id'] for t in threads], 'latest':max([t.get('updatedAt',0) for t in threads]+[0])}


def checkpoint(operation):
    path=handoff.ROOT/'workspaces'/operation/'image.json'
    if path.exists():
        saved=json.loads(path.read_text())
        if saved['phase']=='closed':return saved
        if saved['phase']=='closing':
            if inspect()['threads']:raise RuntimeError('interrupted closure still has native writers; preserve the checkpoint')
            saved['phase']='closed';atomic(path,saved);return saved
    observed=inspect()
    value=workspace.inventory(operation,refresh=True,persist=False,same_host=True)
    if set(observed['threads'])-set(value['discussions']):
        raise RuntimeError('Codex has discussions outside the saved tmux workspace')
    panes=[p['source_pane'] for s in value['sessions'] for w in s['windows'] for p in w['panes'] if p['discussion']]
    with workspace.frozen_tuis(panes):
        inspect()  # Check again after preventing TUI input; never interrupt a turn.
        saved={'phase':'closing','workspace':value};atomic(path,saved)
        subprocess.run([handoff.codex_binary(),'app-server','daemon','stop'],check=True,timeout=90)
        for s in value['sessions']:
            for w in s['windows']:
                for p in w['panes']:
                    if p['discussion']:
                        workspace.tmux('respawn-pane','-k','-t',p['source_pane'],'-c',p['cwd'],'/bin/bash -l')
    saved['phase']='closed';atomic(path,saved)
    return saved


def restore(operation):
    path=handoff.ROOT/'workspaces'/operation/'image.json'
    if not path.exists():return {'restored':False}  # Historical operation.
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
    subprocess.run(['/usr/local/bin/codex','app-server','daemon','stop'],check=True,timeout=90)
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


if __name__=='__main__':main()
