"""Explicit non-live leaf responses for consumer_absence_commands only."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent))
from consumer_absence_commands import stable


def main(program):
    view = Path(os.environ['KLOKAST_ABSENCE_VIEW'])
    argv = sys.argv[1:]
    fixtures = json.loads((view / 'broker-fixtures.json').read_text())
    engine = fixtures['registry-source-status']['engine_commit']

    def record(boundary, value):
        with Path(os.environ['KLOKAST_ABSENCE_TRACE']).open('a') as stream:
            stream.write(json.dumps(stable(dict(boundary=boundary, program=program, **value), view), sort_keys=True) + '\n')

    if program in ('doas', 'sudo'):
        if len(argv) == 2 and argv[0] == '/usr/local/sbin/ksa-apply' and argv[1] in fixtures:
            record('source-broker', {'operation': argv[1]})
            print(json.dumps(fixtures[argv[1]]))
            return
        raise SystemExit('absence fixture refuses privilege or credential operation: ' + repr(argv))
    if program == 'klokast-controller-guard':
        if set(argv) - {'--status', '--json', '--require-active'}:
            raise SystemExit('absence fixture refuses controller guard mutation')
        print(json.dumps(dict(active=True, configured=True)))
        return
    if program == 'hostname':
        print(fixtures['controller-identity-status']['controllers']['active']['hostname'])
        return
    if program == 'git':
        if argv[:1] == ['-C']:
            argv = argv[2:]
        if argv == ['status', '--short']:
            return
        if argv == ['rev-parse', '--abbrev-ref', 'HEAD']:
            print('main')
        elif argv == ['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}']:
            print('origin/main')
        elif argv in (['rev-parse', 'HEAD'], ['rev-parse', 'origin/main']):
            print(engine)
        else:
            raise SystemExit('absence fixture refuses unrecognized Git command: ' + repr(argv))
        record('approval', {'args': argv})
        return
    if program == 'platform-image-build':
        record('builder', {'args': argv})
        return
    if program == 'tailscale':
        if argv[:1] != ['ssh'] or len(argv) < 3:
            raise SystemExit('absence fixture refuses unrecognized Tailscale operation')
        content = sys.stdin.read() if not sys.stdin.isatty() else ''
        content = stable(content, view)
        record('runtime-dispatch', {'args': argv, 'stdin_sha256': hashlib.sha256(content.encode()).hexdigest()})
        return
    if program == 'ansible-playbook':
        inventories, variables, playbooks = [], [], []
        for index, arg in enumerate(argv):
            if arg in ('-i', '--inventory'):
                inventories += ['-i', argv[index + 1]]
            if arg in ('-e', '--extra-vars'):
                value = argv[index + 1]
                variables.append(Path(value[1:]).read_text() if value.startswith('@') else value)
            if arg.endswith(('.yml', '.yaml')) and '/playbooks/' in arg:
                path = Path(arg)
                playbooks.append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        if not inventories or not playbooks:
            raise SystemExit('absence fixture requires actual inventory and playbook inputs')
        checked = subprocess.run([os.environ['KLOKAST_ABSENCE_INVENTORY'], *inventories, '--list'],
                                 text=True, capture_output=True, check=True)
        graph = json.loads(checked.stdout)
        record('runtime-dispatch', {'args': argv, 'playbooks': playbooks,
               'variables': variables, 'inventory': graph})
        return
    raise SystemExit('unrecognized absence fixture program: ' + program)


if __name__ == '__main__':
    main(PROGRAM)
