"""Durable first-router installation stages; never infer a disk or enrollment."""
import router_generations as generations
from router_transaction import TransactionError

STAGES = ('planned', 'allocated', 'prepared', 'enrolled', 'verified')
FIELDS = frozenset({'kind', 'box', 'role', 'operation_id', 'engine_commit',
                    'selection_sha256', 'release_sha256', 'disk', 'stage', 'preparation_sha256',
                    'enrollment_sha256', 'machine_id', 'generation_sha256', 'record_sha256'})


def provision_pointer(value, box, engine):
    """Select one common template and operation before a first-install window."""
    generations.check_seal(value)
    if (set(value) != {'kind', 'box', 'engine_commit', 'operation_id',
            'source_operation', 'template_operation', 'selection_sha256',
            'release_sha256', 'record_sha256'} or
            value['kind'] != 'klokast.router-initial-provision-pointer.v1' or
            value['box'] != box or value['engine_commit'] != engine or
            any(not generations.matches('[0-9a-f]{24}', value[key]) for key in (
                'operation_id', 'source_operation', 'template_operation')) or
            any(not generations.matches('[0-9a-f]{64}', value[key]) for key in (
                'selection_sha256', 'release_sha256'))):
        raise TransactionError('first router provisioning pointer is invalid or selects another engine')
    return value


def validate(value, box):
    if not isinstance(value, dict):
        raise TransactionError('router initial installation record is absent')
    generations.check_seal(value)
    if (not isinstance(value, dict) or set(value) != FIELDS or
            value['kind'] != 'klokast.router-initial-installation.v1' or
            value['box'] != box or value['role'] != 'router' or
            not generations.matches('[0-9a-f]{24}', value['operation_id']) or
            not generations.matches('[0-9a-f]{40}', value['engine_commit']) or
            not generations.matches('[0-9a-f]{64}', value['selection_sha256']) or
            not generations.matches('[0-9a-f]{64}', value['release_sha256']) or
            value['stage'] not in STAGES):
        raise TransactionError('router initial installation has an invalid target or source')
    disk = value['disk']
    if (not isinstance(disk, dict) or set(disk) != {'path', 'uuid', 'bytes'} or
            disk['path'] != '/dev/vg0/routergen_' + value['operation_id'] or
            (disk['uuid'] is not None if value['stage'] == 'planned' else
             not generations.matches('[A-Za-z0-9-]{1,64}', disk['uuid'])) or
            disk['bytes'] != 2147483648):
        raise TransactionError('router initial installation disk identity is invalid')
    hashes = [value[name] for name in ('preparation_sha256', 'enrollment_sha256', 'generation_sha256')]
    count = max(0, STAGES.index(value['stage']) - 1)
    if any(not generations.matches('[0-9a-f]{64}', item) for item in hashes[:count]) or any(
            item is not None for item in hashes[count:]):
        raise TransactionError('router initial installation stage lacks exact evidence')
    if ((count < 2 and value['machine_id'] is not None) or
            (count >= 2 and not generations.matches('[A-Za-z0-9_-]{1,128}', value['machine_id']))):
        raise TransactionError('router initial installation has no exact enrolled machine identity')
    return value


def advance(current, next_value, box):
    """Permit exact retry or one forward step; never replace a minted identity."""
    validate(current, box)
    validate(next_value, box)
    if current == next_value:
        return next_value
    before, after = STAGES.index(current['stage']), STAGES.index(next_value['stage'])
    disk_matches = current['disk'] == next_value['disk']
    if current['stage'] == 'planned' and next_value['stage'] == 'allocated':
        disk_matches = all(current['disk'][key] == next_value['disk'][key]
                           for key in ('path', 'bytes'))
    completed = max(0, before - 1)
    if after != before + 1 or any(current[name] != next_value[name] for name in (
            'box', 'role', 'operation_id', 'engine_commit', 'selection_sha256',
            'release_sha256')) or not disk_matches or any(
            current[name] != next_value[name] for name in
            ('preparation_sha256', 'enrollment_sha256', 'generation_sha256')[:completed]) or (
            completed >= 2 and current['machine_id'] != next_value['machine_id']):
        raise TransactionError('router initial installation cannot skip, rewind, or change recorded identity')
    return next_value


def matches_generation(installation, generation, box):
    validate(installation, box)
    generations.generation(generation, box)
    if (installation['stage'] != 'verified' or generation['origin'] != 'template' or
            generation['generation_id'] != installation['operation_id'] or
            generation['engine_commit'] != installation['engine_commit'] or
            generation['release_sha256'] != installation['release_sha256'] or
            generation['disk'] != installation['disk'] or
            generation['record_sha256'] != installation['generation_sha256']):
        raise TransactionError('router initial generation differs from its verified installation')
    return True
