"""Validated development Instance views. Observations never supply policy."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess

INSTANCE = Path('/home/smith/private/klokast/instance')
REPO = Path('/home/smith/src/klokast/klokast-box')
MODE = Path('/etc/klokast/deployment.json')
BINARY = Path('/usr/local/bin/klokast')
GUARD = Path('/usr/local/sbin/klokast-controller-guard')


class SourceError(RuntimeError):
    pass


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceError('JSON contains a duplicate field: ' + key)
        result[key] = value
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def read_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 16 * 1024 * 1024:
        raise SourceError('required JSON file is absent, unsafe, or too large: ' + str(path))
    return json.loads(path.read_text(), object_pairs_hook=unique)


def require_development():
    info = MODE.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise SourceError('deployment lifecycle record must be root-owned and protected')
    if read_json(MODE) != {'schema_version': 1, 'lifecycle': 'development'}:
        raise SourceError('this automation supports development only; production requires an admitted Platform release')


def command(argv):
    result = subprocess.run([str(v) for v in argv], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=120, check=False,
                            env={key: value for key, value in os.environ.items()
                                 if key not in {'KLOKAST_CONTROLLER_HA_MARKER', 'PYTHONPATH', 'PYTHONHOME'}})
    if result.returncode:
        raise SourceError('command failed: ' + str(argv[0]) + ': ' + result.stderr[-2048:].strip())
    return result.stdout


def require_controller():
    if pwd.getpwuid(os.geteuid()).pw_name not in {'smith', 'root'}:
        raise SourceError('run on the active controller as smith')
    status = json.loads(command([GUARD, '--status', '--json', '--require-active']), object_pairs_hook=unique)
    local = socket.gethostname().split('.')[0]
    if (status.get('configured') is not True or status.get('active') is not True or
            status.get('role') != 'active' or status.get('hostname') != local or
            not local.endswith('-ops') or local.startswith(('vultr-', 'hetzner-'))):
        raise SourceError('operation requires the configured active box controller')
    require_development()
    return status


def as_controller(argv):
    return command(['/usr/bin/doas', '-u', 'smith', *argv] if os.geteuid() == 0 else argv)


def implementation():
    names = as_controller(['git', '-C', REPO, 'ls-files', '-z', '--cached', '--others',
                           '--exclude-standard', '--', 'cmd', 'internal', 'schemas', 'ansible',
                           'klokast-ops', 'apps', 'templates/instance', 'vendor', 'assets.go',
                           'cloud-providers.json', 'go.mod', 'go.sum']).split('\0')
    content = hashlib.sha256()
    for name in sorted(set(names) - {''}):
        path = REPO / name
        if not path.exists(): continue
        if path.is_symlink() or not path.is_file(): raise SourceError('implementation source must be a regular file: ' + name)
        content.update(name.encode() + b'\0')
        content.update(hashlib.sha256(path.read_bytes()).digest())
    return {'commit': as_controller(['git', '-C', REPO, 'rev-parse', 'HEAD']).strip(),
            'dirty': bool(as_controller(['git', '-C', REPO, 'status', '--porcelain']).strip()),
            'source_sha256': content.hexdigest()}


def snapshot():
    require_controller()
    raw = (INSTANCE / 'klokast-instance.json').read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    registry = json.loads(as_controller([BINARY, 'registry', '--instance', INSTANCE, '--json']), object_pairs_hook=unique)
    if (registry.get('valid') is not True or registry.get('kind') != 'klokast.registry.v1' or
            registry.get('inputs') != [{'path': 'klokast-instance.json', 'sha256': sha}]):
        raise SourceError('Instance validation failed or desired state changed during rendering')
    instance = json.loads(raw, object_pairs_hook=unique)
    if (INSTANCE / 'klokast-instance.json').read_bytes() != raw:
        raise SourceError('Instance changed during rendering; retry the operation')
    return {'instance': instance, 'instance_sha256': sha, 'rendered': registry,
            'implementation': implementation()}


def registry_status(view=None):
    view = view or snapshot()
    return {'schema_version': 1, 'kind': 'klokast.registry-source-status.v1',
            'source': 'instance', 'instance_sha256': view['instance_sha256'],
            'engine_commit': view['implementation']['commit'], 'rendered': view['rendered']}


def controllers(view=None):
    view = view or snapshot()
    pair = {role: {'box': box, 'hostname': box + '-ops'}
            for role, box in view['instance']['controllers'].items() if box}
    return {'schema_version': 1, 'kind': 'klokast.controller-identity-status.v1',
            'source': 'instance', 'instance_sha256': view['instance_sha256'],
            'engine_commit': view['implementation']['commit'], 'controllers': pair}


def retention(view=None):
    view = view or snapshot()
    projection = {'boxes': sorted(view['instance']['boxes']), 'datasets': [
        {'app': app, 'dataset': dataset, 'box': data['box'], 'retention': data['retention'],
         'desired_state': binding['desired-state']}
        for app, binding in sorted(view['instance']['apps'].items())
        for dataset, data in sorted(binding.get('data', {}).items())]}
    return {'schema_version': 1, 'kind': 'klokast.vm-retention-source.v1', 'source': 'instance',
            'instance_sha256': view['instance_sha256'], 'engine_commit': view['implementation']['commit'],
            'private_commit': view['rendered']['repository'].get('head_commit', ''),
            'inputs': view['rendered']['inputs'], 'projection': projection,
            'projection_sha256': digest(projection), 'adoption_authorized': False}
