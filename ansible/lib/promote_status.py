"""Read-only list of the unfinished controller switch records."""
import json
import os
from pathlib import Path
import re

# controller_switch.py owns these records.
SWITCH_ROOT = Path('/home/smith/private/klokast/controller-switch')
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
        if record['phase'] in ('complete', 'abandoned'): return None
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
    return [value for value in rows if value]


def last_switch():
    """Return the latest completed switch time from this controller's records.

    Switch records have no completion field; their final write time is the
    recorded completion time. Marker timestamps can also change during convergence.
    """
    latest = None
    for path in sorted(SWITCH_ROOT.glob('*.json')):
        try:
            record = json.loads(path.read_text())
            if not isinstance(record, dict) or record.get('phase') != 'complete': continue
            if not OPERATION.fullmatch(path.stem) or any(
                    not isinstance(record.get(key), str) or not BOX.fullmatch(record[key]) for key in ('old', 'new')):
                continue
            completed = int(path.stat().st_mtime)
            latest = max(latest or 0, completed)
        except (ValueError, TypeError):
            continue  # The unfinished report already reports unreadable records.
    return {'completed_at': latest}
