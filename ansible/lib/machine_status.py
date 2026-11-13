"""Read-only state of the declared Platform machines: last update time and installed packages."""
import concurrent.futures
import re
import socket
import subprocess
import time

import platform_source
import vm_service_runtime
from vm_lifecycle_roles import PROFILES

# The machine runs this script without privilege. It reads world-readable files only.
PROBE = r'''set -eu
echo "hostname $(hostname)"
echo "clock $(date +%s)"
echo "uptime $(cut -d. -f1 /proc/uptime)"
echo "release $(cat /etc/alpine-release)"
echo "identity $(stat -c %Y /etc/hostname)"
echo "database $(stat -c %Y /lib/apk/db/installed)"
if [ -f /etc/klokast-template.json ]; then echo "image $(stat -c %Y /etc/klokast-template.json)"; fi
echo packages
grep -E '^[PVt]:' /lib/apk/db/installed
'''
HEADER = {'hostname': r'[a-z][a-z0-9-]{0,62}', 'release': r'[0-9]+(?:\.[0-9]+){1,2}[a-z0-9_-]{0,16}',
          **{name: r'[0-9]{1,12}' for name in ('clock', 'uptime', 'identity', 'database', 'image')}}
FIELDS = {'P': ('name', r'[A-Za-z0-9][A-Za-z0-9+_.-]*'), 'V': ('version', r'[A-Za-z0-9][A-Za-z0-9+_.~-]*'),
          't': ('built_at', r'[0-9]{1,12}')}
SECONDS = 30
LIMIT = 4 * 1024 * 1024


def machines(instance):
    """Return each declared machine with its declared runtime state."""
    rows = {}
    controllers = instance['controllers']
    for box in sorted(instance['boxes']):
        rows[box + '-dom0'] = 'running'
        for role in sorted(PROFILES):
            if role == 'ops':
                # The update target check refuses the active controller; status shows both.
                if box in (controllers.get('active'), controllers.get('standby')): rows[box + '-ops'] = 'running'
                continue
            try: rows[box + '-' + role] = vm_service_runtime.target({'instance': instance}, box, role)
            except RuntimeError: continue
    return rows


def parse(target, output):
    """Validate the probe output. A machine can state only its own values."""
    head, separator, body = output.partition('\npackages\n')
    values = {}
    for line in head.splitlines():
        name, _, value = line.partition(' ')
        if name not in HEADER or name in values or not re.fullmatch(HEADER[name], value):
            raise RuntimeError('the machine returned an invalid status line')
        values[name] = value if name in ('hostname', 'release') else int(value)
    if not separator or set(HEADER) - set(values) - {'image'}: raise RuntimeError('the machine returned an incomplete status')
    if values['hostname'] != target: raise RuntimeError('the machine reports the hostname ' + values['hostname'])
    packages = []
    for line in body.splitlines():
        key, pattern = FIELDS.get(line[:1], (None, None))
        if key is None or line[1:2] != ':' or not re.fullmatch(pattern, line[2:]):
            raise RuntimeError('the machine returned an invalid package record')
        if key == 'name': packages.append({'name': line[2:]})
        elif not packages or key in packages[-1]: raise RuntimeError('the machine returned an ambiguous package record')
        else: packages[-1][key] = int(line[2:]) if key == 'built_at' else line[2:]
    names = [package['name'] for package in packages]
    if not packages or len(names) != len(set(names)) or any('version' not in package for package in packages):
        raise RuntimeError('the machine returned an ambiguous package list')
    return {'alpine': values['release'], 'booted_at': values['clock'] - values['uptime'],
            # A replacement writes the identity of the new root. An in-place upgrade writes the package database.
            'updated_at': max(values['identity'], values['database']), 'image_built_at': values.get('image'),
            'packages': sorted(packages, key=lambda package: package['name'])}


def probe(target):
    if target == socket.gethostname().split('.')[0]: argv = ['sh', '-s']
    else: argv = ['tailscale', 'ssh', ('smith@' if target.endswith('-ops') else 'neo@') + target, 'sh', '-s']
    try: result = subprocess.run(argv, input=PROBE, text=True, capture_output=True, timeout=SECONDS)
    except subprocess.TimeoutExpired: raise RuntimeError('no answer in %d seconds' % SECONDS) from None
    if result.returncode: raise RuntimeError(result.stderr.strip()[-300:] or 'the status probe failed')
    if len(result.stdout) > LIMIT: raise RuntimeError('the machine returned too much data')
    return parse(target, result.stdout)


def observe(target, declared):
    row = {'target': target, 'declared': declared}
    # Status never starts a machine and does not contact a machine that is declared stopped.
    if declared != 'running': return dict(row, state='stopped')
    try: return dict(row, state='reachable', **probe(target))
    except (RuntimeError, OSError) as error: return dict(row, state='unreachable', error=str(error))


def summary(row):
    row = dict(row)
    if 'packages' in row: row['packages'] = len(row['packages'])
    return row


def report(target=None):
    """Observe one declared machine with its packages, or all machines with package counts."""
    declared = machines(platform_source.snapshot()['instance'])
    if target:
        if target not in declared: raise RuntimeError(target + ' is not a declared machine. List the machines with: kk status')
        return dict(observe(target, declared[target]), observed_at=int(time.time()))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda item: summary(observe(*item)), sorted(declared.items())))
    return {'observed_at': int(time.time()), 'machines': rows}
