"""Active-peer coordination of diskless updates, with explicit recovery."""
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

import air_maintenance
import infrastructure_images
import ops_replacement_inputs
import platform_resource_runtime
import platform_source
from platform_update_runtime import REPO, command, load, write
from platform_updates import UpdateError

STATE = Path('/var/lib/klokast/updates/discovery/dom0-operations')


def authority(box):
    view = platform_source.snapshot()
    if box not in view['instance']['boxes'] or view['controllers'].get('standby') != box:
        raise RuntimeError('dom0 update requires the declared standby controller box; move authority off the target first')
    return view


def step(box, operation, action, directory, image=None, sha256=None):
    authority(box)
    result_file = directory / (action + '-result.json')
    arguments = directory / (action + '-args.json')
    write(arguments, {'dom0_update_box': box, 'dom0_update_operation': operation,
          'dom0_update_action': action, 'dom0_update_image': image or '', 'dom0_update_sha256': sha256 or '',
          'dom0_update_result_file': str(result_file)})
    with (directory / (action + '.log')).open('a') as log:
        command(['ansible-playbook', '-vv', '-i', REPO / 'ansible/execution-inventory/hosts',
                 REPO / 'ansible/playbooks/74-dom0-update.yml', '--limit', box + '-dom0',
                 '-e', '@' + str(arguments)], timeout=300, log=log)
    return {'state': 'started'} if action == 'reboot' else load(result_file)


def selected_image(box, image):
    # Public build evidence stays on its owning controller. Only a small
    # public selection receipt crosses this link; the bundle remains on dom0.
    script = '''set -eu
cd /home/smith/src/klokast/klokast-box
python3 - "$1" "$2" <<'PY'
import json,re,sys
from pathlib import Path
box,image=sys.argv[1:]
if not re.fullmatch('[0-9a-f]{24}',image):raise SystemExit('invalid dom0 image ID')
root=Path('/var/lib/klokast/updates/discovery/dom0-builds')/image
value=json.loads((root/'build-result.json').read_text())
if value.get('box')!=box or value.get('state')!='bundle-built':raise SystemExit('dom0 build is incomplete or belongs to another box')
print(json.dumps(value))
PY
'''
    result = subprocess.run(['tailscale', 'ssh', 'smith@' + box + '-ops', 'sh', '-s', '--', box, image],
                            input=script, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('cannot read the selected same-box dom0 build: ' + result.stderr[-500:])
    return json.loads(result.stdout)


def health(box, directory, label):
    with (directory / (label + '-health.log')).open('a') as log:
        command([REPO / 'ansible/bin/platform-check', '--box', box, '--target', 'dom0'], timeout=360, log=log)


def execute(box, *, image=None, resume=None, dry_run=False, abort=False, status=False, lock_held=False, verify_reboot=False):
    view = authority(box)
    for value in (image, resume):
        if value and not re.fullmatch('[0-9a-f]{24}', value):
            raise RuntimeError('dom0 image and operation IDs must have 24 hexadecimal characters')
    if dry_run:
        return {'state': 'planned', 'box': box, 'role': 'dom0', 'image': image,
                'reboot': True, 'coordinator': view['controllers']['active'], 'one_box_per_run': True}
    os.umask(0o077)
    with contextlib.nullcontext() if lock_held else platform_resource_runtime.vm_update_installation_lock():
        operation = resume or secrets.token_hex(12)
        directory = STATE / operation
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = step(box, operation, 'status', directory)
        if status:
            return state
        pending = state.get('pending')
        if pending and pending['operation'] != operation:
            raise RuntimeError('unfinished dom0 update; resume with: ansible/bin/platform-update update --box ' + box +
                               ' --role dom0 --resume ' + pending['operation'])
        if resume and not pending:
            if (state.get('accepted') or {}).get('operation') == resume:
                if box + '-air' in view['instance'].get('airunners', []):
                    air_maintenance.release(box, operation)
                return {'state': 'complete', 'box': box, 'role': 'dom0', 'operation': resume, 'replaced': True}
            raise RuntimeError('no matching pending dom0 update exists')
        has_air = box + '-air' in view['instance'].get('airunners', [])
        if abort:
            result = step(box, operation, 'abort', directory)
            if has_air: air_maintenance.release(box, operation)
            return result
        if not pending and verify_reboot:
            health(box, directory, 'before-reboot-test')
            pending = step(box, operation, 'test-reboot', directory)
        if not pending:
            if image is None and not state.get('accepted'):
                return {'state': 'skipped', 'role': 'dom0', 'box': box,
                        'reason': 'first hardware qualification requires an explicit --image'}
            if has_air and not air_maintenance.remote(box, 'image-idle')['idle']:
                return {'state': 'skipped', 'box': box, 'role': 'dom0', 'reason': 'runner busy'}
            built = selected_image(box, image) if image else infrastructure_images.local_action(
                box, 'dom0-alpine-v1', ops_replacement_inputs.revision(REPO, True), 'prepare')
            image = built['operation_id']
            health(box, directory, 'before')
            pending = step(box, operation, 'prepare', directory, image, built['bundle']['sha256'])
            if pending['state'] == 'unchanged':
                return dict(pending, role='dom0', replaced=False)
        # Reserve launcher access and checkpoint the runner before any shutdown.
        # Resume after reboot uses the saved reservation rather than requiring idle.
        if has_air and pending['state'] in ('prepared', 'selecting', 'selected'):
            air_maintenance.before_stop(box, operation, image=True)
        pending = step(box, operation, 'select', directory)
        if pending['state'] in ('selected', 'rebooting'):
            step(box, operation, 'reboot', directory)
        deadline = time.monotonic() + 1200
        error = None
        while time.monotonic() < deadline:
            try:
                accepted = step(box, operation, 'verify', directory)
                break
            except (UpdateError, RuntimeError, OSError, subprocess.SubprocessError) as failure:
                error = failure
                time.sleep(10)
        else:
            raise RuntimeError('dom0 reboot verification stopped; resume with: ansible/bin/platform-update update --box ' + box +
                               ' --role dom0 --resume ' + operation + '; last verification error: ' + str(error))
        health(box, directory, 'after')
        accepted = step(box, operation, 'accept', directory)
        if has_air: air_maintenance.release(box, operation)
        return {'state': 'complete', 'role': 'dom0', 'box': box, 'operation': operation,
                'image': accepted['image'], 'replaced': not accepted.get('reboot_test', False),
                'reboot_verified': True}
