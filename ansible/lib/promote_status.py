"""Read-only list of the unfinished controller switch and workspace move records."""
import json
import os
from pathlib import Path
import re

# The owners of these records are controller_switch.py and bin/air-workspace.
SWITCH_ROOT = Path('/home/smith/private/klokast/controller-switch')
WORKSPACE_ROOT = Path('/home/smith/private/klokast/air-workspace')
BOX = re.compile(r'[a-z][a-z0-9-]{0,30}')
OPERATION = re.compile(r'[0-9a-f]{24}')


def running(operation, proc=Path('/proc')):
    """Tell if another local process names this operation in its arguments."""
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid(): continue
        try:
            if operation.encode() in (entry / 'cmdline').read_bytes(): return True
        except OSError: continue
    return False


def row(path, operation, role, destination):
    """Return the public fields of one unfinished record, or None."""
    value = {'role': role, 'operation': operation, 'updated_at': int(path.stat().st_mtime)}
    try:
        record = json.loads(path.read_text())
        if record['phase'] == 'complete' or record.get('workspace'): return None  # A qualification moves no placement.
        box = record[destination]
        if not OPERATION.fullmatch(operation) or not isinstance(box, str) or not BOX.fullmatch(box):
            raise ValueError('invalid target or operation ID')
        created, error = record.get('created_at'), record.get('error')
        value.update(target=box + '-' + role, phase=str(record['phase'])[:40],
                     created_at=created if type(created) is int else None,
                     error=str(error)[:500] if error else None, running=running(operation))
    except (ValueError, KeyError, TypeError) as cause:
        value.update(target=None, phase='unreadable', created_at=None, running=False,
                     error='unreadable record ' + str(path) + ': ' + str(cause)[:200])
    return value


def unfinished():
    rows = [row(path, path.stem, 'ops', 'new') for path in sorted(SWITCH_ROOT.glob('*.json'))]
    rows += [row(path, path.parent.name, 'air', 'destination') for path in sorted(WORKSPACE_ROOT.glob('*/operation.json'))]
    return [value for value in rows if value]
