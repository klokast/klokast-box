"""Explicit non-live leaf responses for consumer_absence_commands only."""

import hashlib
import json
import os
from contextlib import contextmanager, ExitStack, nullcontext
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from consumer_absence_commands import stable


def source_event(operation):
    trace = os.environ.get('KLOKAST_ABSENCE_TRACE')
    if trace:
        with Path(trace).open('a') as stream:
            stream.write(json.dumps({'boundary': 'source-reader', 'program': 'klokast',
                                     'operation': operation}, sort_keys=True) + '\n')


@contextmanager
def source_fixture(view):
    """Supply controller and Go projection replies only in a test process.

    The real snapshot and reader validation still run. No production reader
    accepts a fixture flag or environment switch.
    """
    import platform_source as source
    import platform_resource_runtime as runtime
    registry = json.loads((view / 'registry.json').read_text())
    inventory = json.loads((view / 'inventory.json').read_text())
    pair = json.loads((view / 'controller.json').read_text())
    instance = view / 'private/instance'

    def projection(argv):
        args = [str(value) for value in argv]
        for operation in ('registry', 'inventory'):
            if args == [str(source.BINARY), operation, '--instance', str(instance), '--json']:
                source_event(operation)
                value = dict(registry) if operation == 'registry' else {'projection': inventory}
                value.update(valid=True, kind='klokast.' + operation + '.v1')
                value['inputs'] = [{'path': 'klokast-instance.json',
                    'sha256': hashlib.sha256((instance / 'klokast-instance.json').read_bytes()).hexdigest()}]
                return json.dumps(value)
        raise AssertionError('source fixture refuses unrecognized command: ' + repr(args))

    with ExitStack() as stack:
        stack.enter_context(patch.object(source, 'INSTANCE', instance))
        stack.enter_context(patch.object(source, 'REPO', view))
        stack.enter_context(patch.object(source, 'require_controller', return_value={
            'active': True, 'configured': True, 'role': 'active',
            'hostname': pair['active']['hostname']}))
        stack.enter_context(patch.object(source, 'require_development'))
        stack.enter_context(patch.object(source, 'as_controller', side_effect=projection))
        stack.enter_context(patch.object(source, 'implementation', return_value={
            'commit': registry['engine']['commit'], 'dirty': False, 'source_sha256': 'a' * 64}))
        stack.enter_context(patch.object(runtime, 'RUN_ROOT', view / 'runtime'))
        stack.enter_context(patch.object(runtime, 'vm_update_installation_lock', nullcontext))
        yield


def main(program):
    view = Path(os.environ['KLOKAST_ABSENCE_VIEW'])
    argv = sys.argv[1:]
    registry = json.loads((view / 'registry.json').read_text())
    pair = json.loads((view / 'controller.json').read_text())
    engine = registry['engine']['commit']

    def record(boundary, value):
        payload = dict(boundary=boundary, program=program)
        payload.update(value)
        with Path(os.environ['KLOKAST_ABSENCE_TRACE']).open('a') as stream:
            stream.write(json.dumps(stable(payload, view), sort_keys=True) + '\n')

    def digest(value):
        return hashlib.sha256(json.dumps(stable(value, view), sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def input_digest(path):
        path = Path(path)
        files = sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
        value = []
        for item in files:
            relative = str(item.relative_to(path)) if path.is_dir() else item.name
            value.append((relative, hashlib.sha256(item.read_bytes()).hexdigest()))
        return digest(value)

    if program in ('doas', 'sudo'):
        raise SystemExit('absence fixture refuses privilege or credential operation: ' + repr(argv))
    if program == 'klokast-controller-guard':
        if set(argv) - {'--status', '--json', '--require-active'}:
            raise SystemExit('absence fixture refuses controller guard mutation')
        print(json.dumps(dict(active=True, configured=True)))
        return
    if program == 'hostname':
        print(pair['active']['hostname'])
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
                playbooks.append(dict(path=str(path), sha256=hashlib.sha256(stable(path.read_text(), view).encode()).hexdigest()))
        if not inventories or not playbooks:
            raise SystemExit('absence fixture requires actual inventory and playbook inputs')
        sources = inventories[1::2]
        cache = Path(os.environ['KLOKAST_ABSENCE_CACHE'])
        cache.mkdir(exist_ok=True)
        cache_key = digest([input_digest(source) for source in sources])
        cached = cache / (cache_key + '.json')
        try:
            graph = json.loads(cached.read_text())
            # Retain the source read when an identical real parse is reused.
            source_event('inventory')
        except FileNotFoundError:
            checked = subprocess.run([os.environ['KLOKAST_ABSENCE_INVENTORY'], *inventories, '--list'],
                                     text=True, capture_output=True, check=True)
            graph = json.loads(checked.stdout)
            temporary = cache / (cache_key + '.' + str(os.getpid()))
            temporary.write_text(json.dumps(graph))
            os.replace(temporary, cached)
        record('runtime-dispatch', {'args': argv, 'playbooks': playbooks,
               'variables_sha256': digest(variables), 'inventory_sha256': digest(graph)})
        return
    raise SystemExit('unrecognized absence fixture program: ' + program)


if __name__ == '__main__':
    main(PROGRAM)
