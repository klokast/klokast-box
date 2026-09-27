"""Stage an authenticated networkless copy capsule for one router generation pair."""
import json
from pathlib import Path

import router_generations
import router_transaction
import router_updates
import vm_template_inputs
from router_update_controller import load


def job(request, old, candidate, inputs_sha256):
    router_transaction.validate(request)
    router_generations.pair(old, candidate, request)
    if not router_generations.matches('[0-9a-f]{64}', inputs_sha256):
        raise ValueError('router copy capsule needs its exact authenticated package input identity')
    common = {'kind': 'klokast.router-copy-request.v1', 'role': 'router', 'box': request['box'],
              'operation': request['operation_id'],
              'seconds': min(120, request['cutover_seconds'] - 30, request['recovery_seconds'] - 30)}
    jobs = {}
    for name, source, target in (('forward', old, candidate), ('reverse', candidate, old)):
        value = {**common, 'source_id': source['disk']['uuid'], 'destination_id': target['disk']['uuid'],
                 'source_accounts': source['accounts'], 'destination_accounts': target['accounts']}
        jobs[name] = {**value, 'request_sha256': router_generations.digest(value)}
    return {'kind': 'klokast.router-copy-job.v1', 'operation_id': request['operation_id'],
            'inputs_sha256': inputs_sha256, **jobs}


def stage(source, work, repo, request, old, candidate):
    source, work, repo = Path(source), Path(work), Path(repo)
    manifest = load(source / 'inputs.json')
    profile = load(repo / 'ansible/update-profiles/router-alpine-v1.json')
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
    return {'kind': 'klokast.router-copy-capsule.v1', 'operation_id': request['operation_id'],
            'inputs_sha256': manifest['inputs_sha256'], 'engine_commit': request['engine_commit'],
            'transaction_sha256': router_generations.digest(request), 'job_sha256': router_generations.digest(value),
            'bootstrap': boot}
