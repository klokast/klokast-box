#!/usr/bin/env python3
"""Find, audit and expire the log files of this machine with the checked-in Platform policy.

The controllers import this file. Ansible copies it unchanged to dom0 and the VMs
(76-platform-logs.yml). It uses only the Python standard library. It deletes only the
files that a rule selects, and old Codex sessions through `codex delete`. It never
deletes operations journal entries, records outside the selected patterns, or images.
See ansible/ansible.md#log-retention.
"""
import argparse
import collections
import fcntl
import glob
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.parse

DAYS = 14
RUNS = 10
LARGE = 16 * 1024**2
FETCH_LIMIT = 32 * 1024**2
LISTED = 50
LOG = r'.+\.(?:log|stdout|stderr)(?:\.[0-9]+|\.previous|\.old|\.gz)?'
ANY = r'.+'
ENTRY = r'[0-9]{8}T[0-9]{6}Z-.+\.md'
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
ROLES = ('dom0', 'ops', 'air', 'router', 'bak', 'dmz', 'iot', 'vpn-egress')
SMITH = '/home/smith/private/klokast'
AGENT = '/home/agent'
JOURNAL = '/var/lib/klokast/operations-journal'
CODEX = '/usr/local/bin/codex'

Rule = collections.namedtuple('Rule', 'roles root rule pattern purpose')
# A root can contain glob characters. The deepest present root that contains a file owns it.
# A root can have several rules: the file follows the last rule whose pattern matches its name.
# runs: keep every file of the newest RUNS children; expire the older files of the other children.
# age: expire the files that are older than DAYS.
# codex: expire the Codex sessions older than DAYS with codex delete, except open sessions and
#        sessions linked by a fork or an attachment to a session that stays.
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
    Rule(('ops',), JOURNAL, 'age', ANY, 'files in the journal that are not entries'),
    Rule(('ops',), JOURNAL, 'audit', ENTRY, 'journal entries; no rule deletes them'),
    Rule(('ops',), SMITH, 'age', LOG, 'loose controller logs'),
    Rule(('ops',), SMITH + '/*', 'runs', LOG, 'controller private operation records'),
    Rule(('ops',), SMITH + '/logs/*', 'runs', LOG, 'controller wrapper runs'),
    Rule(('ops',), SMITH + '/evidence', 'age', ANY, 'agent scratch files'),
    Rule(('dom0',), '/var/log/xen', 'age', ANY, 'Xen domain logs'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-*', 'runs', LOG, 'dom0 operations and tests'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-*/operations', 'runs', LOG, 'dom0 update operations'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-*/staging', 'runs', LOG, 'dom0 image staging'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-update-history', 'audit', r'.+\.jsonl', 'update history; no rule deletes it'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-boot-history', 'audit', r'.+\.jsonl', 'boot history; no rule deletes it'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-infrastructure/qualification', 'runs', LOG, 'infrastructure qualifications'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-router-templates', 'audit', LOG, 'router templates; template retention owns them'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-router-compatibility', 'audit', LOG, 'router compatibility; template retention owns it'),
    Rule(('dom0',), '/mnt/dom0_data/klokast-router-updates/cold-backups', 'audit', LOG, 'router cold backups'),
    Rule(('air',), AGENT + '/.npm/_logs', 'age', ANY, 'npm logs'),
    Rule(('air',), AGENT + '/.cache/claude-cli-nodejs/*/*', 'age', ANY, 'Claude Code MCP logs'),
    Rule(('air',), AGENT + '/.codex/log', 'age', ANY, 'Codex logs'),
    Rule(('air',), AGENT + '/.codex/app-server-daemon', 'age', LOG, 'Codex daemon logs'),
    Rule(('air',), AGENT + '/.codex', 'audit', r'.+\.jsonl|' + LOG, 'Codex prompt history and logs'),
    Rule(('air',), AGENT + '/.codex/sessions', 'codex', r'.+\.jsonl', 'Codex conversations'),
    Rule(('air',), AGENT + '/.codex/archived_sessions', 'codex', r'.+\.jsonl', 'archived Codex conversations'),
    Rule(('air',), AGENT + '/.local/state/klokast/handoffs', 'runs', ANY, 'workspace checkpoints and older Codex handoff exports'),
    Rule(('air',), AGENT + '/.claude', 'audit', r'.+\.jsonl|' + LOG,
         'Claude Code conversations; Claude Code deletes them after 14 days (cleanupPeriodDays)'),
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
    """Return (directory, rules) for each policy root that exists on this machine.

    A specific rule for a directory replaces a glob rule for it, so the table lists
    general rules first. Rules that name the same root together form its rule list.
    """
    found = {}
    for entry in POLICY:
        if role not in entry.roles and '*' not in entry.roles: continue
        for path in sorted(glob.glob(glob.escape(prefix) + entry.root)):
            try: info = os.lstat(path)
            except OSError: continue
            if not stat.S_ISDIR(info.st_mode): continue
            same = [e for e in found.get(path, []) if e.root == entry.root]
            found[path] = same + [entry]
    return sorted(found.items())


def owner(path, found):
    """Return the deepest root that contains path, and its rule for the file name."""
    best = (None, [])
    for root, entries in found:
        if path.startswith(root + '/') and (best[0] is None or len(root) > len(best[0])): best = (root, entries)
    name = os.path.basename(path)
    rule = next((e for e in reversed(best[1]) if re.fullmatch(e.pattern, name)), None)
    return best[0], rule


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
            'rule': entry.rule if entry else None, 'purpose': entry.purpose if entry else None,
            'state': state, 'large': info.st_size > LARGE,
            'identity': (info.st_dev, info.st_ino, info.st_mtime_ns)}


def history_parent(path):
    """Return the session that a paginated fork reads from, None, or False when unknown."""
    try:
        with open(path, 'rb') as stream: line = stream.readline(1024**2)
    except FileNotFoundError: return None
    except OSError: return False
    try: base = json.loads(line)['payload'].get('history_base')
    except (ValueError, KeyError, TypeError, AttributeError): return False
    if base is None: return None
    return base.get('thread_id') if isinstance(base, dict) else False


def writing(home, session):
    """True when a live Codex writer holds the native lock of the session."""
    try: descriptor = os.open(os.path.join(home, 'thread-writer-locks', session + '.lock'), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError: return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except BlockingIOError: return True
    finally: os.close(descriptor)


def codex_sessions(prefix, cutoff):
    """Return {rollout path: session} of the deletable Codex sessions, and every indexed rollout path.

    A session stays when it changed in the last DAYS, has a live writer, or is linked by a fork
    or an attachment to a session that stays. An unreadable lineage keeps every session.
    """
    home = prefix + AGENT + '/.codex'
    database = home + '/state_5.sqlite'
    if not os.path.isfile(database): return {}, set()
    connection = sqlite3.connect('file:' + urllib.parse.quote(database) + '?mode=ro', uri=True)
    try: rows = connection.execute('SELECT id, rollout_path, updated_at, attached_thread_parent_id FROM threads').fetchall()
    finally: connection.close()
    indexed = {prefix + path for _, path, _, _ in rows if path}
    links, keep = collections.defaultdict(set), set()
    for session, path, updated, attached in rows:
        parent = history_parent(prefix + path) if path else None
        if parent is False: return {}, indexed
        for other in (attached, parent):
            if other: links[session].add(other); links[other].add(session)
        if type(updated) is not int or updated >= cutoff or writing(home, session): keep.add(session)
    stack = list(keep)
    while stack:
        for other in links[stack.pop()]:
            if other not in keep: keep.add(other); stack.append(other)
    return {prefix + p: s for s, p, _, _ in rows if p and s not in keep and re.fullmatch(UUID, s)}, indexed


def decide(root, entry, items, cutoff, codex):
    """Return the file records of one root rule and its expired units."""
    child = lambda path: path[len(root) + 1:].split('/', 1)[0]
    newest = {}
    for path, info in items: newest[child(path)] = max(newest.get(child(path), 0), info.st_mtime)
    keep = set(sorted(newest, key=lambda name: (newest[name], name), reverse=True)[:RUNS])
    deletable, indexed = codex
    rows, units = [], []
    for path, info in items:
        old, session = info.st_mtime < cutoff, None
        if entry.rule == 'runs': expire = old and child(path) not in keep
        elif entry.rule == 'age': expire = old
        elif entry.rule == 'codex':
            # An indexed session leaves only through codex delete; an unindexed old file is a plain file.
            session = deletable.get(path)
            expire = session is not None or (old and path not in indexed)
        else: expire = False
        rows.append(record(path, info, root, entry, 'expire' if expire else 'keep'))
        if expire: units.append((root, path, session))
    return rows, units


def scan(machine, prefix='', now=None):
    """Classify every log file of this machine. Return the file records and the expired units."""
    role = role_of(machine)
    cutoff = (time.time() if now is None else now) - DAYS * 86400
    found = present_roots(role, prefix)
    codex = codex_sessions(prefix, cutoff) if role == 'air' else ({}, set())
    starts = [root for root, _ in found] + [prefix + path for path in SCAN.get(role, SCAN['*'])]
    owned, rows = {}, []
    for path, info in regular_files(starts):
        root, entry = owner(path, found)
        if entry is not None: owned.setdefault((root, entry), []).append((path, info))
        elif re.fullmatch(LOG, os.path.basename(path)): rows.append(record(path, info, root, None, 'unregistered'))
    units = []
    for (root, entry), items in sorted(owned.items(), key=lambda item: (item[0][0], item[0][1].rule)):
        value, expired = decide(root, entry, items, cutoff, codex)
        rows += value; units += expired
    return role, found, rows, units


def summary(machine, found, rows, files=False):
    """Count the files of each root rule. Audit rules also count their first-level children."""
    groups = {}
    totals = dict.fromkeys(('files', 'bytes', 'expire', 'expire_bytes', 'unregistered', 'large'), 0)
    for row in rows:
        totals['files'] += 1; totals['bytes'] += row['bytes']; totals['large'] += row['large']
        if row['state'] == 'unregistered': totals['unregistered'] += 1; continue
        value = groups.setdefault((row['root'], row['rule']), {
            'root': row['root'], 'rule': row['rule'], 'purpose': row['purpose'], 'files': 0, 'bytes': 0,
            'expire': 0, 'expire_bytes': 0, 'oldest': None, 'newest': None})
        value['files'] += 1; value['bytes'] += row['bytes']
        value['oldest'] = min(value['oldest'] or row['mtime'], row['mtime'])
        value['newest'] = max(value['newest'] or 0, row['mtime'])
        if row['state'] == 'expire':
            totals['expire'] += 1; totals['expire_bytes'] += row['bytes']
            value['expire'] += 1; value['expire_bytes'] += row['bytes']
        if value['rule'] == 'audit':
            # Show what an audited tree holds, for example the cases and forum of the journal.
            name = row['path'][len(row['root']) + 1:].split('/', 1)[0]
            value.setdefault('children', {})[name] = value.get('children', {}).get(name, 0) + 1
    public = lambda row: {k: v for k, v in row.items() if k != 'identity'}
    result = {'machine': machine, 'state': 'reachable', 'observed_at': int(time.time()), 'days': DAYS,
              'runs': RUNS, 'totals': totals, 'roots': sorted(groups.values(), key=lambda v: (v['root'], v['rule'])),
              'unregistered': [public(r) for r in rows if r['state'] == 'unregistered'][:LISTED],
              'large': [public(r) for r in rows if r['large']][:LISTED]}
    if files: result['files'] = [public(r) for r in sorted(rows, key=lambda r: r['path'])]
    return result


def codex_delete(prefix, session):
    """Delete one Codex session with the native command, as the runner account."""
    argv = [prefix + CODEX, 'delete', '--force', session]
    if os.geteuid() == 0: argv = ['su', '-s', '/bin/sh', 'agent', '-c', 'exec "$0" "$@"', *argv]
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, cwd='/')
    if result.returncode:
        raise RuntimeError('codex delete failed: ' + (result.stderr or result.stdout).strip()[-300:])


def remove(unit, rows, prefix=''):
    """Delete one expired unit. A plain file must still be the scanned file."""
    root, path, session = unit
    match = [r for r in rows if r['path'] == path]
    if session is not None:
        codex_delete(prefix, session)
        if os.path.lexists(path): raise RuntimeError('codex delete kept the session file')
        return match
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or not match or match[0]['identity'] != (info.st_dev, info.st_ino, info.st_mtime_ns):
        raise RuntimeError('changed after the scan; kept')
    os.unlink(path)
    return match


def remove_empty_parents(root, path):
    """Remove the directories that this deletion emptied, up to the root."""
    directory = os.path.dirname(path)
    while directory.startswith(root + '/'):
        try: os.rmdir(directory)
        except OSError: return
        directory = os.path.dirname(directory)


def clean(machine, apply=False, prefix='', now=None):
    _, found, rows, units = scan(machine, prefix, now)
    result = summary(machine, found, rows)
    deleted, errors = {'files': 0, 'bytes': 0, 'sessions': 0}, []
    if apply:
        for unit in units:
            try: gone = remove(unit, rows, prefix)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                errors.append(unit[1] + ': ' + str(error)); continue
            remove_empty_parents(unit[0], unit[1])
            deleted['files'] += len(gone); deleted['bytes'] += sum(r['bytes'] for r in gone)
            deleted['sessions'] += unit[2] is not None
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
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        message = 'log retention on ' + args.machine + ': ' + str(error)
        print(message, file=sys.stderr)
        # The playbook saves standard output, so the controller can show the cause.
        print(json.dumps({'machine': args.machine, 'state': 'failed', 'error': message}))
        return 1
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == '__main__': raise SystemExit(main())
