"""Report the independent credentials of this controller without printing secrets.

Each controller holds its own provider and Git credentials. A planned switch
refuses a destination that lacks one of them. This report lets the operator
find and repair each missing item before a switch.
"""
import json
import os
from pathlib import Path
import re
import socket
import subprocess

REPO = Path(__file__).resolve().parents[2]
GUARD = '/usr/local/sbin/klokast-controller-guard'
INSTANCE = Path('/home/smith/private/klokast/instance')
READ_KEY = Path('/home/smith/.ssh/github-klokast-instance')
WRITE_KEY = Path('/home/smith/.ssh/github-klokast-instance-write')
FREEBOX_BROKER = Path('/usr/local/sbin/freebox-ipv6-broker')
ORIGIN = re.compile(r'(?:git@github\.com:|ssh://git@ssh\.github\.com:443/)'
                    r'([A-Za-z0-9][A-Za-z0-9-]{0,38}/klokast-instance)\.git')

# Root reads only metadata and the client ID; secrets never leave this probe.
ROOT_PROBE = r'''
import hashlib, json, os, re, stat, sys
result = {}
for path in sys.argv[1:]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        result[path] = {'present': False}
        continue
    safe = (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and
            stat.S_IMODE(info.st_mode) == 0o600)
    value = {'present': True, 'safe': safe}
    if safe and path.endswith('.env'):
        with open(path) as stream:
            found = re.findall(r'^TS_OAUTH_CLIENT_ID=["\x27]?([^"\x27\s]+)', stream.read(), re.M)
        if len(found) == 1:
            value['client_id_sha256'] = hashlib.sha256(found[0].encode()).hexdigest()
    result[path] = value
print(json.dumps(result))
'''
POLICY = '/etc/klokast/tailscale-policy.env'
DEVICES = '/etc/klokast/tailscale-devices.env'
FREEBOX = '/etc/klokast/freebox-ipv6.json'


def run(argv, *, stdin=None, timeout=60, env=None):
    try:
        return subprocess.run(argv, input=stdin, text=True, capture_output=True, timeout=timeout,
                              env=dict(os.environ, **(env or {})))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, '', 'timed out after ' + str(timeout) + ' s')


def failure(result):
    # Git and SSH put the cause first and generic advice last.
    lines = [line.strip() for line in (result.stderr or result.stdout or '').splitlines() if line.strip()]
    return lines[0][:200] if lines else 'exit status ' + str(result.returncode)


def git_ssh(key):
    return ('ssh -i ' + str(key) + ' -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10'
            ' -o StrictHostKeyChecking=yes -p 443 -o Hostname=ssh.github.com -o HostKeyAlias=github.com')


def public_key(key):
    result = run(['ssh-keygen', '-y', '-f', str(key)], timeout=10)
    return result.stdout.strip() if result.returncode == 0 else ''


def item(identifier, required, state, detail, **extra):
    return dict({'id': identifier, 'required': required, 'state': state, 'detail': detail}, **extra)


def own_tailnet(devices):
    """Files may select the tailnet as "-"; the device list must then contain this node."""
    try:
        own = json.loads(run(['tailscale', 'status', '--json'], timeout=30).stdout)['Self']['DNSName'].rstrip('.')
        return any(device.get('name') == own for device in json.loads(devices)['devices'])
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def tailscale_items(files, hostname):
    items = []
    checks = {POLICY: ['doas', '-n', '/usr/local/sbin/ts-authkey-ops', '--check-config',
                       '--hostname', hostname, '--tags', 'tag:ops'],
              DEVICES: ['doas', '-n', '/usr/local/sbin/ts-devices-list']}
    for identifier, path in (('tailscale-policy', POLICY), ('tailscale-devices', DEVICES)):
        observed = files.get(path, {})
        if not observed.get('present'):
            items.append(item(identifier, True, 'absent', path + ' is absent'))
        elif not observed.get('safe'):
            items.append(item(identifier, True, 'unsafe', path + ' must be a root-owned regular file with mode 0600'))
        else:
            result = run(checks[path], timeout=60)
            state, detail = ('ok', 'installed and accepted by Tailscale') if result.returncode == 0 else (
                'failed', 'Tailscale check failed: ' + failure(result))
            if state == 'ok' and path == DEVICES and not own_tailnet(result.stdout):
                state, detail = 'failed', 'the client lists the devices of another tailnet, not of ' + hostname
            items.append(item(identifier, True, state, detail,
                              client_id_sha256=observed.get('client_id_sha256', '')))
    return items


def instance_items(repository):
    items = []
    if not READ_KEY.is_file():
        items.append(item('instance-read', True, 'absent', str(READ_KEY) + ' is absent'))
    else:
        result = run(['git', '-C', str(INSTANCE), 'ls-remote', 'origin', 'refs/heads/main'], timeout=30,
                     env={'GIT_SSH_COMMAND': git_ssh(READ_KEY)})
        state, detail = ('ok', 'reads ' + repository) if result.returncode == 0 else (
            'failed', 'GitHub refused the read key: ' + failure(result))
        items.append(item('instance-read', True, state, detail, public_key=public_key(READ_KEY)))
    if not WRITE_KEY.is_file():
        items.append(item('instance-write', True, 'absent', str(WRITE_KEY) + ' is absent'))
        return items
    result = run(['git', '-C', str(INSTANCE), 'push', '--dry-run', 'origin', 'HEAD:main'], timeout=30,
                 env={'GIT_SSH_COMMAND': git_ssh(WRITE_KEY)})
    selected = run(['git', '-C', str(INSTANCE), 'config', '--get', 'core.sshCommand'], timeout=10).stdout
    if result.returncode != 0:
        state, detail = 'failed', 'GitHub refused a dry-run push with the write key: ' + failure(result)
    elif str(WRITE_KEY) not in selected:
        state, detail = 'unconfigured', 'the Instance checkout does not use the write key'
    else:
        state, detail = 'ok', 'can push to ' + repository
    items.append(item('instance-write', True, state, detail, public_key=public_key(WRITE_KEY)))
    return items


def metadata_item():
    result = run([str(REPO / 'ansible/bin/secret-authority'), 'github-metadata', 'status'], timeout=30)
    try:
        configured = result.returncode == 0 and json.loads(result.stdout).get('configured') is True
    except ValueError:
        configured = False
    return item('github-metadata', True, 'ok' if configured else 'absent',
                'token installed' if configured else 'no public GitHub metadata token')


def freebox_item(files):
    # Only controllers of boxes in France install the broker.
    if not FREEBOX_BROKER.exists():
        return []
    present = files.get(FREEBOX, {}).get('present')
    return [item('freebox', False, 'ok' if present else 'absent',
                 'app token installed' if present else 'no Freebox app token; it needs physical approval')]


def status():
    hostname = socket.gethostname().split('.', 1)[0]
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,30}-ops', hostname):
        raise RuntimeError('run on a BOX-ops controller; this host is ' + hostname)
    box = hostname[:-4]
    guard = run([GUARD, '--require-local-image-box', box], timeout=30)
    if guard.returncode != 0:
        raise RuntimeError('this controller is not configured or is fenced: ' + failure(guard))
    marker = json.loads(run([GUARD, '--status', '--json'], timeout=30).stdout or '{}')
    origin = run(['git', '-C', str(INSTANCE), 'remote', 'get-url', 'origin'], timeout=10).stdout.strip()
    match = ORIGIN.fullmatch(origin)
    repository = match.group(1) if match else ''
    probe = run(['doas', '-n', 'python3', '-', POLICY, DEVICES, FREEBOX], stdin=ROOT_PROBE, timeout=30)
    if probe.returncode != 0:
        raise RuntimeError('cannot inspect root-only credential files: ' + failure(probe))
    files = json.loads(probe.stdout)
    items = tailscale_items(files, hostname)
    if repository:
        items += instance_items(repository)
    else:
        items.append(item('instance-read', True, 'absent', 'no Instance checkout with a GitHub origin'))
    items += [metadata_item()] + freebox_item(files)
    return {'schema_version': 1, 'kind': 'klokast.controller-credentials.v1', 'box': box,
            'hostname': hostname, 'role': marker.get('role', 'unknown'),
            'instance_repository': repository,
            'ready': all(i['state'] == 'ok' for i in items if i['required']), 'items': items}
