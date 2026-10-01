"""Freeze one authenticated networkless filesystem-inspection boot capsule."""
import hashlib
import json
from pathlib import Path

import router_generations as generations
import router_updates
import vm_template_inputs
from platform_updates import UpdateError


def prepare(source, work, repo, *, box, operation, engine):
    source, work, repo = Path(source), Path(work), Path(repo)
    if (box != 'k001' or not generations.matches('[0-9a-f]{24}', operation) or
            not generations.matches('[0-9a-f]{40}', engine) or
            work.is_symlink() or not work.is_dir() or any(work.iterdir())):
        raise UpdateError('K001 cold filesystem bootstrap requires one new private operation directory')
    profile = json.loads((repo / 'ansible/update-profiles/router-alpine-v2.json').read_text())
    manifest = json.loads((source / 'inputs.json').read_text())
    router_updates.validate_inputs(manifest, profile, engine)
    guest = repo / 'ansible/roles/router-cold-filesystem/files/router-cold-filesystem-guest'
    if guest.is_symlink() or not guest.is_file() or not 0 < guest.stat().st_size <= 1024 * 1024:
        raise UpdateError('cold filesystem guest source is absent or unsafe')
    guest_sha256 = hashlib.sha256(guest.read_bytes()).hexdigest()
    boot = vm_template_inputs.bootstrap(source, work / 'boot', guest,
        expected_profile=router_updates.PROFILE)
    if hashlib.sha256(guest.read_bytes()).hexdigest() != guest_sha256:
        raise UpdateError('cold filesystem guest source changed during bootstrap assembly')
    value = generations.seal({'kind': 'klokast.router-cold-filesystem-bootstrap.v1',
        'box': box, 'operation_id': operation, 'engine_commit': engine,
        'inputs_sha256': manifest['inputs_sha256'], 'boot': boot,
        'guest_sha256': guest_sha256})
    target = work / 'filesystem-bootstrap.json'
    with target.open('x') as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')
        stream.flush()
    return value
