"""One-shot Tailscale enrollment; credentials arrive only on an explicit boot."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

INPUT = Path('/dev/xvdg')
OUTPUT = Path('/dev/xvdh')
KIND = 'klokast.guest-enrollment.v1'


def command(arguments, *, key=None):
    # Never include CLI output in exceptions or console logs: it can contain
    # login URLs or credentials. The result channel contains fixed error codes.
    return subprocess.run(['/usr/bin/tailscale', *arguments], input=key,
                          capture_output=True, text=True, timeout=90)


def status():
    result = command(['status', '--json'])
    # NeedsLogin can return nonzero with a valid status document.
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError('tailscale-status-unavailable') from None
    if not isinstance(value, dict):
        raise RuntimeError('tailscale-status-invalid')
    return value


def enrolled(value, request):
    identity = value.get('Self') or {}
    if (value.get('BackendState') != 'Running' or not identity.get('ID') or
            identity.get('HostName') != request['hostname'] or
            sorted(identity.get('Tags') or []) != request['tags']):
        raise RuntimeError('tailscale-identity-not-converged')
    preferences = command(['debug', 'prefs'])
    if preferences.returncode or not json.loads(preferences.stdout).get('RunSSH'):
        raise RuntimeError('tailscale-ssh-not-enabled')
    return {'status': 'enrolled', 'device_id': identity['ID'],
            'hostname': identity['HostName'], 'tags': request['tags']}


def enroll(request):
    deadline = time.monotonic() + 60
    while True:
        try:
            value = status()
            if value.get('BackendState') not in ('Starting', None):
                break
        except RuntimeError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('tailscale-start-timeout')
        time.sleep(1)
    if value.get('BackendState') == 'Running':
        return enrolled(value, request)
    if (value.get('Self') or {}).get('ID'):
        raise RuntimeError('existing-device-needs-repair')
    if value.get('BackendState') not in ('NeedsLogin', 'NoState'):
        raise RuntimeError('tailscale-state-needs-repair')
    if not request['auth_key']:
        return {'status': 'needs-key'}
    command(['up', '--auth-key=file:/dev/stdin', '--hostname=' + request['hostname'],
             '--advertise-tags=' + ','.join(request['tags']), '--ssh', '--timeout=60s'],
            key=request['auth_key'] + '\n')
    # A timed-out CLI can still have completed registration. Inspect its state
    # before reporting failure, and never force reauthentication.
    return enrolled(status(), request)


def validate(request, attempt):
    fields = {'kind', 'attempt_id', 'vm_uuid', 'config_sha256', 'hostname', 'tags', 'auth_key'}
    if (not isinstance(request, dict) or set(request) != fields or
            request['kind'] != KIND or request['attempt_id'] != attempt or
            not isinstance(request['auth_key'], str) or
            (request['auth_key'] and not re.fullmatch(r'tskey-auth-[A-Za-z0-9_-]{10,500}', request['auth_key']))):
        raise RuntimeError('enrollment-input-invalid')
    marker = json.loads(Path('/etc/klokast-infrastructure.json').read_text())
    public = marker['configuration']
    if (public['role'] not in ('air', 'bak', 'dmz', 'iot') or request['hostname'] != public['box'] + '-' + public['role'] or
            request['tags'] != (['tag:airunner'] if public['role'] == 'air' else ['tag:vm']) or request['config_sha256'] != marker['config_sha256'] or
            request['vm_uuid'] != Path('/sys/hypervisor/uuid').read_text().strip()):
        raise RuntimeError('enrollment-assignment-mismatch')
    return request


def main():
    args = [v.split('=', 1)[1] for v in Path('/proc/cmdline').read_text().split()
            if v.startswith('klokast_enrollment=')]
    if not args:
        return 0  # Generic images, normal boots and replacements cannot enroll.
    if len(args) != 1 or not re.fullmatch('[0-9a-f]{24}', args[0]) or os.geteuid() != 0:
        raise RuntimeError('enrollment-boot-invalid')
    attempt = args[0]
    for path, readonly in ((INPUT, '1'), (OUTPUT, '0')):
        if (path.is_symlink() or not stat.S_ISBLK(path.stat().st_mode) or
                Path('/sys/class/block/' + path.name + '/ro').read_text().strip() != readonly):
            raise RuntimeError('enrollment-device-invalid')
        path.chmod(0o600)
    result = {'kind': KIND, 'attempt_id': attempt, 'status': 'failed'}
    try:
        with INPUT.open('rb') as stream:
            content = stream.read(8193).split(b'\0', 1)[0]
        if len(content) > 8192:
            raise RuntimeError('enrollment-input-too-large')
        request = validate(json.loads(content), attempt)
        result.update(vm_uuid=request['vm_uuid'], config_sha256=request['config_sha256'])
        try:
            result.update(enroll(request))
        finally:
            request['auth_key'] = ''
    except Exception:
        # Do not serialize arbitrary exceptions, including JSON parsing errors.
        result['error'] = 'Enrollment failed. Check guest network and Tailscale state; resume the recorded attempt.'
    with OUTPUT.open('wb', buffering=0) as stream:
        stream.write(json.dumps(result, sort_keys=True).encode() + b'\0')
        os.fsync(stream.fileno())
    return 0 if result['status'] in ('enrolled', 'needs-key') else 1


if __name__ == '__main__':
    raise SystemExit(main())
