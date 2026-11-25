"""Read-only state of the declared Platform machines: last update, installed packages, update history and drift."""
import calendar
import concurrent.futures
import json
from pathlib import Path
import re
import socket
import subprocess
import time

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


def report(target=None):
    """Observe one declared machine with its packages, or all machines with package counts and drift."""
    view = platform_source.snapshot()
    declared = machines(view['instance'])
    if target:
        if target not in declared: raise RuntimeError(target + ' is not a declared machine. List the machines with: kk status')
        row = observe(target, declared[target])
        histories, role = row.get('history', {}), role_of(target)
        if row['state'] == 'reachable' and role != 'dom0':
            try: histories = history(run_probe(target[:-len(role)] + 'dom0', HISTORY_PROBE).splitlines()[1:])
            except (RuntimeError, OSError) as error: row['history_error'] = str(error)[-300:]
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
