#!/usr/bin/env python3
"""Find, audit and expire the log files of this machine with the checked-in Platform policy.

The controllers import this file. Ansible copies it unchanged to dom0 and the VMs
(76-platform-logs.yml). It uses only the Python standard library. It deletes only
log-named regular files and whole agent evidence entries. It never deletes records,
receipts, images or the operations journal. See ansible/ansible.md#log-retention.
"""
import argparse
import collections
import glob
import json
import os
import re
import shutil
import socket
import stat
import sys
import time

DAYS = 14
RUNS = 10
LARGE = 16 * 1024**2
FETCH_LIMIT = 32 * 1024**2
LISTED = 50
LOG = r'.+\.(?:log|stdout|stderr)(?:\.[0-9]+|\.previous|\.old|\.gz)?'
ANY = r'.+'
ROLES = ('dom0', 'ops', 'air', 'router', 'bak', 'dmz', 'iot', 'vpn-egress')
SMITH = '/home/smith/private/klokast'
AGENT = '/home/agent'

Rule = collections.namedtuple('Rule', 'roles root rule pattern purpose')
# A root can contain glob characters. The longest present root that contains a file owns it.
# runs: keep every log of the newest RUNS children; expire the older logs of the other children.
# age: expire the log files directly in the root that are older than DAYS.
# tree: remove a whole child when all its files are older than DAYS and it holds no KEEP file.
# audit: report only.
POLICY = (
    Rule(('ops',), '/var/lib/klokast/*', 'runs', LOG, 'controller operation records'),
    Rule(('ops',), '/var/lib/klokast/updates/discovery', 'runs', LOG, 'ad-hoc discovery runs'),
    Rule(('ops',), '/var/lib/klokast/updates/discovery/builds', 'runs', LOG, 'image builds and qualifications'),
    Rule(('ops',), '/var/lib/klokast/updates/discovery/router', 'runs', LOG, 'router candidates and tests'),
    Rule(('ops',), '/var/lib/klokast/updates/discovery/dom0-builds', 'runs', LOG, 'dom0 image builds'),
    Rule(('ops',), '/var/lib/klokast/updates/discovery/dom0-operations', 'runs', LOG, 'dom0 update operations'),
    Rule(('ops',), '/var/lib/klokast/updates/recovery', 'audit', LOG, 'update recovery records'),
    Rule(('ops',), '/var/lib/klokast/ops-nightly', 'audit', LOG, 'nightly runs; the coordinator keeps the last 10'),
    Rule(('ops',), '/var/lib/klokast/operations-journal', 'audit', ANY, 'operations journal; no rule deletes it'),
    Rule(('ops',), SMITH, 'age', LOG, 'loose controller logs'),
    Rule(('ops',), SMITH + '/*', 'runs', LOG, 'controller private operation records'),
    Rule(('ops',), SMITH + '/logs/*', 'runs', LOG, 'controller wrapper runs'),
    Rule(('ops',), SMITH + '/evidence', 'tree', ANY, 'agent evidence; a KEEP file keeps an entry'),
    Rule(('dom0',), '/var/log/xen', 'age', ANY, 'Xen domain logs'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-*', 'runs', LOG, 'dom0 operations and tests'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-*/operations', 'runs', LOG, 'dom0 update operations'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-*/staging', 'runs', LOG, 'dom0 image staging'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-infrastructure/qualification', 'runs', LOG, 'infrastructure qualifications'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-router-templates', 'audit', LOG, 'router templates; template retention owns them'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-router-compatibility', 'audit', LOG, 'router compatibility; template retention owns it'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-router-updates/cold-backups', 'audit', LOG, 'router cold backups'),
    Rule(('air',), AGENT + '/.npm/_logs', 'age', ANY, 'npm logs'),
    Rule(('air',), AGENT + '/.cache/claude-cli-nodejs/*/*', 'age', ANY, 'Claude Code MCP logs'),
    Rule(('air',), AGENT + '/.codex/log', 'age', ANY, 'Codex logs'),
    Rule(('air',), AGENT + '/.codex/app-server-daemon', 'age', LOG, 'Codex daemon logs'),
    Rule(('air',), AGENT + '/.codex', 'audit', r'.+\.jsonl|' + LOG, 'Codex conversations; handoff needs them'),
    Rule(('air',), AGENT + '/.claude', 'audit', r'.+\.jsonl|' + LOG, 'Claude Code conversations; handoff needs them'),
    Rule(('*',), '/var/lib/tailscale', 'audit', LOG, 'Tailscale state logs'),
    Rule(('*',), '/var/log', 'audit', ANY, 'system logs; the image owns their rotation'),
)
# Places where a log file that no rule owns is reported as unregistered.
SCAN = {
    'ops': ('/var/log', '/var/lib/klokast', SMITH),
    'dom0': ('/var/log', '/var/lib/klokast', '/mnt/dom0_data', '/root', '/home'),
    'air': ('/var/log', '/var/lib/klokast', '/root', '/home'),
    '*': ('/var/log', '/var/lib/klokast', '/root', '/home', '/srv'),
}
# Large trees that hold no Platform logs: sources, packages, container layers and guest file systems.
PRUNE = {'.git', 'node_modules', 'vendor', 'src', '.venv', 'venv', '_cacache', 'containers', 'overlay',
         'overlay-images', 'overlay-layers', 'vfs', 'vfs-images', 'vfs-layers', 'merged', 'diff',
         'rootfs', 'filesystem'}


def role_of(machine):
    match = re.fullmatch(r'[a-z][a-z0-9-]*?-(' + '|'.join(ROLES) + ')', machine)
    if not match: raise ValueError('not a Platform machine name: ' + machine)
    return match[1]


def present_roots(role, prefix=''):
    """Return (directory, rule) for each policy root that exists on this machine.

    A later rule replaces an earlier glob rule for the same directory, so the table lists
    general rules first.
    """
    found = {}
    for entry in POLICY:
        if role not in entry.roles and '*' not in entry.roles: continue
        for path in sorted(glob.glob(glob.escape(prefix) + entry.root)):
            try: info = os.lstat(path)
            except OSError: continue
            if stat.S_ISDIR(info.st_mode): found[path] = entry
    return sorted(found.items())


def owner(path, found):
    best = (None, None)
    for root, entry in found:
        if path.startswith(root + '/') and (best[0] is None or len(root) > len(best[0])): best = (root, entry)
    return best


def regular_files(starts):
    """Yield each regular file once. Do not follow links or cross into another file system."""
    seen = set()
    for start in sorted(set(starts)):
        try: device = os.lstat(start).st_dev
        except OSError: continue
        for parent, dirs, names in os.walk(start):
            kept = []
            for name in dirs:
                try: info = os.lstat(os.path.join(parent, name))
                except OSError: continue
                if name not in PRUNE and stat.S_ISDIR(info.st_mode) and info.st_dev == device: kept.append(name)
            dirs[:] = kept
            for name in names:
                path = os.path.join(parent, name)
                if path in seen: continue
                try: info = os.lstat(path)
                except OSError: continue
                if stat.S_ISREG(info.st_mode):
                    seen.add(path)
                    yield path, info


def record(path, info, root, entry, state):
    return {'path': path, 'bytes': info.st_size, 'mtime': int(info.st_mtime), 'root': root,
            'rule': entry.rule if entry else None, 'state': state, 'large': info.st_size > LARGE,
            'identity': (info.st_dev, info.st_ino, info.st_mtime_ns)}


def decide(root, entry, items, cutoff):
    """Return the file records of one root and the paths that expire as a whole unit."""
    child = lambda path: path[len(root) + 1:].split('/', 1)[0]
    newest = {}
    for path, info in items: newest[child(path)] = max(newest.get(child(path), 0), info.st_mtime)
    keep = set(sorted(newest, key=lambda name: (newest[name], name), reverse=True)[:RUNS])
    rows, units = [], []
    for path, info in items:
        old = info.st_mtime < cutoff
        if entry.rule == 'runs': expire = old and child(path) not in keep
        elif entry.rule == 'age': expire = old and '/' not in path[len(root) + 1:]
        elif entry.rule == 'tree':
            name = child(path)
            expire = newest[name] < cutoff and not os.path.lexists(os.path.join(root, name, 'KEEP'))
            if expire and os.path.join(root, name) not in units: units.append(os.path.join(root, name))
        else: expire = False
        rows.append(record(path, info, root, entry, 'expire' if expire else 'keep'))
        if expire and entry.rule != 'tree': units.append(path)
    return rows, units


def scan(machine, prefix='', now=None):
    """Classify every log file of this machine. Return the file records and the expired units."""
    role = role_of(machine)
    cutoff = (time.time() if now is None else now) - DAYS * 86400
    found = present_roots(role, prefix)
    starts = [root for root, _ in found] + [prefix + path for path in SCAN.get(role, SCAN['*'])]
    owned, rows = {}, []
    for path, info in regular_files(starts):
        root, entry = owner(path, found)
        name = os.path.basename(path)
        if entry is not None and re.fullmatch(entry.pattern, name):
            owned.setdefault(root, (entry, []))[1].append((path, info))
        elif re.fullmatch(LOG, name):
            rows.append(record(path, info, root, entry, 'unregistered'))
    units = []
    for root, (entry, items) in sorted(owned.items()):
        value, expired = decide(root, entry, items, cutoff)
        rows += value; units += expired
    return role, found, rows, units


def summary(machine, found, rows, files=False):
    """Count the files of each root. Audit roots also count their first-level children."""
    roots = {}
    for root, entry in found:
        roots.setdefault(root, {'root': root, 'rule': entry.rule, 'purpose': entry.purpose, 'files': 0,
                                'bytes': 0, 'expire': 0, 'expire_bytes': 0, 'oldest': None, 'newest': None})
    totals = dict.fromkeys(('files', 'bytes', 'expire', 'expire_bytes', 'unregistered', 'large'), 0)
    for row in rows:
        totals['files'] += 1; totals['bytes'] += row['bytes']; totals['large'] += row['large']
        if row['state'] == 'unregistered': totals['unregistered'] += 1; continue
        value = roots[row['root']]
        value['files'] += 1; value['bytes'] += row['bytes']
        value['oldest'] = min(value['oldest'] or row['mtime'], row['mtime'])
        value['newest'] = max(value['newest'] or 0, row['mtime'])
        if row['state'] == 'expire':
            totals['expire'] += 1; totals['expire_bytes'] += row['bytes']
            value['expire'] += 1; value['expire_bytes'] += row['bytes']
        if value['rule'] == 'audit':
            # Show what an audited tree holds, for example journal entries and raw logs put into the journal.
            name = row['path'][len(row['root']) + 1:].split('/', 1)[0]
            value.setdefault('children', {})[name] = value.get('children', {}).get(name, 0) + 1
            value['logs'] = value.get('logs', 0) + bool(re.fullmatch(LOG, os.path.basename(row['path'])))
    public = lambda row: {k: v for k, v in row.items() if k != 'identity'}
    result = {'machine': machine, 'state': 'reachable', 'observed_at': int(time.time()), 'days': DAYS,
              'runs': RUNS, 'totals': totals, 'roots': [v for v in roots.values() if v['files']],
              'unregistered': [public(r) for r in rows if r['state'] == 'unregistered'][:LISTED],
              'large': [public(r) for r in rows if r['large']][:LISTED]}
    if files: result['files'] = [public(r) for r in sorted(rows, key=lambda r: r['path'])]
    return result


def remove(unit, rows):
    """Delete one expired unit. A file must still be the scanned file; a tree child must be a real directory."""
    info = os.lstat(unit)
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(unit)
        return [r for r in rows if r['path'].startswith(unit + '/')]
    match = [r for r in rows if r['path'] == unit]
    if not stat.S_ISREG(info.st_mode) or not match or match[0]['identity'] != (info.st_dev, info.st_ino, info.st_mtime_ns):
        raise RuntimeError('changed after the scan; kept')
    os.unlink(unit)
    return match


def clean(machine, apply=False, prefix='', now=None):
    _, found, rows, units = scan(machine, prefix, now)
    result = summary(machine, found, rows)
    deleted, errors = {'files': 0, 'bytes': 0, 'entries': 0}, []
    if apply:
        for unit in units:
            try: gone = remove(unit, rows)
            except (OSError, RuntimeError) as error:
                errors.append(unit + ': ' + str(error)); continue
            deleted['entries'] += 1; deleted['files'] += len(gone); deleted['bytes'] += sum(r['bytes'] for r in gone)
    result.update(apply=apply, deleted=deleted, errors=errors[:LISTED])
    return result


def check(machine, path, prefix=''):
    """Return the scanned record of one file that may leave the machine."""
    if not path.startswith('/') or os.path.normpath(path) != path or os.path.realpath(path) != path:
        raise ValueError('use the exact absolute path that kk logs list shows')
    _, _, rows, _ = scan(machine, prefix)
    match = [r for r in rows if r['path'] == path]
    if not match: raise ValueError('not a log file that the retention policy lists: ' + path)
    if match[0]['bytes'] > FETCH_LIMIT: raise ValueError('the file is larger than %d MiB' % (FETCH_LIMIT // 1024**2))
    return {k: v for k, v in match[0].items() if k != 'identity'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--machine', required=True, help='the expected host name of this machine')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('scan').add_argument('--files', action='store_true', help='also list each file')
    sub.add_parser('clean').add_argument('--apply', action='store_true', help='delete; without it, only plan')
    sub.add_parser('check').add_argument('path')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if socket.gethostname().split('.')[0] != args.machine:
            raise ValueError('this machine is ' + socket.gethostname() + ', not ' + args.machine)
        if args.action == 'scan':
            _, found, rows, _ = scan(args.machine)
            value = summary(args.machine, found, rows, args.files)
        elif args.action == 'clean': value = clean(args.machine, args.apply)
        else: value = check(args.machine, args.path)
    except (OSError, ValueError, RuntimeError) as error:
        message = 'log retention on ' + args.machine + ': ' + str(error)
        print(message, file=sys.stderr)
        # The playbook saves standard output, so the controller can show the cause.
        print(json.dumps({'machine': args.machine, 'state': 'failed', 'error': message}))
        return 1
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == '__main__': raise SystemExit(main())
