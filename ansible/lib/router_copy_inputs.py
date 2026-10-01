"""Stage an authenticated networkless copy capsule for one router generation pair."""
import json
import os
from pathlib import Path
import stat
import uuid

import router_generations
import router_updates
import vm_template_inputs
from router_update_controller import load, write
from router_copy_contract import capsule, job
from platform_updates import UpdateError


def stage(source, work, repo, request, old, candidate):
    source, work, repo = Path(source), Path(work), Path(repo)
    manifest = load(source / 'inputs.json')
    profile = load(repo / 'ansible/update-profiles/router-alpine-v2.json')
    router_updates.validate_inputs(manifest, profile, request['engine_commit'])
    value = job(request, old, candidate, manifest['inputs_sha256'])
    if not work.is_dir() or work.is_symlink() or any(work.iterdir()):
        raise ValueError('router copy capsule requires a new empty controller staging directory')
    entry = work / 'copy-job.json'
    entry.write_text(json.dumps(value, sort_keys=True) + '\n')
    entry.chmod(0o600)
    files = repo / 'ansible/roles/router-state-copy/files'
    boot = vm_template_inputs.bootstrap(source, work / 'boot', files / 'router-copy-transaction-guest',
        expected_profile=router_updates.PROFILE,
        job_files={'router-copy-job.json': entry,
                   'usr/local/lib/klokast/router_state.py': repo / 'ansible/lib/router_state.py',
                   'usr/local/libexec/router-state-copy-guest': files / 'router-state-copy-guest'})
    vm_template_inputs.verify_inputs(source, manifest, expected_profile=router_updates.PROFILE)
    return {'kind': 'klokast.router-copy-capsule.v2', 'operation_id': request['operation_id'],
            'inputs_sha256': manifest['inputs_sha256'], 'engine_commit': request['engine_commit'],
            'transaction_sha256': router_generations.digest(request), 'job_sha256': router_generations.digest(value),
            'bootstrap': boot, 'domains': {phase: str(uuid.uuid4()) for phase in ('forward', 'reverse')}}


def retain(source, work, repo, request, old, candidate, *, allow_build=True):
    """Build once and reuse only the exact private capsule and boot bytes.

    A partial build remains for explicit reconciliation. Never regenerate
    domain identities or replace an existing boot environment on retry.
    """
    source, work = Path(source), Path(work)
    def private(path, *, directory=False):
        info = path.lstat()
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        if (not kind(info.st_mode) or info.st_uid != os.geteuid() or
                stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600) or
                not directory and info.st_nlink != 1):
            raise UpdateError('router copy capsule path has unsafe private metadata: ' + path.name)
    private(work.parent, directory=True)
    manifest = load(source / 'inputs.json')
    if not work.exists() and not work.is_symlink():
        if not allow_build:
            raise UpdateError('router cutover requires its existing staged copy capsule; no rebuild is allowed')
        work.mkdir(mode=0o700)
        value = stage(source, work, repo, request, old, candidate)
        capsule(value, request, old, candidate)
        for name in ('kernel', 'initramfs'):
            (work / 'boot' / name).chmod(0o600)
        write(work / 'capsule.json', value)
    private(work, directory=True)
    if not (work / 'capsule.json').exists():
        raise UpdateError('router copy capsule build is incomplete; reconcile this exact operation')
    if {path.name for path in work.iterdir()} != {'capsule.json', 'copy-job.json', 'boot'}:
        raise UpdateError('router copy capsule workspace has unexpected files; reconcile the exact namespace')
    private(work / 'capsule.json')
    private(work / 'copy-job.json')
    value = capsule(load(work / 'capsule.json'), request, old, candidate)
    if (value['inputs_sha256'] != manifest['inputs_sha256'] or
            load(work / 'copy-job.json') != job(request, old, candidate, manifest['inputs_sha256'])):
        raise UpdateError('retained router copy capsule differs from its frozen source or job')
    private(work / 'boot', directory=True)
    if {path.name for path in (work / 'boot').iterdir()} != {'kernel', 'initramfs'}:
        raise UpdateError('router copy boot workspace has unexpected files; reconcile the exact namespace')
    for name, expected in value['bootstrap'].items():
        path = work / 'boot' / name
        private(path)
        if path.stat().st_size != expected['bytes'] or vm_template_inputs.sha256(path) != expected['sha256']:
            raise UpdateError('retained router copy boot bytes changed: ' + name)
    return value
