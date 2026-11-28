"""Read-only state of the declared Platform machines: last update, installed packages, update history and drift."""
import calendar
import concurrent.futures
import json
from pathlib import Path
import re
import socket
import subprocess
import time

from health_checks import STATES as HEALTH_STATES
import platform_source
import vm_service_runtime
from vm_lifecycle_roles import PROFILES

REPO = Path(__file__).resolve().parents[2]
SHARED_GUESTS = ('bak', 'dmz', 'iot')

# The machine runs this script without privilege. It reads public package metadata
# and asks upstream tools for their installed version.
PROBE = r'''set -eu
echo "hostname $(hostname)"
echo "clock $(date +%s)"
echo "uptime $(cut -d. -f1 /proc/uptime)"
echo "release $(cat /etc/alpine-release)"
echo "identity $(stat -c %Y /etc/hostname)"
echo "database $(stat -c %Y /lib/apk/db/installed)"
if [ -f /etc/klokast-template.json ]; then echo "image $(stat -c %Y /etc/klokast-template.json)"; fi
echo world
cat /etc/apk/world
echo packages
grep -E '^[PVt]:' /lib/apk/db/installed
echo upstream
for tool in tailscale codex claude; do
    case "$tool" in claude) package=claude-code ;; *) package=$tool ;; esac
    if grep -qx "P:$package" /lib/apk/db/installed; then continue; fi
    if ! command -v "$tool" >/dev/null 2>&1; then continue; fi
    case "$tool" in
        tailscale) version=$(tailscale version | head -n 1) ;;
        codex) version=$(codex --version); version=${version#codex-cli } ;;
        claude) version=$(claude --version); version=${version%% *} ;;
    esac
    printf 'P:%s\nV:%s\n' "$package" "$version"
done
'''
# A dom0 also lists its Xen guests and autostart links, and a shared guest its
# running rootless containers. These reads use the existing doas and Podman
# access of neo. They change nothing.
# The assignment stops the script when xl fails; a pipe would hide the failure.
DOM0_PROBE = r'''domains=$(doas -n /usr/sbin/xl list)
echo guests
printf '%s\n' "$domains" | awk 'NR > 1 && $1 != "Domain-0" {print $1}'
echo autostart
for link in /etc/xen/auto/*.cfg; do
    if [ -e "$link" ] || [ -L "$link" ]; then basename "$link" .cfg; fi
done
'''
# A dom0 also returns the update history of each machine of its box. Any account can read
# it. The probe returns the newest entries only.
HISTORY_PROBE = r'''echo history
for file in /mnt/dom0_data/klokast-update-history/*.jsonl; do
    if [ -f "$file" ]; then tail -n 10 "$file" | sed "s/^/$(basename "$file" .jsonl) /"; fi
done
'''
SHARED_PROBE = r'''echo containers
if command -v podman >/dev/null 2>&1; then podman ps --format '{{.Names}} {{.IsInfra}}'; fi
'''
NAME = r'[a-z][a-z0-9-]{0,62}'
CONTAINER = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}'
HEADER = {'hostname': r'[a-z][a-z0-9-]{0,62}', 'release': r'[0-9]+(?:\.[0-9]+){1,2}[a-z0-9_-]{0,16}',
          **{name: r'[0-9]{1,12}' for name in ('clock', 'uptime', 'identity', 'database', 'image')}}
FIELDS = {'P': ('name', r'[A-Za-z0-9][A-Za-z0-9+_.-]*'), 'V': ('version', r'[A-Za-z0-9][A-Za-z0-9+_.~-]*'),
          't': ('built_at', r'[0-9]{1,12}')}
VERSION = r'[A-Za-z0-9][A-Za-z0-9+_.~-]{0,127}'
KIND = r'(?:first record|rebuild|image|configuration|image and configuration)'
COMPONENT = r'[A-Za-z0-9][A-Za-z0-9 ._/@-]{0,255}'
SECONDS = 30
LIMIT = 4 * 1024 * 1024
# Role-specific health checks for `show`. Each command is the existing tool of the role.
# It runs read-only on the controller and prints one JSON line:
# {"checks": [{"name": ..., "state": "ok|warning|failed|skipped", "detail": ...}]}.
HEALTH = {'vpn-egress': lambda box: [str(REPO / 'ansible/bin/platform-vpn-egress'), 'health', '--box', box],
          'air': lambda box: [str(REPO / 'ansible/bin/airunner'), 'health', '--box', box],
          'exit': lambda box: [str(REPO / 'ansible/bin/platform-exit-appliance'), 'health', '--box', box],
          'print': lambda box: [str(REPO / 'apps/print-server/bin/print-serverctl'), 'health', '--box', box]}
HEALTH_SECONDS = 400
# Appliance roles have no login, so the package probe cannot read them. The application
# that declares each one. `show` reports their declared state, health and image build record.
APPLIANCES = {'exit': 'exit-node', 'print': 'print-server'}
# The dom0 keeps the record of the installed appliance image and of its build, readable
# by root only. neo reads these fixed files with its existing doas access.
APPLIANCE_PROBE = r'''set -eu
base=/mnt/dom0_data/klokast-@KIND@
record=$(doas -n cat "$base/installed.json")
operation=$(printf '%s' "$record" | sed -n 's/.*"operation_id": *"\([0-9a-f]\{24\}\)".*/\1/p')
[ -n "$operation" ]
echo installed
printf '%s\n' "$record"
echo built
doas -n stat -c %Y "$base/staging/$operation/build-result.json"
echo result
doas -n cat "$base/staging/$operation/build-result.json"
echo uptime
doas -n /usr/sbin/xl uptime @KIND@ 2>/dev/null | tail -n 1 || true
echo stop
if doas -n test -f "$base/health-stop.json"; then doas -n cat "$base/health-stop.json"; fi
'''
# Appliance tools stop a VM that its health check found unusable and start it again for kk restart.
APPLIANCE_TOOLS = {'exit': REPO / 'ansible/bin/platform-exit-appliance', 'print': REPO / 'ansible/bin/platform-print-appliance'}
# Boot work can follow the Tailscale start, for example the print queue setup. A check that makes
# a VM unusable stops it only after this uptime, and kk restart waits this long before it decides.
BOOT_GRACE = 300
HEALTH_STOPPED_PROBE = r'''for kind in exit print; do
    if doas -n test -f "/mnt/dom0_data/klokast-$kind/health-stop.json"; then echo "$kind"; fi
done
'''


def machines(instance):
    """Return each declared machine with its declared runtime state, by role and then by box.

    The same role on different boxes is listed together, for comparison.
    """
    rows = {}
    controllers = instance['controllers']
    for box in sorted(instance['boxes']):
        rows[('dom0', box)] = 'running'
        for role in sorted(PROFILES):
            if role == 'ops':
                # The update target check refuses the active controller; status shows both.
                if box in controllers: rows[(role, box)] = 'running'
                continue
            try: rows[(role, box)] = vm_service_runtime.target({'instance': instance}, box, role)
            except RuntimeError: continue
    return {box + '-' + role: rows[(role, box)] for role, box in sorted(rows)}


def history(lines):
    """Validate the update history of one box. Return the entries of each machine, oldest first."""
    found = {}
    def require(value, pattern, optional=True):
        if value is None and optional: return
        if not isinstance(value, str) or not re.fullmatch(pattern, value): raise ValueError()
    for line in lines:
        machine, _, text = line.partition(' ')
        try:
            entry = json.loads(text)
            require(machine, NAME, optional=False)
            require(entry['operation'], '[0-9a-f]{24}', optional=False)
            require(entry['kind'], KIND, optional=False)
            for side in ('from', 'to'):
                require(entry['image'][side], '[0-9a-f]{24}', optional=side == 'from')
                require(entry['commit'][side], '[0-9a-f]{40}')
            packages = entry['packages']
            for group in ('added', 'removed', 'changed') if packages is not None else ():
                for name, versions in packages[group].items():
                    require(name, FIELDS['P'][1], optional=False)
                    versions = versions if group == 'changed' else [versions]
                    if len(versions) != (2 if group == 'changed' else 1): raise ValueError()
                    for version in versions: require(version, VERSION, optional=False)
            for name in entry['configuration'] or (): require(name, COMPONENT, optional=False)
            row = {key: entry[key] for key in ('operation', 'kind', 'image', 'commit', 'packages', 'configuration')}
            row.update(at=calendar.timegm(time.strptime(entry['at'], '%Y-%m-%dT%H:%M:%SZ')),
                       seeded=entry.get('seeded') is True)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise RuntimeError('the machine returned an invalid update history') from None
        found.setdefault(machine, []).append(row)
    return found


def parse(target, output, sections=()):
    """Validate the probe output. A machine can state only its own values."""
    head, world_separator, rest = output.partition('\nworld\n')
    world, separator, rest = rest.partition('\npackages\n')
    body, upstream_separator, upstream = rest.partition('\nupstream\n')
    if not world_separator or not separator or not upstream_separator:
        raise RuntimeError('the machine returned an incomplete package list')
    # The extra sections follow the upstream list, in the order of sections.
    upstream, extra = '\n' + upstream, {}
    for name in reversed(sections):
        upstream, found, lines = upstream.rpartition('\n' + name + '\n')
        if not found: raise RuntimeError('the machine returned no ' + name + ' list')
        extra[name] = lines.splitlines(); upstream += '\n'
    upstream = upstream[1:]
    requested = set()
    for constraint in world.splitlines():
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9+_.-]*)(?:@[A-Za-z0-9_.-]+)?(?:[<>=~]+[A-Za-z0-9+_.~:-]+)?', constraint)
        if not match: raise RuntimeError('the machine returned an invalid explicit package')
        requested.add(match[1])
    values = {}
    for line in head.splitlines():
        name, _, value = line.partition(' ')
        if name not in HEADER or name in values or not re.fullmatch(HEADER[name], value):
            raise RuntimeError('the machine returned an invalid status line')
        values[name] = value if name in ('hostname', 'release') else int(value)
    if not separator or set(HEADER) - set(values) - {'image'}: raise RuntimeError('the machine returned an incomplete status')
    if values['hostname'] != target: raise RuntimeError('the machine reports the hostname ' + values['hostname'])
    packages = []
    for line in (body + '\n' + upstream).splitlines():
        key, pattern = FIELDS.get(line[:1], (None, None))
        if key is None or line[1:2] != ':' or not re.fullmatch(pattern, line[2:]):
            raise RuntimeError('the machine returned an invalid package record')
        if key == 'name': packages.append({'name': line[2:]})
        elif not packages or key in packages[-1]: raise RuntimeError('the machine returned an ambiguous package record')
        else: packages[-1][key] = int(line[2:]) if key == 'built_at' else line[2:]
    names = [package['name'] for package in packages]
    if not packages or len(names) != len(set(names)) or any('version' not in package for package in packages):
        raise RuntimeError('the machine returned an ambiguous package list')
    upstream_names = {line[2:] for line in upstream.splitlines() if line.startswith('P:')}
    packages = [package for package in packages if package['name'] in requested | upstream_names]
    for name in ('guests', 'autostart'):
        if name in extra and (any(not re.fullmatch(NAME, line) for line in extra[name]) or
                              len(set(extra[name])) != len(extra[name])):
            raise RuntimeError('the machine returned an invalid ' + name + ' list')
    containers = []
    for line in extra.get('containers', []):
        match = re.fullmatch('(' + CONTAINER + ') (true|false)', line)
        if not match: raise RuntimeError('the machine returned an invalid container list')
        # The infrastructure container of a pod belongs to the containers of that pod.
        if match[2] == 'false': containers.append(match[1])
    # An invalid history hides only the update times, not the machine.
    updates = {}
    if 'history' in extra:
        try: updates = {'history': history(extra['history'])}
        except RuntimeError as error: updates = {'history': {}, 'history_error': str(error)}
    return {'alpine': values['release'], 'booted_at': values['clock'] - values['uptime'],
            # A replacement writes the identity of the new root. An in-place upgrade writes the package database.
            'updated_at': max(values['identity'], values['database']), 'image_built_at': values.get('image'),
            'packages': sorted(packages, key=lambda package: package['name']),
            **{name: sorted(extra[name]) for name in ('guests', 'autostart') if name in extra},
            **({'containers': sorted(containers)} if 'containers' in extra else {}), **updates}


def run_probe(target, script):
    if target == socket.gethostname().split('.')[0]: argv = ['sh', '-s']
    else: argv = ['tailscale', 'ssh', ('smith@' if target.endswith('-ops') else 'neo@') + target, 'sh', '-s']
    try: result = subprocess.run(argv, input=script, text=True, capture_output=True, timeout=SECONDS)
    except subprocess.TimeoutExpired: raise RuntimeError('no answer in %d seconds' % SECONDS) from None
    if result.returncode: raise RuntimeError(result.stderr.strip()[-300:] or 'the status probe failed')
    if len(result.stdout) > LIMIT: raise RuntimeError('the machine returned too much data')
    return result.stdout


def probe(target):
    role = target.rsplit('-', 1)[-1]
    sections = ('guests', 'autostart', 'history') if role == 'dom0' else ('containers',) if role in SHARED_GUESTS else ()
    script = PROBE + (DOM0_PROBE + HISTORY_PROBE if role == 'dom0' else SHARED_PROBE if sections else '')
    return parse(target, run_probe(target, script), sections)


def role_of(target):
    """Return the role of a machine name, also a role with a hyphen in its name."""
    return next((role for role in ('dom0', *PROFILES) if target.endswith('-' + role)), None)


def add_updates(row, histories):
    """Set the last update of a reachable machine from the history that its dom0 keeps.

    A dom0 installs its packages again at each boot, so without an entry its last update
    is unknown. A VM root keeps its own time, which stands until the first entry.
    """
    if row.get('state') != 'reachable': return row
    entries = histories.get(row['target'], [])
    row = {key: value for key, value in row.items() if key != 'history'}
    if entries: row['updated_at'] = entries[-1]['at']
    elif role_of(row['target']) == 'dom0': row['updated_at'] = None
    return dict(row, updates=entries)


def observe(target, declared):
    row = {'target': target, 'declared': declared}
    # Status never starts a machine and does not contact a machine that is declared stopped.
    if declared != 'running': return dict(row, state='stopped')
    try: return dict(row, state='reachable', **probe(target))
    except (RuntimeError, OSError) as error: return dict(row, state='unreachable', error=str(error))


def summary(row):
    row = {key: value for key, value in row.items() if key != 'updates'}
    for role, profile in {'dom0': 'dom0-alpine-v1', **PROFILES}.items():
        if row.get('target', '').endswith('-' + role):
            row.update(role=role, profile=profile)
            break
    if 'packages' in row: row['packages'] = len(row['packages'])
    return row


def resource_plan():
    """Return the compiled resource plan of the Instance: the declared applications and their VMs."""
    result = subprocess.run([str(REPO / 'ansible/bin/platform-resources'), 'show'], capture_output=True, text=True,
                            timeout=300)
    if result.returncode:
        raise RuntimeError('cannot compile the declared applications: ' + result.stderr.strip()[-300:])
    return json.loads(result.stdout)


def declared_guests(instance, plan, box):
    """Map each Xen guest that the Platform declares running on the box to its Tailscale name."""
    declared = machines(instance)
    guests = {role: box + '-' + role for role in PROFILES if declared.get(box + '-' + role) == 'running'}
    # The app VMs come from the application declarations of the Instance.
    for spec in plan['app_vm_specs']:
        if spec['node'] == box and spec.get('runtime_state', 'running') == 'running':
            guests[spec['guest_spec']['guest_name']] = spec['tailnet_hostname']
    return guests


def declared_containers(plan, box, role):
    """Name the containers that the applications declared running on the box run in the role."""
    names = set()
    for entry in plan['apps'].values():
        if entry.get('enabled') is True and entry.get('runtime_state') == 'running' and box in entry.get('boxes', []):
            for workload in entry.get('containers', []):
                if workload['zone'] == role: names.update(workload['names'])
    return names


def drift(instance, plan, rows):
    """Name the VMs and the containers that run but that the Platform does not declare.

    An application that the Instance does not declare shows only as such VMs or containers.
    """
    vms, containers = [], []
    for row in rows:
        if row.get('state') != 'reachable' or 'role' not in row: continue
        box = row['target'][:-len(row['role']) - 1]
        if 'guests' in row:
            declared = declared_guests(instance, plan, box)
            vms += [{'box': box, 'guest': guest} for guest in row['guests'] if guest not in declared]
        if 'containers' in row:
            declared = declared_containers(plan, box, row['role'])
            containers += [{'target': row['target'], 'container': name} for name in row['containers']
                           if name not in declared]
    return {'vms': vms, 'containers': containers}


def health(target):
    """Run the health command of the role of the machine. Return its checks."""
    role = role_of(target) or target.rsplit('-', 1)[-1]
    try: result = subprocess.run(HEALTH[role](target[:-len(role) - 1]), capture_output=True, text=True,
                                 timeout=HEALTH_SECONDS)
    except subprocess.TimeoutExpired: raise RuntimeError('no health result in %d seconds' % HEALTH_SECONDS) from None
    if result.returncode: raise RuntimeError(result.stderr.strip()[-300:] or 'the health command failed')
    try:
        checks = json.loads(result.stdout.strip().splitlines()[-1])['checks']
        # restart_requires names the human action that a VM needs before it can work again.
        if not isinstance(checks, list) or any(set(item) - {'restart_requires'} != {'name', 'state', 'detail'}
                                               or item['state'] not in HEALTH_STATES
                                               or not all(isinstance(item[key], str) for key in item) for item in checks):
            raise ValueError()
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError('the health command returned an invalid result') from None
    return checks


def uptime(line, kind):
    """Read the seconds of `xl uptime KIND`, for example `print 7 1 day, 2:16:29`. None: the VM does not run."""
    match = re.fullmatch(r'(\S+)\s+\d+\s+(?:(\d+) days?, )?(\d+):(\d\d):(\d\d)', line.strip())
    if not line.strip(): return None
    if not match or match[1] != kind: raise ValueError()
    return int(match[2] or 0) * 86400 + int(match[3]) * 3600 + int(match[4]) * 60 + int(match[5])


def health_stop(target, text):
    """Validate the stop record that the dom0 keeps when the health check stopped the appliance."""
    if not text.strip(): return None
    value = json.loads(text)
    if (set(value) != {'check', 'detail', 'restart_requires', 'at', 'operation_id', 'hostname', 'image'} or
            value['hostname'] != target or type(value['at']) is not int or not re.fullmatch('[0-9a-f]{24}', value['operation_id']) or
            any(not isinstance(value[k], str) or not value[k].isprintable() for k in ('check', 'detail', 'restart_requires'))):
        raise ValueError()
    return {key: value[key] for key in ('check', 'detail', 'restart_requires', 'at')}


def appliance_image(target, output):
    """Validate the dom0 record of the installed appliance image. Return its build time, packages,
    uptime and health stop record.

    A newer build records the packages of the sealed image. An older build records only the
    build closure, which includes the build-only packages that the seal removed.
    """
    head, _, rest = output.partition('\nresult\n')
    installed, _, built = head.partition('\nbuilt\n')
    result, _, rest = rest.partition('\nuptime\n')
    running, _, stop = rest.partition('stop\n') if rest.startswith('stop\n') else rest.partition('\nstop\n')
    try:
        record, value = json.loads(installed.removeprefix('installed\n')), json.loads(result)
        if (record['state'] != 'installed' or record['hostname'] != target or value['success'] is not True or
                value['operation_id'] != record['operation_id'] or value['inputs_sha256'] != record['image']):
            raise ValueError()
        packages = value.get('runtime_packages') or value['packages']
        rows = [{'name': name, 'version': version} for name, version in sorted(packages.items())]
        if not rows or any(not re.fullmatch(FIELDS['P'][1], r['name']) or not re.fullmatch(VERSION, r['version']) for r in rows):
            raise ValueError()
        return {'image_built_at': int(built.strip()), 'image': record['image'][:24], 'operation': record['operation_id'],
                'packages': rows, 'package_source': 'sealed image' if value.get('runtime_packages') else 'build closure',
                'uptime': uptime(running, target.rsplit('-', 1)[-1]), 'health_stop': health_stop(target, stop)}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError('the dom0 returned an invalid appliance image record') from None


def appliance_spec(target):
    role = target.rsplit('-', 1)[-1]
    specs = [spec for spec in resource_plan()['app_vm_specs']
             if spec['app'] == APPLIANCES[role] and spec['node_hostname'] == target]
    if len(specs) != 1: raise RuntimeError(target + ' is not a declared machine. List the machines with: kk status')
    return specs[0]


def appliance_record(target):
    role = target.rsplit('-', 1)[-1]
    return appliance_image(target, run_probe(target[:-len(role) - 1] + '-dom0', APPLIANCE_PROBE.replace('@KIND@', role)))


def appliance_power(target, action, check=None):
    """Run the stop or start command of the appliance tool on this active controller."""
    role = target.rsplit('-', 1)[-1]
    reason = None if check is None else json.dumps(
        {'check': check['name'], 'detail': check['detail'], 'restart_requires': check['restart_requires']})
    try: result = subprocess.run([str(APPLIANCE_TOOLS[role]), action, '--box', target[:-len(role) - 1]], input=reason or '',
                                 capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired: raise RuntimeError('the appliance %s did not end in 300 seconds' % action) from None
    if result.returncode: raise RuntimeError(result.stderr.strip()[-300:] or 'the appliance ' + action + ' failed')


def unusable(checks):
    """The checks that make the VM unusable until a person acts."""
    return [c for c in checks if c.get('restart_requires') and c['state'] in ('warning', 'failed')]


def appliance(target):
    """Return the declared state, the image record and the health of an appliance without a login.

    A check that makes the appliance unusable stops it, after BOOT_GRACE seconds of uptime. The
    dom0 record keeps it stopped until kk restart. The Instance still declares it running.
    """
    declared = appliance_spec(target).get('runtime_state', 'running')
    row = {'target': target, 'declared': declared, 'state': 'appliance' if declared == 'running' else 'stopped'}
    if declared != 'running': return row
    try: row.update(appliance_record(target))
    except (RuntimeError, OSError) as error: row['image_error'] = str(error)[-300:]
    if row.get('health_stop'): return dict(row, state='stopped-health')
    try: row['health'] = health(target)
    except (RuntimeError, OSError) as error: return dict(row, health_error=str(error)[-300:])
    found = unusable(row['health'])
    if not found or 'image_error' in row: return row
    if row.get('uptime') is None or row['uptime'] < BOOT_GRACE:
        return dict(row, health_stop_deferred='the VM started %s seconds ago; the health check stops it only after %d seconds'
                    % (row.get('uptime', 'unknown'), BOOT_GRACE))
    try:
        appliance_power(target, 'stop', found[0])
        row.update(appliance_record(target))
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        return dict(row, health_stop_error=str(error)[-300:])
    return dict(row, state='stopped-health' if row.get('health_stop') else row['state'])


def restart(target):
    """Start an appliance that the health check stopped, wait for its boot work, and check it again."""
    role = target.rsplit('-', 1)[-1]
    if role not in APPLIANCES:
        raise RuntimeError('kk restart starts only a VM that its health check can stop: ' +
                           ', '.join('BOX-' + name for name in sorted(APPLIANCES)))
    if appliance_spec(target).get('runtime_state', 'running') != 'running':
        raise RuntimeError(target + ' is declared stopped in the Instance')
    if not appliance_record(target)['health_stop']:
        raise RuntimeError(target + ' was not stopped by the health check, so kk restart does not start it. See: kk status ' + target)
    appliance_power(target, 'start')
    deadline = time.monotonic() + BOOT_GRACE + 30
    while time.monotonic() < deadline:
        time.sleep(15)
        try: checks = health(target)
        except (RuntimeError, OSError): continue
        if not any(c['state'] == 'failed' for c in checks) and not unusable(checks): break
    return dict(appliance(target), restarted=True, observed_at=int(time.time()))


def health_targets(instance, plan):
    """The declared running machines whose role has a health check, in a fixed order."""
    targets = [name for name, state in machines(instance).items() if state == 'running' and role_of(name) in HEALTH]
    targets += [spec['node_hostname'] for spec in plan['app_vm_specs'] if spec['app'] in APPLIANCES.values()
                and spec.get('runtime_state', 'running') == 'running' and spec['node_hostname'].rsplit('-', 1)[-1] in HEALTH]
    return sorted(targets)


def health_summary(row):
    """Keep what the nightly record needs from one machine status: the problems, the warnings and a stop."""
    checks = row.get('health') or []
    value = {'target': row['target'], 'state': row.get('state'),
             'failed': [c['name'] + ': ' + c['detail'] for c in checks if c['state'] == 'failed'],
             'warnings': [c['name'] + ': ' + c['detail'] for c in checks if c['state'] == 'warning']}
    for key in ('error', 'health_error', 'health_stop_error', 'health_stop'):
        if row.get(key): value[key] = row[key]
    return value


def nightly():
    """Run the health check of each machine that has one, from the nightly run of the active controller.

    The machines are checked one at a time, so their probes never overlap. A check that finds an
    appliance unusable stops it, as kk status does.
    """
    guard = subprocess.run(['/usr/local/sbin/klokast-controller-guard', '--require-active'], capture_output=True, text=True,
                           timeout=60)
    if guard.returncode: return {'state': 'skipped', 'reason': 'standby'}
    view = platform_source.snapshot()
    results = []
    for target in health_targets(view['instance'], resource_plan()):
        try: results.append(health_summary(report(target)))
        except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            results.append({'target': target, 'state': 'unknown', 'failed': [], 'warnings': [], 'error': str(error)[-300:]})
    return {'state': 'complete', 'machines': results}


def health_stopped(box):
    """Name the Xen guests of the box that the health check stopped. A dom0 update leaves them stopped."""
    output = run_probe(box + '-dom0', HEALTH_STOPPED_PROBE)
    names = set(output.split())
    if not names <= set(APPLIANCES): raise RuntimeError('the dom0 returned an invalid health stop list')
    return names


def report(target=None):
    """Observe one declared machine with its packages, or all machines with package counts and drift."""
    view = platform_source.snapshot()
    declared = machines(view['instance'])
    if target and target.rsplit('-', 1)[-1] in APPLIANCES and target not in declared:
        return dict(appliance(target), observed_at=int(time.time()))
    if target:
        if target not in declared: raise RuntimeError(target + ' is not a declared machine. List the machines with: kk status')
        row = observe(target, declared[target])
        histories, role = row.get('history', {}), role_of(target)
        if row['state'] == 'reachable' and role != 'dom0':
            try: histories = history(run_probe(target[:-len(role)] + 'dom0', HISTORY_PROBE).splitlines()[1:])
            except (RuntimeError, OSError) as error: row['history_error'] = str(error)[-300:]
        if row['state'] == 'reachable' and role in HEALTH:
            try: row['health'] = health(target)
            except (RuntimeError, OSError) as error: row['health_error'] = str(error)[-300:]
        return dict(add_updates(row, histories), observed_at=int(time.time()))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda item: observe(*item), declared.items()))
    # A dom0 states the history of the machines of its own box only.
    histories = {name: entries for row in rows for name, entries in row.get('history', {}).items()
                 if role_of(name) and name[:-len(role_of(name))] == row['target'][:-len('dom0')]}
    rows = [summary(add_updates(row, histories)) for row in rows]
    value = {'observed_at': int(time.time()), 'machines': rows}
    try: value['drift'] = drift(view['instance'], resource_plan(), rows)
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        value['drift_error'] = str(error)[-300:]
    return value
