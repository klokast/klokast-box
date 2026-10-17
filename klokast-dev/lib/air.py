"""Device-local selection and OpenSSH/Mosh transport for native runners."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HELPER = '/usr/local/bin/klokast-air-workspace'
BOX = re.compile(r'[a-z][a-z0-9-]{0,30}')
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
       '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2']


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON field: ' + key)
        value[key] = item
    return value


def read_json(path):
    return json.loads(path.read_text(), object_pairs_hook=unique)


def declarations(path):
    if not path:
        raise ValueError('select a private Instance worktree with --instance PATH or KLOKAST_INSTANCE')
    value = read_json(Path(path).expanduser() / 'klokast-instance.json')
    if type(value.get('schema-version')) is not int or value['schema-version'] != 1:
        raise ValueError('Instance schema-version must be 1')
    suffix = value.get('tailscale', {}).get('tailnet-dns-name', '')
    if not isinstance(suffix, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*\.)+ts\.net', suffix):
        raise ValueError('Instance needs a valid tailscale.tailnet-dns-name')
    names = value.get('airunners')
    if (not isinstance(names, list) or not names or
            any(not isinstance(n, str) for n in names) or len(names) != len(set(names))):
        raise ValueError('Instance airunners must contain unique runner names')
    boxes = []
    for name in names:
        if name.endswith('-air'):
            box = name[:-4]
            if not BOX.fullmatch(box) or box not in value.get('boxes', {}):
                raise ValueError('runner references an invalid or undeclared box: ' + name)
            boxes.append(box)
    if not boxes:
        raise ValueError('kk air requires a declared native <box>-air runner')
    return suffix, sorted(boxes)


def config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'klokast/air.json'


@contextlib.contextmanager
def settings(suffix):
    path = config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = read_json(path) if path.exists() else {'schema_version': 1, 'instances': {}}
        if value.get('schema_version') != 1 or not isinstance(value.get('instances'), dict):
            raise ValueError('unsupported device air settings; inspect ' + str(path))
        item = value['instances'].setdefault(suffix, {'prefer': ['k002', 'k001'], 'last': None})
        if (not isinstance(item.get('prefer'), list) or
                any(not isinstance(b, str) or not BOX.fullmatch(b) for b in item['prefer']) or
                (item.get('last') is not None and not BOX.fullmatch(item['last']))):
            raise ValueError('invalid device runner preference or selection')
        yield item
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.air-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def probe(box):
    try:
        result = subprocess.run([*SSH, 'agent@' + box + '-air', HELPER, 'status'],
                                text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise ValueError('SSH or workspace probe failed: ' + result.stderr.strip()[-300:])
        value = json.loads(result.stdout, object_pairs_hook=unique)
        if (value.get('schema_version') != 1 or value.get('hostname') != box + '-air' or
                any(type(value.get(k)) is not bool for k in ('available', 'workspace', 'maintenance')) or
                type(value.get('attached')) is not int or not isinstance(value.get('busy_pids'), list)):
            raise ValueError('runner returned an incompatible workspace status')
        return value
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'available': False, 'workspace': False, 'maintenance': True,
                'attached': 0, 'busy_pids': [], 'error': str(error)}


def ordered(boxes, preference, last):
    return list(dict.fromkeys(b for b in [last, *preference, *sorted(boxes)] if b in boxes))


def choose(boxes, preference, last, explicit=None, inspect=probe):
    if explicit and explicit not in boxes:
        raise ValueError('runner is not declared in Instance: ' + explicit)
    errors = []
    observed = {box:inspect(box) for box in boxes}
    if explicit:
        status=observed[explicit]
        if not status['available']:
            raise ValueError(explicit+': '+status.get('error','runner is unavailable'))
        placement=status.get('placement')
        if placement and placement.get('phase')=='ready' and placement.get('current')!=explicit:
            print('Warning: the shared workspace is on '+str(placement['current'])+'-air; '
                  'the retained '+explicit+'-air copy remains accessible.',file=sys.stderr)
        elif status['maintenance'] or status.get('pending'):
            print('Warning: '+explicit+'-air has maintenance or a workspace move pending; '
                  'finish work and detach before replacement.',file=sys.stderr)
        return explicit,bool(status['workspace'])
    placements=[s['placement'] for s in observed.values() if s.get('placement')]
    if placements:
        placement=placements[0]
        if any(p!=placement for p in placements):
            raise ValueError('workspace placement is changing or conflicting; retry after its recorded move completes')
        if placement.get('phase')!='ready':
            raise ValueError('workspace move is in progress; reconnect after operation '+str(placement.get('operation')))
        current=placement.get('current')
        if current not in boxes or (explicit and explicit!=current):
            raise ValueError('the only valid workspace placement is '+str(current)+'-air')
        status=observed[current]
        if not status['available'] or status.get('pending') or not status['workspace']:
            raise ValueError('the current workspace is unavailable; refusing to open another copy')
        return current,True
    if not explicit and last in boxes:
        if not observed[last]['workspace']:
            last = None
    for box in [explicit] if explicit else ordered(boxes, preference, last):
        status = observed[box] if box in observed else inspect(box)
        reconnect = status['workspace'] and (box == last or box == explicit)
        if status['available'] and (not status['maintenance'] or reconnect) and not status.get('pending', False):
            return box, reconnect
        errors.append(box + ': ' + status.get('error', 'replacement pending' if status.get('pending') else 'maintenance or unavailable'))
    raise ValueError('no usable runner; ' + '; '.join(errors))


def attach(box, reconnect=False):
    if not shutil.which('mosh'):
        raise ValueError('Mosh is missing; run kk doctor --install')
    print('Connecting to ' + box + '-air (tmux main)', flush=True)
    # Mosh invokes OpenSSH, including the operator device's existing config.
    argv = ['mosh', '--ssh=ssh -o ConnectTimeout=8', 'agent@' + box + '-air',
            '--', HELPER, 'open']
    if reconnect:
        argv.append('--reconnect')
    return subprocess.run(argv, check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description='Open the remote main tmux workspace over Mosh.')
    parser.add_argument('--instance', default=os.environ.get('KLOKAST_INSTANCE', ''))
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--box')
    group.add_argument('--prefer', help='save this device preference, for example k002,k001')
    group.add_argument('--status', action='store_true')
    args = parser.parse_args(argv)
    try:
        suffix, boxes = declarations(args.instance)
        with settings(suffix) as current:
            if args.prefer is not None:
                preference = args.prefer.split(',')
                if (not preference or len(set(preference)) != len(preference) or
                        any(b not in boxes for b in preference)):
                    raise ValueError('--prefer requires unique declared boxes separated by commas')
                current.update(prefer=preference, last=None,device_preference=True)
                print('Saved device preference: ' + ','.join(preference) + '; cleared remembered runner')
                return 0
            preference, last = current['prefer'][:], current.get('last')
            if not current.get('device_preference'):
                declared=read_json(Path(args.instance).expanduser()/'klokast-instance.json')['airunners']
                default=next((n[:-4] for n in declared if n.endswith('-air')),None)
                preference=list(dict.fromkeys([default,*preference])) if default else preference
        if args.status:
            print('Device preference: ' + ','.join(preference) + '; selected: ' + (last or 'none'))
            for box in ordered(boxes, preference, last):
                status = probe(box)
                if status.get('error'):
                    print('{}-air available=False maintenance=unknown workspace=unknown attached=unknown work=unknown error={}'.format(
                        box, status['error']))
                    continue
                placement=status.get('placement')
                maintenance=bool(status.get('reservation')) if placement else status['maintenance']
                detail=(' workspace_on={} move={}'.format(placement.get('current') or 'none',placement['phase'])) if placement else ''
                print('{}-air available={} maintenance={} workspace={} attached={} work={}{}'.format(
                    box, status['available'], maintenance, status['workspace'], status['attached'],
                    len(status['busy_pids']),detail))
            return 0
        if not shutil.which('mosh'):
            raise ValueError('Mosh is missing; run kk doctor --install')
        box, reconnect = choose(boxes, preference, last, args.box)
        # Save before attach so a dropped client can reconnect. A failed probe on
        # the next invocation still permits the normal fallback.
        with settings(suffix) as current:
            current['last'] = box
        return attach(box, reconnect)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        print('kk air: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
