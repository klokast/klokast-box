"""Verify the same qualified router template and networkless boot inputs for both lifecycle modes."""
from pathlib import Path

import router_candidate_disk as disks
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
from xen_build_runtime import checksum, safe_directory, safe_file

TEMPLATES = Path('/mnt/dom0_data/klokast-router-templates')


def template(value, release):
    work = TEMPLATES / value['template_operation']
    records.parents(work)
    safe_directory(work)
    candidate_path = work / 'candidate.json'
    candidate = records.read(candidate_path)
    if (checksum(candidate_path) != value['template_sha256'] or
            candidate.get('kind') != 'klokast.router-template-candidate.v1' or
            candidate.get('box') != value['box'] or candidate.get('role') != 'router' or
            candidate.get('operation_id') != value['template_operation'] or
            candidate.get('inputs_sha256') != value['inputs_sha256'] or
            candidate.get('kernel_release') != release['kernel_release'] or
            candidate.get('generic_tests') != release['generic_tests'] or
            candidate.get('replacement_authorized') is not False or
            {key:item['sha256'] for key,item in candidate.get('artifacts', {}).items()} != release['artifacts']):
        raise TransactionError('router preparation template differs from its approved release')
    expected = candidate['artifacts']['os']
    return disks.template_source(work / 'os.slot', expected), expected


def bootstrap(work, value):
    if not isinstance(value['bootstrap'], dict) or set(value['bootstrap']) != {'kernel', 'initramfs'}:
        raise TransactionError('router preparation lacks exact networkless boot artifacts')
    for name, maximum in (('kernel', 32 * 1024 * 1024), ('initramfs', 1024 * 1024 * 1024)):
        item = value['bootstrap'][name]
        if (not isinstance(item, dict) or set(item) != {'bytes', 'sha256'} or
                type(item['bytes']) is not int or not 0 < item['bytes'] <= maximum or
                not generations.matches('[0-9a-f]{64}', item['sha256'])):
            raise TransactionError('router preparation boot identity is invalid')
        path = work / ('bootstrap-' + name)
        safe_file(path, maximum)
        if path.stat().st_size != item['bytes'] or checksum(path) != item['sha256']:
            raise TransactionError('router preparation boot bytes changed')
