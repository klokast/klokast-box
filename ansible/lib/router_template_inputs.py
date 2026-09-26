"""Controller-side router-only build staging. Outputs are qualification evidence."""
from pathlib import Path
import tarfile

import router_updates
import vm_template_inputs
from platform_updates import UpdateError


def stage(source, work, profile, engine, guest):
    import router_update_controller as transport
    source, work, guest = Path(source), Path(work), Path(guest)
    manifest = transport.load(source / 'inputs.json')
    router_updates.validate_inputs(manifest, profile, engine)
    vm_template_inputs.verify_inputs(source, manifest, expected_profile=router_updates.PROFILE)
    if not work.is_dir() or work.is_symlink() or any(work.iterdir()):
        raise UpdateError('router build staging requires a new empty directory')
    capsule = work / 'capsule.tar'
    with tarfile.open(capsule, 'x', format=tarfile.USTAR_FORMAT) as archive:
        for relative in ['inputs.json', *['keys/' + name for name in sorted(manifest['keys'])],
                         *[p['file'] for p in manifest['packages']]]:
            archive.add(source / relative, arcname=relative, recursive=False)
        archive.add(guest, arcname='guest.py', recursive=False)
    # Native APK extraction is scriptless and unprivileged on the controller.
    # The template's package scripts and filesystem tools run only inside Xen.
    boot = vm_template_inputs.bootstrap(source, work / 'boot', guest, expected_profile=router_updates.PROFILE)
    vm_template_inputs.verify_inputs(source, manifest, expected_profile=router_updates.PROFILE)
    return manifest, {'sha256': vm_template_inputs.sha256(capsule), 'bytes': capsule.stat().st_size}, boot


def release(candidate, manifest, profile, engine, box, operation):
    if (not isinstance(candidate, dict) or
            candidate.get('kind') != 'klokast.router-template-candidate.v1' or
            candidate.get('box') != box or candidate.get('role') != 'router' or
            candidate.get('operation_id') != operation or
            candidate.get('inputs_sha256') != manifest['inputs_sha256'] or
            candidate.get('replacement_authorized') is not False):
        raise UpdateError('router template result belongs to another operation or target')
    value = router_updates.seal({'kind': router_updates.RELEASE, 'profile': router_updates.PROFILE,
        'engine_commit': engine, 'inputs': manifest, 'kernel_release': candidate['kernel_release'],
        'artifacts': {k: v['sha256'] for k, v in candidate['artifacts'].items()},
        'generic_tests': candidate['generic_tests']})
    router_updates.validate_release(value, profile, engine)
    return value
