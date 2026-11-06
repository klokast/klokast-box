"""Temporary Xen transport for first enrollment; callers hold assignment locks."""
import json
from pathlib import Path
import re
import secrets
import time

from xen_build_runtime import (attach_loop, checksum, detach_loop, domain, loop_devices,
                               read_slot, require_identity, run, safe_directory, safe_file, write)

RUNTIME = Path('/run/klokast-guest-enrollment')
KIND = 'klokast.guest-enrollment.v1'
DEVICES = {'input': ('xvdg', 51808), 'result': ('xvdh', 51824)}


def directory(state):
    attempt = state.get('enrollment_attempt')
    if not isinstance(attempt, str) or not re.fullmatch('[0-9a-f]{24}', attempt):
        raise RuntimeError('enrollment attempt is invalid; preserve the assignment')
    return RUNTIME / state['uuid'] / attempt


def outcome(state):
    value = state.get('enrollment_result')
    if value is None and state.get('enrollment_attempt'):
        path = directory(state) / 'result.slot'
        if path.exists():
            safe_file(path, 1024 * 1024)
            try:
                value = read_slot(path)
            except (ValueError, RuntimeError):
                return None  # Empty or partial result; the guest may be writing.
    if not value:
        return None
    if (value.get('kind') != KIND or value.get('attempt_id') != state['enrollment_attempt'] or
            value.get('vm_uuid') != state['uuid'] or value.get('config_sha256') != state['config_sha256']):
        raise RuntimeError('enrollment result does not match its assignment; preserve the guest')
    result = {k: value[k] for k in ('kind', 'attempt_id', 'vm_uuid', 'config_sha256', 'status')}
    if value['status'] == 'enrolled':
        if (value.get('hostname') != state['box'] + '-air' or value.get('tags') != ['tag:airunner'] or
                not isinstance(value.get('device_id'), str) or
                not re.fullmatch('[A-Za-z0-9_-]{1,128}', value['device_id'])):
            raise RuntimeError('enrollment returned an unexpected Tailscale identity')
        result.update({k: value[k] for k in ('hostname', 'tags', 'device_id')})
    elif value['status'] not in ('needs-key', 'failed'):
        raise RuntimeError('enrollment result has an unknown status')
    return result


def status(work, state):
    if state['stage'] == 'ready':
        return 'ready'
    if state['stage'] != 'prepared':
        raise RuntimeError('runner is not prepared for enrollment; resume its original creation workflow')
    current = domain('air')
    if current:
        require_identity(current, state['uuid'])
    if not state.get('enrollment_attempt'):
        if current:
            raise RuntimeError('unrecorded runner is running; preserve it for recovery')
        return 'key-required'
    result = outcome(state)
    if result and result['status'] == 'failed' and current is None:
        return 'probe'
    if result is not None or current:
        return 'resume'
    return 'probe'  # Host reboot lost /run. Inspect this same root without a key.


def cleanup(state):
    path = directory(state)
    if not path.exists():
        return
    safe_directory(RUNTIME); safe_directory(path.parent); safe_directory(path)
    current = domain('air')
    if current:
        require_identity(current, state['uuid'])
        for name, number in DEVICES.values():
            rows = run(['xl', 'block-list', str(current['domid'])]).stdout.splitlines()
            if any(row.split() and row.split()[0] == str(number) for row in rows):
                run(['xl', 'block-detach', str(current['domid']), name])
            deadline = time.monotonic() + 30
            while True:
                actual = domain('air')
                if actual is None:
                    break
                require_identity(actual, state['uuid'])
                rows = run(['xl', 'block-list', str(actual['domid'])]).stdout.splitlines()
                if not any(row.split() and row.split()[0] == str(number) for row in rows):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('temporary enrollment disk remains attached; resume cleanup')
                time.sleep(1)
    for name in DEVICES:
        slot = path / (name + '.slot')
        if slot.exists():
            safe_file(slot, 1024 * 1024)
            for device in loop_devices(slot):
                detach_loop(slot, device)
            slot.unlink()
    (path / 'boot.cfg').unlink(missing_ok=True)
    path.rmdir()


def enroll(work, state, permanent_config, key):
    action = status(work, state)
    if action == 'ready':
        return {'changed': False, 'action': 'ready', 'state': state}
    if key and action != 'key-required':
        raise RuntimeError('a new key cannot replace an unfinished enrollment attempt')
    if action in ('key-required', 'probe'):
        if key and not re.fullmatch(r'tskey-auth-[A-Za-z0-9_-]{10,500}', key):
            raise RuntimeError('enrollment credential is invalid')
        if action == 'probe':
            cleanup(state)  # No live guest and no completed result remain.
        state['enrollment_attempt'] = secrets.token_hex(12)
        state.pop('enrollment_result', None)
        path = directory(state)
        RUNTIME.mkdir(mode=0o700, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.mkdir(mode=0o700)
        safe_directory(RUNTIME); safe_directory(path.parent); safe_directory(path)
        # The pending record contains no credential, including after a crash.
        write(work / 'assignment.json', state)
        request = {'kind': KIND, 'attempt_id': state['enrollment_attempt'], 'vm_uuid': state['uuid'],
                   'config_sha256': state['config_sha256'], 'hostname': state['box'] + '-air',
                   'tags': ['tag:airunner'], 'auth_key': key}
        loops = {}
        for name in DEVICES:
            slot = path / (name + '.slot')
            write(slot, request if name == 'input' else {})
            # NUL-terminated slots match the existing opaque Xen transport.
            with slot.open('r+b') as stream:
                stream.seek(0, 2); stream.write(b'\0'); stream.truncate(1024 * 1024)
            loops[name] = attach_loop(slot, readonly=name == 'input')
        request['auth_key'] = ''; key = ''
        extra_disks = ["phy:" + loops[name] + ',' + DEVICES[name][0] + (',r' if name == 'input' else ',w')
                       for name in DEVICES]
        # Append to the checked normal recipe, not to the persisted boot file.
        config = permanent_config + '\ndisk += ' + repr(extra_disks) + '\n'
        config += "extra += ' klokast_enrollment=" + state['enrollment_attempt'] + "'\non_reboot = 'destroy'\n"
        boot = path / 'boot.cfg'
        boot.write_text(config); boot.chmod(0o600)
        state['enrollment_boot_sha256'] = checksum(boot)
        write(work / 'assignment.json', state)
        run(['xl', 'create', boot])
    deadline = time.monotonic() + 300
    while True:
        current = domain('air')
        if current:
            require_identity(current, state['uuid'])
        result = outcome(state)
        if result:
            break
        if current is None:
            raise RuntimeError('enrollment boot stopped without a result; retry a keyless probe of the assigned root')
        if time.monotonic() >= deadline:
            raise RuntimeError('enrollment timed out; preserve the guest and resume this attempt without a new key')
        time.sleep(1)
    state['enrollment_result'] = result
    write(work / 'assignment.json', state)
    if result['status'] == 'failed':
        raise RuntimeError('guest enrollment failed; inspect its network and Tailscale state before retrying')
    if result['status'] == 'needs-key':
        # Only a completed keyless probe may request another key. Never stop a
        # registered or uncertain guest, or an already accepted runner.
        if current:
            run(['xl', 'shutdown', str(current['domid'])])
            deadline = time.monotonic() + 60
            while (current := domain('air')) is not None:
                require_identity(current, state['uuid'])
                if time.monotonic() >= deadline:
                    raise RuntimeError('keyless enrollment probe did not stop; preserve the guest')
                time.sleep(1)
        cleanup(state)
        for field in ('enrollment_attempt', 'enrollment_result', 'enrollment_boot_sha256'):
            state.pop(field, None)
        write(work / 'assignment.json', state)
        return {'changed': True, 'action': 'key-required', 'state': state}
    cleanup(state)
    state['stage'] = 'ready'
    write(work / 'assignment.json', state)
    if domain('air') is None:
        run(['xl', 'create', work / 'air.cfg'])
    return {'changed': True, 'action': 'ready', 'state': state}
