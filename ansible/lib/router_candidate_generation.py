"""Build a proposed router generation from exact, separately qualified evidence.

This controller-side assembler writes no record and grants no replacement
authority. The dom0 issuer must still verify the source files, LV identity,
current assignment, signed policy, and native readiness before publication.
"""
import router_generations as generations
import router_updates
import copy
from router_transaction import TransactionError


def assemble(*, box, operation, template_operation, old, release, profile, prepared, disk_record, boot,
             xen_uuid, approved_engine):
    router_updates.validate_release(release, profile, approved_engine)
    generations.generation(old, box)
    expected_files = {'kind', 'box', 'role', 'mode', 'operation_id', 'inputs_sha256',
                      'engine_commit', 'packages', 'accounts', 'configuration_files',
                      'tailscale', 'identity_absent', 'replacement_authorized', 'service_syntax'}
    component = {key:release['inputs']['tailscale'][key] for key in (
        'version', 'sha256', 'tailscale_sha256', 'tailscaled_sha256', 'openrc_sha256')}
    if (not generations.matches('[0-9a-f]{24}', operation) or
            not generations.matches('[0-9a-f]{24}', template_operation) or
            not isinstance(prepared, dict) or set(prepared) != expected_files or
            prepared['kind'] != 'klokast.router-candidate-files.v1' or
            (prepared['box'], prepared['role'], prepared['mode'], prepared['operation_id']) !=
            (box, 'router', 'replacement', operation) or
            prepared['engine_commit'] != approved_engine or
            prepared['inputs_sha256'] != release['inputs']['inputs_sha256'] or
            prepared['packages'] != release['runtime_packages'] or
            prepared['tailscale'] != component or
            prepared['identity_absent'] is not True or
            prepared['replacement_authorized'] is not False or
            prepared['service_syntax'] is not True):
        raise TransactionError('router generation lacks exact replacement preparation evidence')
    if (not isinstance(disk_record, dict) or
            set(disk_record) != {'kind', 'operation_id', 'path', 'tag', 'uuid', 'stage', 'template_sha256'} or
            disk_record['kind'] != 'klokast.router-candidate-disk.v1' or
            disk_record['operation_id'] != operation or
            disk_record['stage'] != 'cloned' or
            disk_record['path'] != '/dev/vg0/routergen_' + operation or
            disk_record['tag'] != 'routergen_' + operation or
            disk_record['template_sha256'] != release['artifacts']['os'] or
            not generations.matches('[A-Za-z0-9-]{1,64}', disk_record['uuid'])):
        raise TransactionError('router generation lacks the exact qualified candidate LV')
    directory = '/mnt/dom0_data/klokast-router-updates/generations/' + operation
    if not isinstance(boot, dict) or set(boot) != {'kernel', 'initramfs'}:
        raise TransactionError('router generation lacks versioned boot artifacts')
    for name in ('kernel', 'initramfs'):
        item = boot[name]
        if (not isinstance(item, dict) or set(item) != {'path', 'sha256', 'bytes'} or
                item['path'] != directory + '/' + name or
                item['sha256'] != release['artifacts'][name]):
            raise TransactionError('router generation boot artifact differs from its approved release')
    proposed = generations.seal({
        'kind':'klokast.router-generation.v1', 'box':box, 'role':'router',
        'generation_id':operation, 'origin':'template', 'engine_commit':approved_engine,
        'template_operation':template_operation,'release_sha256':release['receipt_sha256'],
        'alpine_branch':release['inputs']['branch'],
        'disk':{'path':disk_record['path'], 'uuid':disk_record['uuid'], 'bytes':2147483648},
        'boot':boot,
        'xen':{'uuid':xen_uuid, 'memory':old['xen']['memory'], 'vcpus':old['xen']['vcpus'],
               'vif':copy.deepcopy(old['xen']['vif'])},
        'packages':copy.deepcopy(prepared['packages']), 'kernel_release':release['kernel_release'],
        'tailscale':component,
        'accounts':copy.deepcopy(prepared['accounts']),
        'configuration_files':copy.deepcopy(prepared['configuration_files']),
        'evidence_sha256':generations.digest({'release':release['receipt_sha256'],
            'prepared':prepared, 'disk':disk_record})})
    generations.generation(proposed, box)
    generations.pair(old, proposed, {'box':box, 'engine_commit':approved_engine,
        'old_sha256':old['record_sha256'], 'candidate_sha256':proposed['record_sha256']})
    return proposed


def assemble_initial(*, box, operation, template_operation, release, profile, prepared, finalized,
                     disk_record, boot, xen, selection_sha256, enrollment_sha256,
                     approved_engine):
    """Bind one verified first enrollment to the same qualified template recipe."""
    router_updates.validate_release(release, profile, approved_engine)
    component = {key:release['inputs']['tailscale'][key] for key in (
        'version', 'sha256', 'tailscale_sha256', 'tailscaled_sha256', 'openrc_sha256')}
    before = {item['name']:item['version'] for item in release['inputs']['packages']}
    expected_prepared = {'kind', 'box', 'role', 'mode', 'operation_id', 'inputs_sha256',
                         'engine_commit', 'packages', 'accounts', 'tailscale',
                         'configuration_files', 'identity_absent', 'replacement_authorized',
                         'service_syntax'}
    if (not generations.matches('[0-9a-f]{24}', operation) or
            not generations.matches('[0-9a-f]{24}', template_operation) or
            not generations.matches('[0-9a-f]{64}', selection_sha256) or
            not generations.matches('[0-9a-f]{64}', enrollment_sha256) or
            not isinstance(prepared, dict) or set(prepared) != expected_prepared or
            prepared['kind'] != 'klokast.router-candidate-files.v1' or
            (prepared['box'], prepared['role'], prepared['mode'], prepared['operation_id']) !=
            (box, 'router', 'initial-install', operation) or
            prepared['engine_commit'] != approved_engine or
            prepared['inputs_sha256'] != release['inputs']['inputs_sha256'] or
            prepared['packages'] != before or prepared['tailscale'] != component or
            prepared['identity_absent'] is not True or
            prepared['replacement_authorized'] is not False or
            prepared['service_syntax'] is not True):
        raise TransactionError('initial router generation lacks exact common-template preparation evidence')
    if (not isinstance(finalized, dict) or set(finalized) != {
            'kind', 'packages', 'removed_packages', 'tests', 'enrolled_state_preserved'} or
            finalized['kind'] != 'klokast.router-finalization.v1' or
            finalized['packages'] != release['runtime_packages'] or
            finalized['removed_packages'] != sorted(before.keys() - finalized['packages'].keys()) or
            finalized['tests'] != release['runtime_tests'] or
            finalized['enrolled_state_preserved'] is not True):
        raise TransactionError('initial router generation lacks enrolled offline finalization evidence')
    if (not isinstance(disk_record, dict) or
            set(disk_record) != {'kind', 'operation_id', 'path', 'tag', 'uuid', 'stage', 'template_sha256'} or
            disk_record['kind'] != 'klokast.router-candidate-disk.v1' or
            disk_record['operation_id'] != operation or disk_record['stage'] != 'cloned' or
            disk_record['path'] != '/dev/vg0/routergen_' + operation or
            disk_record['tag'] != 'routergen_' + operation or
            disk_record['template_sha256'] != release['artifacts']['os']):
        raise TransactionError('initial router generation lacks the exact qualified template clone')
    directory = '/mnt/dom0_data/klokast-router-updates/generations/' + operation
    if (not isinstance(boot, dict) or set(boot) != {'kernel', 'initramfs'} or any(
            not isinstance(boot[name], dict) or set(boot[name]) != {'path', 'sha256', 'bytes'} or
            boot[name]['path'] != directory + '/' + name or
            boot[name]['sha256'] != release['artifacts'][name]
            for name in ('kernel', 'initramfs'))):
        raise TransactionError('initial router generation boot artifacts differ from the approved release')
    proposed = generations.seal({
        'kind':'klokast.router-generation.v1', 'box':box, 'role':'router',
        'generation_id':operation, 'origin':'template', 'engine_commit':approved_engine,
        'template_operation':template_operation,'release_sha256':release['receipt_sha256'],
        'alpine_branch':release['inputs']['branch'],
        'disk':{'path':disk_record['path'], 'uuid':disk_record['uuid'], 'bytes':2147483648},
        'boot':copy.deepcopy(boot), 'xen':copy.deepcopy(xen),
        'packages':copy.deepcopy(finalized['packages']), 'kernel_release':release['kernel_release'],
        'tailscale':component, 'accounts':copy.deepcopy(prepared['accounts']),
        'configuration_files':copy.deepcopy(prepared['configuration_files']),
        'evidence_sha256':generations.digest({'release':release['receipt_sha256'],
            'selection':selection_sha256, 'prepared':prepared,
            'finalized':finalized, 'enrollment':enrollment_sha256,
            'disk':disk_record, 'xen':xen})})
    return generations.generation(proposed, box)
