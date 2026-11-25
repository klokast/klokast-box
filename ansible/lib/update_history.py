"""Record on dom0 when a machine of its box received an update, and what changed.

The dom0 update helper and the VM replacement step load this file on dom0. The
history is evidence for people. No update decision reads it, and a failed write
never stops an update. It holds no Instance values: the directory is readable by
the unprivileged status probe. See doc/platform-updates.md#update-history.
"""
import json
import os
from pathlib import Path
import re
import time

ROOT = Path('/mnt/dom0_data/klokast-update-history')
MACHINE = re.compile('[a-z][a-z0-9-]{0,62}')
# The package names of the upstream tools that an image holds outside APK.
UPSTREAM = {'tailscale': 'tailscale', 'codex': 'codex', 'claude': 'claude-code'}


def packages(apk, components):
    """Return the versions of the APK packages and of the upstream tools of one image."""
    if not apk: return None
    found = dict(apk)
    for name, package in UPSTREAM.items():
        version = (components.get(name) or {}).get('version')
        if version: found.setdefault(package, version)
    return found


def changes(before, after):
    """Compare two package sets. An unknown set gives no comparison."""
    if before is None or after is None: return None
    return {'added': {n: after[n] for n in sorted(set(after) - set(before))},
            'removed': {n: before[n] for n in sorted(set(before) - set(after))},
            'changed': {n: [before[n], after[n]] for n in sorted(set(before) & set(after)) if before[n] != after[n]}}


def names(before, after):
    """Name the configuration components that differ. An unknown set gives no comparison."""
    if before is None or after is None: return None
    return sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))


def entry(operation, before, after, at=None):
    """Describe one accepted update. Each side has image, commit, packages, components and configuration."""
    if not before: kind = 'first record'
    else:
        kinds = [name for name, key in (('image', 'image'), ('configuration', 'configuration'))
                 if before.get(key) != after.get(key)]
        kind = ' and '.join(kinds) or 'rebuild'
    before = before or {}
    return {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(at)), 'operation': operation, 'kind': kind,
            'image': {'from': before.get('image'), 'to': after['image']},
            'commit': {'from': before.get('commit'), 'to': after.get('commit')},
            'packages': changes(before.get('packages'), after.get('packages')),
            'configuration': names(before.get('components'), after.get('components'))}


def record(machine, operation, describe, root=None):
    """Write the entry that describe() returns, once for each operation. Return the error text, or None.

    The caller allows one update of a machine at a time. A resumed operation adds no second entry.
    """
    try:
        if not MACHINE.fullmatch(machine): raise RuntimeError('invalid machine name')
        path = (ROOT if root is None else root) / (machine + '.jsonl')
        content = path.read_text() if path.exists() else ''
        if all(json.loads(line)['operation'] != operation for line in content.splitlines()):
            write(path, content + json.dumps(describe(), sort_keys=True) + '\n')
        return None
    except Exception as error:  # Evidence must never stop an accepted update.
        return 'the update history of ' + machine + ' was not written: ' + type(error).__name__ + ': ' + str(error)[-300:]


def write(path, content):
    """Replace the file in one step, so a power loss leaves the old or the new history."""
    root = path.parent
    root.mkdir(mode=0o755, exist_ok=True)
    root.chmod(0o755)
    temporary = root / ('.' + path.name + '.tmp')
    with temporary.open('w') as stream:
        stream.write(content)
        stream.flush()
        os.fchmod(stream.fileno(), 0o644)
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(root, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
