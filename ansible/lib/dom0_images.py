"""Prepare public diskless bundles on the owning controller; never activate."""
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import tarfile

import platform_source
import vm_local_images
import vm_template_inputs as inputs
from platform_update_metadata import fetch_json, newest_stable_branch
from platform_update_runtime import CACHE, REPO, STATE, command, load, now, write
from platform_updates import UpdateError, timestamp

PROFILE = 'dom0-alpine-v1'


def profile(repo=REPO):
    import yaml
    policy = yaml.safe_load((repo / 'ansible/inventory-policy/group_vars/dom0.yml').read_text())
    world = sorted(policy['dom0_world_packages'])
    # The wired bare-metal hosts need the driver modules, but the complete
    # firmware meta-package is too large for their EFI partitions. Firmware
    # for a concrete device must be added explicitly when hardware needs it.
    tools = set(inputs.BUILDER) | {'alpine-conf', 'linux-lts', 'linux-firmware-none', 'squashfs-tools', 'kmod'}
    return {'kind': 'klokast.vm-template-profile.v1', 'profile': PROFILE,
            'architecture': 'x86_64', 'packages': sorted(set(world) | tools), 'dom0_world': world,
            'repositories': ['main', 'community'], 'repository_origin': inputs.ORIGIN,
            'release_metadata': 'https://alpinelinux.org/releases.json'}


def capsule(directory, output, manifest):
    inputs.verify_inputs(directory, manifest, expected_profile=PROFILE)
    with tarfile.open(output, 'x', format=tarfile.USTAR_FORMAT) as archive:
        for relative in ['inputs.json', *['keys/' + k for k in manifest['keys']],
                         *[p['file'] for p in manifest['packages']]]:
            archive.add(directory / relative, arcname=relative, recursive=False)
        for name, expected in manifest['indexes'].items():
            path = directory / 'resolver/var/cache/apk' / name
            if not inputs.regular(path, 64 * 1024**2) or inputs.sha256(path) != expected:
                raise UpdateError('signed dom0 repository index changed before assembly')
            archive.add(path, arcname='cache/' + name, recursive=False)
    if output.stat().st_size > 2049 * 1024**2:
        raise UpdateError('dom0 input capsule exceeds its bound')
    return {'bytes': output.stat().st_size, 'sha256': inputs.sha256(output)}


def prepare(box, inputs_only=False, test_app=None):
    platform_source.require_local_image(box)
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', box) or test_app:
        raise UpdateError('dom0 preparation needs one box and does not accept application tests')
    if command(['git', '-C', REPO, 'status', '--porcelain']).strip():
        raise UpdateError('dom0 preparation needs a clean public checkout')
    revision = command(['git', '-C', REPO, 'rev-parse', 'HEAD']).strip()
    os.umask(0o077)
    with (STATE / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        operation = secrets.token_hex(12)
        work = CACHE / ('dom0-build-' + operation)
        result = STATE / 'dom0-builds' / operation
        work.mkdir(mode=0o700)
        result.mkdir(mode=0o700, parents=True)
        inventory = vm_local_images.inventory(REPO, result / 'inventory', box,
                                              json.loads(command(['tailscale', 'status', '--json'])))
        selected = profile()
        releases, metadata_sha256 = fetch_json(selected['release_metadata'])
        observed = now()
        branch = newest_stable_branch(releases, observed, 0)
        write(result / 'selection.json', {'box': box, 'operation_id': operation, 'branch': branch,
              'observed_at': timestamp(observed), 'metadata_sha256': metadata_sha256, 'engine_commit': revision})
        frozen = inputs.freeze_local(work / 'inputs', selected, branch, revision,
                                     expected_profile=PROFILE, apk_network_timeout=10, cache_root=CACHE)
        write(result / 'inputs.json', frozen)
        summary = {'box': box, 'operation_id': operation, 'profile': PROFILE,
                   'branch': branch, 'result_directory': str(result), 'state': 'inputs-prepared',
                   'hardware_qualified': False, 'activation_performed': False}
        if inputs_only:
            write(result / 'build-result.json', summary)
            return summary
        job = REPO / 'ansible/roles/dom0-image-builder/files/dom0-build-guest'
        original = REPO / 'ansible/roles/vm-template-builder/files/vm-template-build-guest'
        payload = capsule(work / 'inputs', work / 'capsule.tar', frozen)
        boot = inputs.bootstrap(work / 'inputs', work / 'boot', job, expected_profile=PROFILE,
                                tools=inputs.BUILDER, job_files={'vm_template_guest.py': original})
        request = {'kind': 'klokast.dom0-build-request.v1', 'box': box, 'operation_id': operation,
                   'inputs_sha256': frozen['inputs_sha256'], 'capsule': payload, 'bootstrap': boot}
        write(work / 'request.json', request)
        write(result / 'request.json', request)
        args = result / 'args.json'
        write(args, {'dom0_build_box': box, 'dom0_build_operation': operation,
                     'dom0_build_inputs': str(work), 'dom0_build_results': str(result)})
        with (result / 'build.log').open('w') as log:
            command(['ansible-playbook', '-vv', '-i', inventory,
                     REPO / 'ansible/playbooks/74-dom0-image-build.yml', '-e', '@' + str(args)],
                    timeout=4200, log=log)
        built = load(result / 'native-result.json')
        if (built.get('success') is not True or built.get('operation_id') != operation or
                built.get('inputs_sha256') != frozen['inputs_sha256']):
            raise UpdateError('dom0 builder returned another operation or failed')
        summary.update(state='bundle-built', bundle=built['bundle'], kernel_release=built['kernel_release'],
                       packages=built['packages'],
                       artifact_path='/mnt/dom0_data/klokast-dom0-builds/' + operation + '/bundle.tar')
        write(result / 'build-result.json', summary)
        return summary
