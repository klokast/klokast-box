"""Controller-local frozen replacement sources; current authority stays independent."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

ROOT = Path('/home/smith/private/klokast/ops-replacement-inputs')
INSTANCE = Path('/home/smith/private/klokast/instance')


def run(argv):
    return subprocess.check_output([str(v) for v in argv], text=True, timeout=180).strip()


def revision(repo, approved=False):
    if run(['git', '-C', repo, 'status', '--porcelain']):
        raise RuntimeError('replacement inputs have uncommitted changes: ' + str(repo))
    commit = run(['git', '-C', repo, 'rev-parse', 'HEAD'])
    if approved:
        remote = run(['git', '-C', repo, 'ls-remote', 'origin', 'refs/heads/main']).split()
        if not remote or remote[0] != commit:
            raise RuntimeError('source is not at approved upstream main: ' + str(repo))
    return commit


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    with temporary.open('x') as stream:
        temporary.chmod(0o600)
        json.dump(value, stream, sort_keys=True); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def static_inventory(graph):
    variables = graph.get('_meta', {}).get('hostvars', {})
    def group(name, parents=()):
        if name in parents: raise RuntimeError('cyclic frozen inventory')
        row = graph.get(name, {})
        return {'hosts': {host: variables.get(host, {}) for host in row.get('hosts', [])},
                'vars': row.get('vars', {}),
                'children': {child: group(child, (*parents, name)) for child in row.get('children', [])}}
    return {'all': group('all')}


def freeze(repo, variables):
    config = variables['ops_replace_configuration']
    for source, field in ((repo, 'engine_commit'), (INSTANCE, 'instance_commit')):
        if revision(source, True) != config[field]:
            raise RuntimeError('approved inputs changed before snapshot creation')
    ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    token = uuid.uuid4().hex[:24]
    work = ROOT / token; work.mkdir(mode=0o700)
    config['input_snapshot'] = token
    for source, name, field in ((repo, 'public', 'engine_commit'), (INSTANCE, 'instance', 'instance_commit')):
        run(['git', 'clone', '--quiet', '--no-hardlinks', '--no-checkout', source, work / name])
        run(['git', '-C', work / name, 'checkout', '--quiet', '--detach', config[field]])
    # Render once from the recorded Instance and bind all host/group values.
    value = json.loads(run(['/usr/local/bin/klokast', 'inventory', '--instance', work / 'instance', '--json']))
    if value.get('valid') is not True: raise RuntimeError('frozen Instance inventory is invalid')
    inventory = static_inventory(value['projection']['inventory'])
    # Inventory policy remains part of the pinned public tree.
    directory = work / 'inventory'; directory.mkdir(mode=0o700)
    (directory / 'group_vars').symlink_to(work / 'public/ansible/inventory-policy/group_vars')
    save(directory / 'hosts.json', inventory)
    manifest = {'variables': variables, 'inventory': inventory}
    save(work / 'manifest.json', manifest)
    return work, checksum(manifest)


def recover(record):
    config = record['requested_configuration']
    token = config.get('input_snapshot', '')
    if not re.fullmatch('[0-9a-f]{24}', token):
        raise RuntimeError('unfinished replacement has no verified source snapshot; refuse resume')
    work = ROOT / token
    if work.is_symlink() or not work.is_dir() or work.stat().st_mode & 0o077:
        raise RuntimeError('replacement snapshot is absent or not private')
    manifest = json.loads((work / 'manifest.json').read_text())
    # The hash is bound by the protected dom0 record before disk allocation.
    if checksum(manifest) != record.get('inputs_sha256'):
        raise RuntimeError('replacement input snapshot checksum differs; refuse resume')
    saved = manifest['variables']['ops_replace_configuration']
    if saved != config or manifest['variables']['ops_replace_image'] != record['image']:
        raise RuntimeError('replacement snapshot selects different inputs')
    for name, field in (('public', 'engine_commit'), ('instance', 'instance_commit')):
        if revision(work / name) != config[field]:
            raise RuntimeError('replacement source snapshot differs from recorded revision')
    if json.loads((work / 'inventory/hosts.json').read_text()) != manifest['inventory']:
        raise RuntimeError('replacement inventory snapshot changed')
    return work, manifest['variables']
