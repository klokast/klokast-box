"""Runner closure for controller operations that stop the runner.

A runner replacement or a dom0 update closes the runner before it stops it
and opens it again when the operation ends. The dom0 journals decide which
operations are pending. The closure only refuses new connections meanwhile.
"""
import json
import re
import subprocess

from platform_maintenance import progress


HELPER = '/usr/local/bin/klokast-air-workspace'


def remote(box, action, *, operation=None, owner=None):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', box):
        raise RuntimeError('invalid runner box')
    argv = ['tailscale', 'ssh', 'neo@' + box + '-air', 'doas', '-n', HELPER, action]
    if operation is not None:
        if not re.fullmatch(r'[0-9a-f]{24}', operation):
            raise RuntimeError('invalid runner operation ID')
        argv += ['--operation', operation]
    if owner:
        argv += ['--owner', owner]
    result = subprocess.run(argv, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('runner maintenance failed on ' + box + ': ' + result.stderr.strip()[-500:])
    return json.loads(result.stdout)


def closed_by(held):
    kind = {'air': 'runner replacement', 'dom0': 'dom0 update'}.get(held.get('owner'), 'controller')
    return kind + ' operation ' + str(held.get('operation'))


def before_stop(box, operation, owner):
    # This is the final remote check. It serializes with managed launcher
    # clients, closes the runner and saves the workspace before any VM shutdown.
    return remote(box, 'image-begin', operation=operation, owner=owner)


def release(box, operation):
    return remote(box, 'complete', operation=operation)


def reopen(box, owner, held):
    """Release `held`, the closure that the runner reports, when an `owner` operation left it.

    Call this only while the journal of `owner` has no pending operation on
    this box: the operation that closed the runner then has ended. Return the
    closure that remains, which another owner holds.
    """
    if not held or held.get('owner') != owner:
        return held
    progress('opening ' + box + '-air again: its closing operation ' + held['operation'] + ' has ended')
    result = release(box, held['operation'])
    if result.get('workspace', 'restored') != 'restored':
        progress(box + '-air tmux workspace ' + result['workspace'] +
                 '; native histories are retained: reopen them with codex resume or claude --resume')
    return None
