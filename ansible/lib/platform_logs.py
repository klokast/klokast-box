"""Controller side of kk logs: scan, expire and fetch log files with the checked-in retention policy.

The local scope is this controller's own state; both controllers apply it, also as standby.
The platform scope is dom0 and the declared running VMs except the controllers; only the
active controller applies it, through 76-platform-logs.yml.
"""
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile

import log_retention

REPO = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO / 'ansible/playbooks/76-platform-logs.yml'
GUARD = '/usr/local/sbin/klokast-controller-guard'
LOGS = Path(log_retention.SMITH) / 'logs/platform-logs'
MACHINE = re.compile(r'[a-z][a-z0-9-]{0,62}')
PLAYBOOK_SECONDS = 1800


def local_machine():
    return socket.gethostname().split('.')[0]


def run_log(action):
    """Open this run's log; the retention policy keeps the last 10 runs of this directory."""
    LOGS.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    return (LOGS / (stamp + '-' + action + '.log')).open('x')


def require_active():
    result = subprocess.run([GUARD, '--require-active'], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('the platform scope needs the active controller: ' +
                           (result.stderr or result.stdout).strip()[-300:])


def platform_machines():
    """Return the declared machines of the platform scope with their declared state."""
    import machine_status
    import platform_source
    declared = machine_status.machines(platform_source.snapshot()['instance'])
    return {name: state for name, state in declared.items() if log_retention.role_of(name) != 'ops'}


def playbook(action, machines, log, path=None):
    """Run the fixed playbook on the given machines; return {machine: result, output directory}."""
    output = tempfile.mkdtemp(prefix='platform-logs.')
    try:
        variables = {'logs_action': action, 'logs_output': output}
        if path is not None: variables['logs_path'] = path
        argv = ['ansible-playbook', '-vv', '-i', str(REPO / 'ansible/execution-inventory/hosts'), str(PLAYBOOK),
                '--limit', ','.join(sorted(machines)), '-e', json.dumps(variables)]
        print('platform-logs: ' + ' '.join(argv[:-1]) + " -e '" + argv[-1] + "'", file=log, flush=True)
        result = subprocess.run(argv, cwd=REPO, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                timeout=PLAYBOOK_SECONDS,
                                env=dict(os.environ, ANSIBLE_CONFIG=str(REPO / 'ansible/ansible.cfg')))
        print('platform-logs: playbook exit status %d' % result.returncode, file=log, flush=True)
        values = {}
        for machine in machines:
            saved = Path(output) / (machine + '.json')
            try: values[machine] = json.loads(saved.read_text())
            except (OSError, ValueError):
                values[machine] = {'machine': machine, 'state': 'unreachable',
                                   'error': 'no result; see the run log ' + log.name}
        return values, Path(output)
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


def local(action, apply=False, files=False):
    machine = local_machine()
    if action == 'scan':
        _, found, rows, _ = log_retention.scan(machine)
        return log_retention.summary(machine, found, rows, files)
    return log_retention.clean(machine, apply)


def platform(action, apply=False, files=False, machine=None, log=None):
    require_active()
    declared = platform_machines()
    if machine is not None and machine not in declared:
        raise RuntimeError(machine + ' is not a declared machine of the platform scope. List them with: kk logs')
    selected = {machine: declared[machine]} if machine else declared
    stopped = {name: {'machine': name, 'state': 'stopped'} for name, state in selected.items() if state != 'running'}
    running = sorted(name for name in selected if name not in stopped)
    task = 'files' if files else 'apply' if apply else action
    values, output = playbook(task, running, log) if running else ({}, None)
    if output is not None: shutil.rmtree(output, ignore_errors=True)
    return [dict(values, **stopped)[name] for name in sorted(selected)]


def execute(action, scope, apply=False, files=False, machine=None):
    """Run one request and return its JSON result. Every run writes a log."""
    with run_log(action + '-' + scope) as log:
        print('platform-logs: %s %s apply=%s machine=%s' % (action, scope, apply, machine), file=log, flush=True)
        if scope == 'local':
            if machine not in (None, local_machine()):
                raise RuntimeError('the local scope covers only ' + local_machine())
            machines = [local(action, apply, files)]
        else:
            machines = platform(action, apply, files, machine, log)
        for value in machines:
            totals = value.get('totals', {})
            print('platform-logs: %s %s files=%s expire=%s deleted=%s errors=%s' % (
                value['machine'], value['state'], totals.get('files'), totals.get('expire'),
                value.get('deleted', {}).get('files'), len(value.get('errors', []))), file=log, flush=True)
        return {'machines': machines, 'log': log.name}


def fetch(machine, path, stream):
    """Copy one listed log file to stream."""
    with run_log('fetch') as log:
        print('platform-logs: fetch %s %s' % (machine, path), file=log, flush=True)
        if machine == local_machine():
            log_retention.check(machine, path)
            with open(path, 'rb') as source: shutil.copyfileobj(source, stream)
            return
        require_active()
        declared = platform_machines()
        if declared.get(machine) != 'running':
            raise RuntimeError(machine + ' is not a running declared machine of the platform scope')
        values, output = playbook('fetch', [machine], log, path)
        try:
            data = output / (machine + '.data')
            if values[machine].get('path') != path or not data.is_file():
                raise RuntimeError(values[machine].get('error') or 'the file was not copied; see the run log ' + log.name)
            with data.open('rb') as source: shutil.copyfileobj(source, stream)
        finally:
            shutil.rmtree(output, ignore_errors=True)


def nightly():
    """Apply the policy from the nightly coordinator. The caller's log receives the playbook output."""
    result = {'local': summary_line(local('clean', apply=True))}
    try: require_active()
    except RuntimeError:
        result['platform'] = 'skipped: standby'
        return result
    values = platform('clean', apply=True, log=sys.stdout)
    result['platform'] = [summary_line(value) for value in values]
    return result


def summary_line(value):
    deleted = value.get('deleted', {})
    return {'machine': value['machine'], 'state': value['state'], 'deleted': deleted.get('files', 0),
            'errors': len(value.get('errors', [])), **({'error': value['error']} if 'error' in value else {})}
