#!/usr/bin/env python3
"""Runner workspace and conservative activity evidence. No background process."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import uuid

STATE = Path('/var/lib/klokast/air')
REPO = Path('/home/agent/src/klokast/klokast-box')
HOLD = 7200
HANDOFFS=Path('/home/agent/.local/state/klokast/handoffs')


def run(argv, *, user=False, check=True, timeout=15):
    if user and os.geteuid() == 0:
        argv = ['su', '-s', '/bin/sh', 'agent', '-c', shlex.join(argv)]
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('workspace command failed: ' + argv[0] + ': ' + result.stderr.strip()[-200:])
    return result


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write(path, value):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.record-', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    try:
        temporary.chmod(0o644)
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def processes(uid, proc=Path('/proc')):
    rows = {}
    for directory in proc.glob('[0-9]*'):
        try:
            fields = dict(line.split(':', 1) for line in (directory / 'status').read_text().splitlines() if ':' in line)
            argv = (directory / 'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            rows[int(directory.name)] = {'parent': int(fields['PPid']),
                'owned': str(uid) in fields['Uid'].split(), 'argv': argv, 'name': fields['Name'].strip(),
                'state': fields['State'].split()[0]}
        except (FileNotFoundError, ProcessLookupError):
            continue
    return rows


def classify(rows, probe_pid, *, live_codex=True):
    """Exclude this probe's ancestors, known idle shells and tmux infrastructure.

    Unknown commands and detached work remain busy. A shell with arguments is
    work even if its process name looks like an interactive shell.
    """
    ignored = set()
    pid = probe_pid
    while pid in rows and pid not in ignored:
        ignored.add(pid)
        pid = rows[pid]['parent']
    busy = []
    for pid, row in rows.items():
        if not row['owned'] or pid in ignored:
            continue
        if row.get('state') == 'Z':
            continue  # Exited processes cannot run work or retain open files.
        argv = row['argv']; name = row['name']
        if name.startswith('tmux:') or (argv and Path(argv[0]).name == 'tmux'):
            continue
        if argv and Path(argv[0]).name.lstrip('-') in ('sh', 'ash', 'bash', 'zsh', 'fish'):
            if all(a in ('-l', '-i', '--login') for a in argv[1:]):
                continue  # Its children are inspected separately.
        if name == 'mosh-server':
            continue  # Its shells/work and tmux attached clients are inspected.
        if not live_codex and argv and Path(argv[0]).name == 'codex' and (
                argv[1:] == ['app-server', 'daemon', 'pid-update-loop'] or
                argv[1:] == ['app-server', '--listen', 'unix://', '--analytics-default-enabled', '--managed-daemon']):
            continue  # No native thread writer exists; unrelated children still block.
        busy.append(pid)
    return sorted(busy)


def known_codex(argv,threads):
    if not argv or Path(argv[0]).name!='codex':return False
    return (argv[1:]==['app-server','daemon','pid-update-loop'] or
            argv[1:]==['app-server','--listen','unix://','--analytics-default-enabled','--managed-daemon'] or
            len(argv)==3 and argv[1]=='resume' and argv[2] in threads)


def codex_writers(home=Path('/home/agent/.codex')):
    directory=home/'thread-writer-locks'
    if not directory.exists():return []
    locked=[]
    try:coordination=os.open(directory/'.coordination.lock',os.O_RDWR|os.O_NOFOLLOW)
    except FileNotFoundError:
        if list(directory.glob('*.lock')):raise RuntimeError('Codex writer coordination is unavailable')
        return []
    try:
        fcntl.flock(coordination,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for path in directory.glob('*.lock'):
            if path.name=='.coordination.lock':continue
            if not re.fullmatch('[0-9a-f-]{36}\\.lock',path.name):raise RuntimeError('unknown Codex writer lock')
            fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
            try:
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:locked.append(path.stem)
            finally:os.close(fd)
    finally:os.close(coordination)
    return sorted(locked)


def status():
    agent = pwd.getpwnam('agent')
    if os.geteuid() not in (0, agent.pw_uid):
        raise RuntimeError('run the workspace helper as agent or through controller maintenance')
    # tmux can sanitize control characters in a non-UTF-8 controller locale.
    # Use printable separators and split numeric fields from the right.
    result = run(['tmux', 'list-sessions', '-F', '#{session_name}|#{session_attached}|#{session_activity}|#{session_last_attached}'], user=True, check=False)
    sessions = []
    if result.returncode:
        if not any(v in result.stderr for v in ('no server running', 'error connecting to', 'failed to connect to server')):
            raise RuntimeError('cannot inspect tmux sessions: ' + result.stderr.strip())
    else:
        for line in result.stdout.splitlines():
            fields=line.rsplit('|',3)
            if len(fields)!=4:raise RuntimeError('tmux returned an unsupported session status; keep the runner available for inspection')
            name, attached, activity, last_attached = fields
            sessions.append({'name': name, 'attached': int(attached),
                             'activity': max(int(activity or 0), int(last_attached or 0))})
    threads=codex_writers();rows=processes(agent.pw_uid)
    busy = classify(rows, os.getpid(), live_codex=bool(threads))
    unknown=[pid for pid in busy if not known_codex(rows[pid]['argv'],threads)]
    now = int(time.time())
    maintenance = read(STATE / 'maintenance.json')
    pending = bool(maintenance and maintenance['phase'] == 'pending')
    active = bool(maintenance and (pending or maintenance['expires_at'] > now))
    placement=read(STATE/'placement.json')
    if placement and (placement['phase']!='ready' or placement['current']!=socket.gethostname().split('-air')[0]):
        pending=True;active=True
    return {'schema_version': 1, 'hostname': socket.gethostname().split('.')[0],
            'available': REPO.is_dir() and bool(run(['sh', '-c', 'command -v tmux && command -v mosh-server'], check=False).returncode == 0),
            'workspace': any(s['name'] == 'main' for s in sessions),
            'maintenance': active, 'pending': pending, 'reservation': maintenance if active else None,
            'placement':placement,
            'attached': sum(s['attached'] for s in sessions), 'sessions': sessions,
            'busy_pids': busy, 'unknown_busy_pids': unknown, 'codex_threads': threads, 'observed_at': now}


def idle_check(value, *, now=None):
    """Controller-owned sampling starts a hold on first observed idle state."""
    now = int(time.time()) if now is None else now
    previous = read(STATE / 'activity.json', {})
    busy = value['attached'] > 0 or bool(value['busy_pids']) or bool(value.get('codex_threads'))
    latest = max([s['activity'] for s in value['sessions']] + [0])
    if busy:
        current = {'idle_since': None, 'last_busy': now, 'latest_tmux': latest,
                   'last_threads':value.get('codex_threads',[]),
                   'last_unknown_work':len(value.get('unknown_busy_pids',value['busy_pids'])),
                   'last_attached':value['attached']}
    else:
        idle = previous.get('idle_since')
        if idle is None or latest > previous.get('latest_tmux', 0):
            idle = now
        current = dict(previous,idle_since=idle,last_busy=previous.get('last_busy',now),latest_tmux=latest)
    write(STATE / 'activity.json', current)
    remaining = HOLD if busy else max(0, HOLD - (now - current['idle_since']))
    return dict(value, idle=not busy and remaining == 0, hold_seconds=remaining)


def handoff_receipt(path,limit=16384):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'r') as stream:
        content=stream.read(limit+1)
    if len(content)>limit:raise RuntimeError('handoff receipt exceeds its bound')
    return json.loads(content)


def workspace_handoff_complete(operation):
    """Release completed workspace work, with current placement and no writers."""
    placement=read(STATE/'placement.json',{})
    if (placement.get('operation')!=operation or placement.get('phase')!='ready' or
            placement.get('origin')!=socket.gethostname().split('-air')[0] or not placement.get('current') or
            placement.get('current')!=placement.get('destination') or
            placement.get('current')==placement.get('origin')):
        raise RuntimeError('workspace handoff has no verified destination placement')
    value=handoff_receipt(HANDOFFS/'workspaces'/operation/'manifest.json',262144)
    sessions=value.get('discussions',[])
    if (value.get('operation')!=operation or value.get('phase')!='closed' or
            not isinstance(sessions,list) or not 1<=len(sessions)<=32 or
            not all(isinstance(s,str) for s in sessions) or len(set(sessions))!=len(sessions)):
        raise RuntimeError('source workspace closure is incomplete')
    completed=[]
    for session in sessions:
        if str(uuid.UUID(session))!=session:raise RuntimeError('invalid workspace discussion identity')
        child=hashlib.sha256((operation+session).encode()).hexdigest()[:24]
        moved=handoff_receipt(HANDOFFS/child/'source.json')
        ledger=handoff_receipt(HANDOFFS/'threads'/(session+'.json'))
        if (moved.get('phase')!='moved' or moved.get('session')!=session or
                session not in moved.get('threads',[]) or
                ledger!={'operation':child,'phase':'moved','sha256':moved.get('sha256')} or
                not re.fullmatch('[0-9a-f]{64}',str(moved.get('sha256'))) or
                type(moved.get('completed_at')) is not int or moved['completed_at']<=0):
            raise RuntimeError('workspace discussion handoff is incomplete or has changed')
        completed.append(moved['completed_at'])
    observed=status()
    latest=max([s['activity'] for s in observed['sessions']]+[0])
    if (observed['attached'] or observed['busy_pids'] or observed.get('codex_threads') or
            latest>max(completed)):
        return {'released':False,'reason':'other work or later workspace activity retains its reservation'}
    previous=read(STATE/'activity.json',{})
    previous.update(idle_since=int(time.time())-HOLD,latest_tmux=latest,
                    last_threads=[],last_unknown_work=0,last_attached=0)
    write(STATE/'activity.json',previous)
    return {'released':True}


def handoff_complete(operation,session):
    """Release only verified completed work; uncertain completion keeps its hold."""
    if os.geteuid()!=0:raise RuntimeError('handoff hold release requires controller maintenance')
    if not re.fullmatch('[0-9a-f]{24}',operation):
        raise RuntimeError('invalid handoff identity')
    if not session:return workspace_handoff_complete(operation)
    if str(uuid.UUID(session))!=session:raise RuntimeError('invalid handoff identity')
    home=HANDOFFS
    moved=handoff_receipt(home/operation/'source.json');ledger=handoff_receipt(home/'threads'/(session+'.json'))
    if (moved.get('phase')!='moved' or moved.get('session')!=session or
            ledger!={'operation':operation,'phase':'moved','sha256':moved['sha256']}):
        raise RuntimeError('source discussion has not completed this handoff')
    ids=moved.get('threads',[])
    if not ids or session not in ids:raise RuntimeError('source handoff has no qualified thread receipt')
    observed=status();previous=read(STATE/'activity.json',{})
    # Attached clients and unknown work keep their own hold. New tmux activity
    # after export also keeps the hold. No process is stopped by this action.
    if (observed['attached'] or observed['busy_pids'] or observed.get('codex_threads') or
            previous.get('last_unknown_work',1) or previous.get('last_attached',1) or
            not previous.get('last_threads') or not set(previous['last_threads']).issubset(ids) or
            max([s['activity'] for s in observed['sessions']]+[0])>moved['completed_at']):
        return {'released':False,'reason':'other work or recent activity retains its hold'}
    previous.update(idle_since=int(time.time())-HOLD,last_threads=[])
    write(STATE/'activity.json',previous)
    return {'released':True}


def reservation(action, operation, ttl, previous=None):
    if os.geteuid() != 0:
        raise RuntimeError('maintenance requires the existing controller maintenance account')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', operation):
        raise RuntimeError('invalid maintenance operation ID')
    now = int(time.time())
    current = read(STATE / 'maintenance.json')
    if current and (current['phase'] == 'pending' or current['expires_at'] > now):
        if current['operation'] != operation and not (
                action == 'begin' and current['phase'] == 'planned' and current['operation'] == previous):
            raise RuntimeError('runner is reserved by another operation: ' + current['operation'])
        if current['phase'] == 'pending' and action == 'reserve':
            raise RuntimeError('replacement is pending; resume its recorded transaction')
    if action in ('clear', 'complete'):
        if current and current['phase'] == 'pending':
            if action != 'complete':
                raise RuntimeError('pending replacement requires verification before release')
        (STATE / 'maintenance.json').unlink(missing_ok=True)
        return {'cleared': True}
    if action == 'begin':
        checked = idle_check(status())
        if not checked['idle']:
            raise RuntimeError('runner has attached clients, running work or a recent-use hold (' + str(checked['hold_seconds']) + ' seconds)')
    value = {'schema_version': 1, 'operation': operation,
             'phase': 'pending' if action == 'begin' else 'planned', 'expires_at': now + ttl}
    write(STATE / 'maintenance.json', value)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'ready', 'open', 'idle', 'reserve', 'clear', 'begin', 'complete', 'handoff-complete','placement'])
    parser.add_argument('--reconnect', action='store_true')
    parser.add_argument('--operation')
    parser.add_argument('--session')
    parser.add_argument('--reservation', help=argparse.SUPPRESS)
    parser.add_argument('--ttl', type=int, default=1800)
    args = parser.parse_args(argv)
    try:
        if args.action=='placement':
            if os.geteuid()!=0:raise RuntimeError('workspace placement requires controller maintenance')
            raw=sys.stdin.read(16385)
            if len(raw)>16384:raise RuntimeError('workspace placement exceeds its bound')
            value=json.loads(raw)
            if (set(value)!={'version','operation','phase','current','origin','destination'} or value['version']!=1 or
                    not re.fullmatch('[0-9a-f]{24}',value['operation']) or value['phase'] not in ('ready','draining','starting') or
                    any(not re.fullmatch('[a-z][a-z0-9-]{0,30}',value[k]) for k in ('origin','destination')) or
                    value['current'] not in (None,value['origin'],value['destination']) or
                    (value['phase']=='ready' and value['current'] is None)):
                raise RuntimeError('workspace placement record is invalid')
            write(STATE/'placement.json',value);print(json.dumps(value))
        elif args.action == 'open':
            if os.geteuid() != pwd.getpwnam('agent').pw_uid:
                raise RuntimeError('open the workspace as agent')
            with (STATE / 'workspace.lock').open('r') as lock:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                value = status()
                placement=value.get('placement')
                if placement and placement['phase']=='ready' and placement['current']!=socket.gethostname().split('-air')[0]:
                    print('air workspace: the shared workspace is on '+placement['current']+'-air. '
                          'This retained workspace remains accessible; continuing a discussion here '
                          'can create a second, different copy.',file=sys.stderr)
                if (value['pending'] or value['maintenance']) and (
                        not placement or placement['phase']!='ready' or value.get('reservation')):
                    print('air workspace: maintenance or workspace transfer is pending; '
                          'please finish work and detach before replacement. '
                          'This connection remains available for recovery.', file=sys.stderr)
                if not value['available']:
                    raise RuntimeError('runner workspace is not ready')
                return subprocess.call(['tmux', 'new-session', '-A', '-s', 'main', '-c', str(REPO)])
        elif args.action == 'status':
            print(json.dumps(status()))
        elif args.action == 'ready':
            value=status()
            if not value['available'] or value['maintenance']:
                raise RuntimeError('runner is unavailable or in maintenance')
            ready=json.loads(run(['/usr/local/bin/klokast-codex-handoff','ready'],user=True,timeout=75).stdout)
            print(json.dumps(dict(value,authenticated=ready['authenticated'])))
        elif args.action == 'idle':
            if os.geteuid() != 0:
                raise RuntimeError('idle sampling requires controller maintenance')
            print(json.dumps(idle_check(status())))
        elif args.action == 'handoff-complete':
            with (STATE/'workspace.lock').open('r') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                print(json.dumps(handoff_complete(args.operation or '',args.session or '')))
        else:
            if not args.operation or not 60 <= args.ttl <= 86400:
                raise RuntimeError('maintenance needs --operation and a TTL from 60 to 86400 seconds')
            if args.action == 'begin':
                with (STATE / 'workspace.lock').open('r') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    print(json.dumps(reservation(args.action, args.operation, args.ttl, args.reservation)))
            else:
                print(json.dumps(reservation(args.action, args.operation, args.ttl)))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print('air workspace: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
