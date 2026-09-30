"""Copy exact router template boot bytes to an operation-owned generation path."""
import hashlib
from pathlib import Path

import router_generations as generations
import router_records as records
from router_transaction import TransactionError

TEMPLATES = Path('/mnt/dom0_data/klokast-router-templates')
MAXIMUM = {'kernel':32 * 1024 * 1024, 'initramfs':128 * 1024 * 1024}


def artifact(source, target, expected, maximum):
    """Copy once; a retry accepts only the same owner and bytes."""
    records.parents(source)
    records.secure(source, maximum=maximum)
    size = source.stat().st_size
    if not 0 < size <= maximum or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise TransactionError('router template boot artifact differs from its release')
    if target.exists() or target.is_symlink():
        records.parents(target)
        records.secure(target, maximum=maximum)
        if target.stat().st_size != size or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise TransactionError('router versioned boot artifact changed; retain the disk')
    else:
        records.atomic(target, source.read_bytes())
    return {'path':str(target), 'sha256':expected, 'bytes':size}


def boot_files(storage, source, release, operation):
    template = TEMPLATES / source['template_operation']
    records.parents(template)
    records.secure(template, directory=True)
    directory = storage.base / 'generations' / operation
    if not directory.exists() and not directory.is_symlink():
        directory.mkdir(mode=0o700)
        records.syncdir(directory.parent)
    records.secure(directory, directory=True)
    result = {}
    for name in ('kernel','initramfs'):
        expected = release['artifacts'].get(name)
        if not generations.matches('[0-9a-f]{64}', expected):
            raise TransactionError('router release lacks exact boot artifact hashes')
        result[name] = artifact(template / name, directory / name, expected, MAXIMUM[name])
    return result
