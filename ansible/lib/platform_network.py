"""Reconcile declared Tailnet policy with a durable preimage and conditional writes."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import platform_source as source
import platform_maintenance as maintenance

ROOT = Path('/var/lib/klokast/network/operations')
HELPER = Path('/usr/local/libexec/klokast/ts-policy-mutate-internal')
RENDERER = Path('/usr/local/sbin/render-tailscale-policy')
RUNTIME = Path('/run/klokast/apply')


def call(action, operation):
    result = subprocess.run([str(HELPER), action, operation], capture_output=True,
                            text=True, stdin=subprocess.DEVNULL, timeout=120)
    if result.returncode: raise source.SourceError('Tailnet policy ' + action + ' failed: ' + result.stderr[-2048:].strip())


def recovery(record, work):
    directory = ROOT / record['operation']
    call('get-recovery', record['operation'])
    current = (work / 'recovery.body').read_bytes()
    before = (directory / 'preimage.body').read_bytes()
    candidate = (directory / 'candidate.body').read_bytes()
    if current == before: return 'previous-policy-present'
    if current != candidate: raise source.SourceError('Tailnet policy changed externally; recovery requires review of the retained preimage')
    shutil.copyfile(directory / 'preimage.body', work / 'preimage.body')
    call('post-preimage', record['operation'])
    call('get-after', record['operation'])
    if (work / 'after.body').read_bytes() != before: raise source.SourceError('Tailnet recovery verification failed')
    return 'restored'


def reconcile(operation=None):
    view = source.snapshot()
    if operation is not None and (len(operation) != 24 or any(c not in '0123456789abcdef' for c in operation)):
        raise source.SourceError('invalid network recovery operation')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in ROOT.glob('*/record.json'):
        state = source.read_json(path)
        if state['stage'] == 'pending' and state['operation'] != operation:
            raise source.SourceError('resolve pending network operation first: ' + state['operation'])
    selected = operation or secrets.token_hex(12)
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    if RUNTIME.stat().st_uid != 0 or RUNTIME.stat().st_mode & 0o077:
        raise source.SourceError('network runtime directory must be root-owned and private')
    work = RUNTIME / selected
    work.mkdir(mode=0o700)
    directory = ROOT / selected
    try:
        if operation:
            record = source.read_json(directory / 'record.json')
            if record['stage'] != 'pending': return record
            record.update(stage='recovered', recovery=recovery(record, work))
            maintenance.write(directory / 'record.json', record)
            return record
        directory.mkdir(mode=0o700)
        # Render exact validated desired bytes, never a caller-selected privileged file.
        (work / 'instance.json').write_text(json.dumps(view['instance']))
        source.command([RENDERER, '--instance', work / 'instance.json', '--output', work / 'candidate.body'])
        call('get-before', selected)
        call('validate-candidate', selected)
        before = (work / 'live.body').read_bytes()
        candidate = (work / 'candidate.body').read_bytes()
        if before == candidate: return {'result': 'unchanged', 'instance_sha256': view['instance_sha256']}
        if source.snapshot()['instance_sha256'] != view['instance_sha256']:
            raise source.SourceError('Instance changed before Tailnet execution')
        for name in ('candidate.body', 'live.body'):
            target = directory / ('preimage.body' if name == 'live.body' else name)
            shutil.copyfile(work / name, target)
            target.chmod(0o600)
            with target.open('rb') as stream: os.fsync(stream.fileno())
        record = {'operation': selected, 'stage': 'pending', 'instance_sha256': view['instance_sha256'],
                  'implementation': view['implementation']}
        maintenance.write(directory / 'record.json', record)
        try:
            call('post-candidate', selected)
            call('get-after', selected)
            if (work / 'after.body').read_bytes() != candidate:
                raise source.SourceError('Tailnet policy verification failed')
        except (source.SourceError, OSError, subprocess.TimeoutExpired):
            record.update(stage='recovered', recovery=recovery(record, work))
            maintenance.write(directory / 'record.json', record)
            raise
        record.update(stage='complete', result='updated')
        maintenance.write(directory / 'record.json', record)
        return record
    finally:
        shutil.rmtree(work)
