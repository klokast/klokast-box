"""Observe and show the boots of the declared machines. Each dom0 keeps the history of its box.

See doc/platform-updates.md#boot-history.
"""
import calendar
import concurrent.futures
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import machine_status
import platform_source

REPO = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO / 'ansible/playbooks/76-boot-history.yml'
GUARD = '/usr/local/sbin/klokast-controller-guard'
PLAYBOOK_SECONDS = 600
# The machine runs these scripts without privilege.
PROBE = r'''set -eu
echo "hostname $(hostname)"
echo "boot $(cat /proc/sys/kernel/random/boot_id)"
echo "clock $(date +%s)"
echo "uptime $(cut -d. -f1 /proc/uptime)"
'''
HISTORY_PROBE = r'''for file in /mnt/dom0_data/klokast-boot-history/*.jsonl; do
    if [ -f "$file" ]; then tail -n 400 "$file" | sed "s/^/$(basename "$file" .jsonl) /"; fi
done
'''
BOOT_ID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
FIELDS = {'hostname': machine_status.NAME, 'boot': BOOT_ID, 'clock': '[0-9]{1,12}', 'uptime': '[0-9]{1,12}'}
# An update accepts a VM soon after it starts it.
ACCEPTANCE_SECONDS = 3600


def require_active():
    result = subprocess.run([GUARD, '--require-active'], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('the boot history needs the active controller: ' + (result.stderr or result.stdout).strip()[-300:])


def current(target):
    """Return the boot that the machine runs now. A machine can state only its own values."""
    values = {}
    for line in machine_status.run_probe(target, PROBE).splitlines():
        name, _, value = line.partition(' ')
        if name not in FIELDS or name in values or not re.fullmatch(FIELDS[name], value):
            raise RuntimeError('the machine returned an invalid boot status')
        values[name] = value
    if set(values) != set(FIELDS): raise RuntimeError('the machine returned an incomplete boot status')
    if values['hostname'] != target: raise RuntimeError('the machine reports the hostname ' + values['hostname'])
    return {'boot_id': values['boot'], 'booted_at': int(values['clock']) - int(values['uptime'])}


def observe(targets):
    """Read the current boot of each machine. Return the boots and the errors, by machine."""
    def one(target):
        try: return target, current(target), None
        except (RuntimeError, OSError) as error: return target, None, str(error)[-300:]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(one, targets))
    return {t: boot for t, boot, _ in rows if boot}, {t: error for t, _, error in rows if error}


def dom0_of(target):
    return target[:-len(machine_status.role_of(target))] + 'dom0'


def check():
    """Record the current boot of each declared running machine on the dom0 of its box."""
    require_active()
    declared = machine_status.machines(platform_source.snapshot()['instance'])
    boots, errors = observe([target for target, state in declared.items() if state == 'running'])
    observations = {}
    for target, boot in boots.items(): observations.setdefault(dom0_of(target), {})[target] = boot
    # A dom0 that does not answer cannot store the boots of its box.
    hosts = sorted(host for host in observations if host in boots)
    if hosts:
        argv = ['ansible-playbook', '-v', '-i', str(REPO / 'ansible/execution-inventory/hosts'), str(PLAYBOOK),
                '--limit', ','.join(hosts), '-e', json.dumps({'boot_observations': {h: observations[h] for h in hosts}})]
        result = subprocess.run(argv, cwd=REPO, stdin=subprocess.DEVNULL, stdout=sys.stderr, timeout=PLAYBOOK_SECONDS,
                                env=dict(os.environ, ANSIBLE_CONFIG=str(REPO / 'ansible/ansible.cfg')))
        if result.returncode: errors['playbook'] = '76-boot-history.yml exit status %d' % result.returncode
    # A machine that does not answer is observed by the next check.
    return {'state': 'failed' if 'playbook' in errors else 'incomplete' if errors else 'complete', 'observed': len(boots),
            **({'errors': errors} if errors else {})}


def nightly():
    """Record the boots from the nightly coordinator. The caller's log receives the playbook output."""
    try: require_active()
    except RuntimeError: return {'state': 'skipped', 'reason': 'standby'}
    return check()


def history(lines):
    """Validate the boot history of one box. Return the boots of each machine, oldest first."""
    found = {}
    def require(value, pattern):
        if not isinstance(value, str) or not re.fullmatch(pattern, value): raise ValueError()
        return value
    def at(text): return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))
    for line in lines:
        machine, _, text = line.partition(' ')
        # A power loss can leave a partial or an empty line; the writer starts the next entry on a new line.
        if not re.fullmatch(machine_status.NAME, machine): raise RuntimeError('the dom0 returned an invalid boot history')
        boots = found.setdefault(machine, {})
        try: entry = json.loads(text)
        except ValueError: continue
        try:
            boot_id, event, moment = require(entry['boot_id'], BOOT_ID), entry['event'], at(entry['at'])
            if event == 'stop':
                if boot_id in boots: boots[boot_id]['stopped_at'] = moment
                continue
            if event != 'boot': raise ValueError()
            row = {'boot_id': boot_id, 'at': moment, 'source': require(entry['source'], 'boot|check'),
                   'corrected': entry.get('corrected') is True}
            if 'seen' in entry: row['seen'] = at(entry['seen'])
            if 'kernel' in entry: row['kernel'] = require(entry['kernel'], r'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}')
            if 'operation' in entry:
                row.update(operation=require(entry['operation'], '[0-9a-f]{24}'), work=require(entry['work'], 'update|reboot'))
            boots.setdefault(boot_id, {}).update(row)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise RuntimeError('the dom0 returned an invalid boot history') from None
    return {machine: list(boots.values()) for machine, boots in found.items()}


def describe(target, boots, live, updates):
    """Add to each boot its end, and the update that started a VM. Return the boots, oldest first.

    A missing stop record is evidence only for a dom0 boot that its own service recorded, and
    only when a later boot exists.
    """
    rows = [dict(boot) for boot in boots]
    if live and all(row['boot_id'] != live['boot_id'] for row in rows):
        rows.append({'boot_id': live['boot_id'], 'at': live['booted_at'], 'source': 'live', 'corrected': False})
    for index, row in enumerate(rows):
        later = rows[index + 1:]
        if live and row['boot_id'] == live['boot_id']: row['end'] = 'running'
        elif 'stopped_at' in row: row['end'] = 'clean'
        elif machine_status.role_of(target) == 'dom0' and row['source'] == 'boot' and later: row['end'] = 'no stop record'
        else: row['end'] = 'unknown'
        if machine_status.role_of(target) == 'dom0': continue
        # The check or the live read saw this boot after the update was accepted, so no other boot came between.
        limit = min(row['at'] + ACCEPTANCE_SECONDS, row.get('seen', int(time.time())),
                    later[0]['at'] if later else int(time.time()))
        for update in updates:
            if not update['seeded'] and row['at'] <= update['at'] <= limit:
                row.update(operation=update['operation'], work='update')
    return rows


def show(targets):
    """Return the boot history of the selected machines, or of all declared machines."""
    require_active()
    declared = machine_status.machines(platform_source.snapshot()['instance'])
    unknown = sorted(set(targets) - set(declared))
    if unknown: raise RuntimeError(', '.join(unknown) + ': not a declared machine. List the machines with: kk status')
    selected = [target for target in declared if not targets or target in targets]
    hosts = sorted({dom0_of(target) for target in selected})
    def read(host):
        try:
            boots = history(machine_status.run_probe(host, HISTORY_PROBE).splitlines())
            updates = machine_status.history(machine_status.run_probe(host, machine_status.HISTORY_PROBE).splitlines()[1:])
            return host, (boots, updates, None)
        except (RuntimeError, OSError) as error: return host, ({}, {}, str(error)[-300:])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: stored = dict(pool.map(read, hosts))
    # This command never contacts a machine that is declared stopped.
    live, errors = observe([target for target in selected if declared[target] == 'running'])
    rows = []
    for target in selected:
        boots, updates, error = stored[dom0_of(target)]
        row = {'target': target, 'declared': declared[target],
               'boots': describe(target, boots.get(target, []), live.get(target), updates.get(target, []))}
        if error: row['history_error'] = dom0_of(target) + ': ' + error
        if target in errors: row['error'] = errors[target]
        rows.append(row)
    return {'observed_at': int(time.time()), 'machines': rows}
