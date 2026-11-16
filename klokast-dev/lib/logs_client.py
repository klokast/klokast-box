"""kk logs: review, expire, view and download the Platform log files.

Each controller reports and expires its own files. The active controller reports and expires
the files of dom0 and the other VMs. The controllers apply the checked-in retention policy;
see ansible/ansible.md#log-retention.
"""
import argparse
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
import tempfile

import air
import platform_client as client

TOOL = client.REMOTE_REPO + '/ansible/bin/platform-logs'
JOURNAL = '/var/lib/klokast/operations-journal'
SCAN_SECONDS = 1900
FETCH_SECONDS = 600


def size(value):
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if value < 1024 or unit == 'GiB': return ('%d %s' if unit == 'B' else '%.1f %s') % (value, unit)
        value /= 1024


def controllers(instance):
    """Return the reachable declared controllers, the active box and the errors."""
    boxes, active, errors = [], None, []
    for box in client.declared_controllers(instance):
        try:
            state = client.remote('smith', box + '-ops', ['/usr/local/sbin/klokast-controller-guard', '--status', '--json'])
        except (RuntimeError, subprocess.SubprocessError) as error:
            errors.append(box + '-ops is unreachable: ' + str(error)[-300:]); continue
        boxes.append(box)
        if state.get('active') and state.get('role') == 'active' and state.get('hostname') == box + '-ops': active = box
    return boxes, active, errors


def collect(instance, action, extra=()):
    """Run one platform-logs action on each controller and on the platform scope of the active one."""
    boxes, active, errors = controllers(instance)
    machines, logs = [], []
    calls = [(box, 'local') for box in boxes] + ([(active, 'platform')] if active else [])
    if not active: errors.append('no single active controller; dom0 and the other VMs are not shown')
    for box, scope in calls:
        try:
            value = client.remote('smith', box + '-ops', [TOOL, action, '--scope', scope, *extra], timeout=SCAN_SECONDS)
            machines += value['machines']; logs.append(box + '-ops:' + value['log'])
        except (RuntimeError, subprocess.SubprocessError, KeyError, TypeError) as error:
            errors.append(box + '-ops ' + scope + ' scope failed: ' + str(error)[-300:])
    return sorted(machines, key=lambda m: m['machine']), active, errors, logs


def report(instance):
    """Print the log situation of the Platform. Return 1 when an item needs attention."""
    print('---------------------- kk logs ----------------------', flush=True)
    machines, active, errors, logs = collect(instance, 'scan')
    attention = len(errors)
    client.section('Machines')
    rows = [('MACHINE', 'STATE', 'FILES', 'SIZE', 'EXPIRED', 'UNREGISTERED', 'LARGE')]
    for value in machines:
        totals = value.get('totals')
        if value['state'] not in ('reachable', 'stopped'): attention += 1
        if not totals:
            rows.append((value['machine'], value['state'], '-', '-', '-', '-', value.get('error', '')))
            continue
        attention += bool(totals['unregistered'] or totals['large'])
        rows.append((value['machine'], value['state'], str(totals['files']), size(totals['bytes']),
                     '%d (%s)' % (totals['expire'], size(totals['expire_bytes'])),
                     str(totals['unregistered']), str(totals['large'])))
    client.table(rows)
    client.section('Largest groups')
    groups = sorted(((v['machine'], r) for v in machines for r in v.get('roots', [])), key=lambda g: -g[1]['files'])
    rows = [('MACHINE', 'ROOT', 'RULE', 'FILES', 'SIZE', 'EXPIRED', 'OLDEST')]
    for machine, value in groups[:15]:
        rows.append((machine, value['root'], value['rule'], str(value['files']), size(value['bytes']),
                     str(value['expire']), client.utc(value['oldest'])))
    client.table(rows)
    client.section('Operations journal')
    rows = [('CONTROLLER', 'ROLE', 'CASES', 'FORUM', 'SIZE', 'OLDEST', 'NEWEST', 'OTHER FILES')]
    for value in machines:
        groups = {g['rule']: g for g in value.get('roots', []) if g['root'] == JOURNAL}
        if not groups: continue
        entries = groups.get('audit', {'children': {}, 'bytes': 0, 'oldest': None, 'newest': None})
        other = groups.get('age', {'files': 0, 'expire': 0, 'bytes': 0})
        rows.append((value['machine'], 'active' if value['machine'] == str(active) + '-ops' else 'standby',
                     str(entries.get('children', {}).get('cases', 0)), str(entries.get('children', {}).get('forum', 0)),
                     size(entries['bytes']), client.utc(entries['oldest']), client.utc(entries['newest']),
                     '%d (%s), %d expired' % (other['files'], size(other['bytes']), other['expire'])))
    if len(rows) > 1: client.table(rows)
    else: client.bullet('No journal entry found.')
    client.bullet('CASES and FORUM count entries; no rule deletes them. Other files expire after 14 days.')
    client.section('Attention')
    for error in errors: client.bullet(error)
    for value in machines:
        for row in value.get('unregistered', []):
            client.bullet('%s: unregistered %s (%s); add a rule or stop its producer' % (value['machine'], row['path'], size(row['bytes'])))
        large = value.get('large', [])
        if len(large) > 3:
            client.bullet('%s: %d large files (%s); see `kk logs list %s`' % (
                value['machine'], value['totals']['large'], size(sum(r['bytes'] for r in large)), value['machine']))
            continue
        for row in large:
            client.bullet('%s: large %s (%s)' % (value['machine'], row['path'], size(row['bytes'])))
    if not attention: client.bullet('Nothing.')
    client.section('Help')
    client.bullet('`kk logs list MACHINE` to list the files of one machine')
    client.bullet('`kk logs clean` to show the expired files; `kk logs clean --apply` to delete them')
    client.bullet('`kk logs view MACHINE PATH` to read one file in vi; `kk logs get MACHINE PATH` to download it')
    client.bullet('The nightly run applies the same policy on each controller.')
    for log in logs: client.bullet('Run log: `' + log + '`')
    client.section('Summary')
    client.bullet('No problem found.' if not attention else '%d item(s) need attention; see above.' % attention)
    client.report_footer()
    return 1 if attention else 0


def route(instance, machine):
    """Return the controller box and the scope that own MACHINE."""
    boxes, active, errors = controllers(instance)
    if machine.endswith('-ops'):
        box = machine[:-4]
        if box not in client.declared_controllers(instance): raise ValueError(machine + ' is not a declared controller')
        if box not in boxes: raise RuntimeError('; '.join(errors) or machine + ' is unreachable')
        return box, 'local'
    if not active: raise RuntimeError('no single active controller; reconcile controller authority')
    return active, 'platform'


def listing(instance, machine, expired):
    box, scope = route(instance, machine)
    value = client.remote('smith', box + '-ops', [TOOL, 'scan', '--scope', scope, '--machine', machine, '--files'],
                          timeout=SCAN_SECONDS)['machines'][0]
    if 'files' not in value: raise RuntimeError(machine + ' is ' + value['state'] + ' ' + value.get('error', ''))
    rows = [('MODIFIED', 'SIZE', 'RULE', 'STATE', 'PATH')]
    for row in sorted(value['files'], key=lambda r: (r['mtime'], r['path'])):
        if expired and row['state'] == 'keep': continue
        rows.append((client.utc(row['mtime']), size(row['bytes']), str(row['rule'] or '-'),
                     row['state'] + (' large' if row['large'] else ''), row['path']))
    client.table(rows)
    print('%d file(s); policy: %d days, newest %d runs kept' % (len(rows) - 1, value['days'], value['runs']))
    return 0


def clean(instance, apply):
    machines, _, errors, logs = collect(instance, 'clean', ['--apply'] if apply else [])
    rows = [('MACHINE', 'STATE', 'EXPIRED', 'SIZE', 'DELETED', 'SESSIONS', 'ERRORS')]
    for value in machines:
        totals, deleted = value.get('totals'), value.get('deleted', {})
        rows.append((value['machine'], value['state'], str(totals['expire']) if totals else '-',
                     size(totals['expire_bytes']) if totals else '-', str(deleted.get('files', 0)),
                     str(deleted.get('sessions', 0)),
                     str(len(value.get('errors', []))) if totals else value.get('error', '')))
        errors += [value['machine'] + ': ' + e for e in value.get('errors', [])]
    client.table(rows)
    for error in errors: client.bullet(error)
    for log in logs: client.bullet('Run log: `' + log + '`')
    if not apply: print('\nNothing was deleted. Run `kk logs clean --apply` to delete the expired files.')
    return 1 if errors else 0


def require_macbook():
    # Log contents are Platform private state; they must not reach a runner.
    if platform.system() != 'Darwin': raise RuntimeError('view and get run only on the operator MacBook')


def fetch(instance, machine, path, destination):
    """Copy one listed file from its controller into destination, which must not exist."""
    require_macbook()
    box, _ = route(instance, machine)
    with destination.open('xb') as stream:
        os.chmod(destination, 0o600)
        result = subprocess.run([*air.SSH, 'smith@' + box + '-ops', shlex.join([TOOL, 'fetch', '--machine', machine, '--path', path])],
                                stdout=stream, stderr=subprocess.PIPE, timeout=FETCH_SECONDS)
    if result.returncode:
        destination.unlink()
        raise RuntimeError(result.stderr.decode(errors='replace').strip()[-1500:] or 'the copy failed')


def view(instance, machine, path):
    require_macbook()
    with tempfile.TemporaryDirectory(prefix='kk-logs.') as directory:
        local = Path(directory) / Path(path).name
        fetch(instance, machine, path, local)
        return subprocess.run(['vi', '-R', str(local)]).returncode


def get(instance, machine, path, directory):
    require_macbook()
    target = Path(directory).expanduser() / machine
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    local = target / Path(path).name
    if local.exists(): raise RuntimeError(str(local) + ' exists; move it or use --to DIRECTORY')
    fetch(instance, machine, path, local)
    print(str(local))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='kk logs', description=__doc__)
    parser.add_argument('--instance', default=os.environ.get('KLOKAST_INSTANCE', ''))
    sub = parser.add_subparsers(dest='action')
    s = sub.add_parser('list', help='list the log files of one machine')
    s.add_argument('machine', metavar='MACHINE'); s.add_argument('--expired', action='store_true', help='only expired and unregistered files')
    s = sub.add_parser('clean', help='show the expired files, or delete them with --apply')
    s.add_argument('--apply', action='store_true')
    for name, text in (('view', 'read one file in vi'), ('get', 'download one file')):
        s = sub.add_parser(name, help=text); s.add_argument('machine', metavar='MACHINE'); s.add_argument('path', metavar='PATH')
        if name == 'get': s.add_argument('--to', default='~/Downloads/klokast-logs', metavar='DIRECTORY')
    args = parser.parse_args(argv)
    try:
        machine = getattr(args, 'machine', None)
        if machine is not None and not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', machine):
            raise ValueError('invalid machine name')
        if args.action is None: return report(args.instance)
        if args.action == 'list': return listing(args.instance, args.machine, args.expired)
        if args.action == 'clean': return clean(args.instance, args.apply)
        if args.action == 'view': return view(args.instance, args.machine, args.path)
        return get(args.instance, args.machine, args.path, args.to)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        print('kk logs: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__': raise SystemExit(main())
