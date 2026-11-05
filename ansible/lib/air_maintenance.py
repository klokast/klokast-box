"""Active-controller reservations using the existing VM operation lock."""
import json
import re
import subprocess


HELPER = '/usr/local/bin/klokast-air-workspace'


def remote(box, action, *, operation=None, ttl=1800, reservation=None, session=None):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', box):
        raise RuntimeError('invalid runner box')
    argv = ['tailscale', 'ssh', 'neo@' + box + '-air', 'doas', '-n', HELPER, action]
    if operation is not None:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', operation):
            raise RuntimeError('invalid runner maintenance operation ID')
        argv += ['--operation', operation, '--ttl', str(ttl)]
    if reservation:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', reservation):
            raise RuntimeError('invalid planned reservation ID')
        argv += ['--reservation', reservation]
    if session:
        import uuid
        if str(uuid.UUID(session))!=session:raise RuntimeError('invalid handoff discussion UUID')
        argv+=['--session',session]
    result = subprocess.run(argv, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('runner maintenance failed on ' + box + ': ' + result.stderr.strip()[-500:])
    return json.loads(result.stdout)


def before_stop(box, operation, reservation=None, *, image=False):
    # This is the final remote check. begin serializes with managed launcher
    # clients and publishes pending status before any VM shutdown.
    return remote(box, 'image-begin' if image else 'begin', operation=operation, reservation=reservation)


def release(box, operation):
    return remote(box, 'complete', operation=operation)
