"""Copy one checked, identity-free VM template to another dom0.

This module stages build artifacts only. It cannot select a guest disk, create
an accepted release, or start a VM. The caller supplies the exact build receipt
from its clean controller checkout and holds the controller build lock.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from platform_updates import UpdateError


OPERATION = re.compile(r'[0-9a-f]{24}')
BOX = re.compile(r'[a-z0-9][a-z0-9-]{0,30}')
HASH = re.compile(r'[0-9a-f]{64}')
BASE = '/mnt/dom0_data/klokast-vm-templates/candidates'
LIMITS = {'root': 4 * 1024 ** 3, 'kernel': 32 * 1024 ** 2,
          'initramfs': 128 * 1024 ** 2}


def validate(candidate, operation, source_box):
    if (not OPERATION.fullmatch(operation) or not BOX.fullmatch(source_box) or
            not isinstance(candidate, dict) or
            candidate.get('kind') != 'klokast.vm-template-candidate.v1' or
            candidate.get('operation_id') != operation or
            candidate.get('box') != source_box or
            candidate.get('success') is not True or
            candidate.get('accepted') is not False or
            not isinstance(candidate.get('artifacts'), dict) or
            set(candidate['artifacts']) != set(LIMITS)):
        raise UpdateError('template transfer needs one successful, unaccepted exact build')
    for name, limit in LIMITS.items():
        item = candidate['artifacts'][name]
        if (not isinstance(item, dict) or set(item) != {'sha256', 'bytes'} or
                not isinstance(item['sha256'], str) or not HASH.fullmatch(item['sha256']) or
                type(item['bytes']) is not int or not 0 < item['bytes'] <= limit):
            raise UpdateError('template transfer has an invalid ' + name + ' identity')


def checked_command(argv, *, input_bytes=None, timeout=60):
    try:
        result = subprocess.run(argv, input=input_bytes, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError('template transfer command failed or timed out') from error
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise UpdateError('template transfer command failed: ' + str(argv[0]))
    return result.stdout


def remote(host, *command, input_bytes=None, timeout=60):
    return checked_command(['tailscale', 'ssh', 'neo@' + host + '-dom0', *command],
                           input_bytes=input_bytes, timeout=timeout)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def source_file(host, source, target, expected):
    try:
        with Path(target).open('xb') as output:
            result = subprocess.run(['tailscale', 'ssh', 'neo@' + host + '-dom0',
                                     'doas', 'cat', source], stdout=output,
                                    stderr=subprocess.PIPE, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError('template source transfer failed or timed out') from error
    if (result.returncode or Path(target).stat().st_size != expected['bytes'] or
            sha256(target) != expected['sha256']):
        raise UpdateError('template source bytes differ from the build receipt')


def target_file(host, source, target, expected):
    try:
        with Path(source).open('rb') as stream:
            result = subprocess.run(['tailscale', 'ssh', 'neo@' + host + '-dom0',
                                     'doas', 'dd', 'of=' + target, 'bs=1048576',
                                     'conv=fsync'], stdin=stream, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError('template target transfer failed or timed out') from error
    if result.returncode:
        raise UpdateError('template target transfer failed; retain exact staging for review')
    remote(host, 'doas', 'chmod', '0400', target)
    details = remote(host, 'doas', 'sha256sum', target).decode().split()
    size = remote(host, 'doas', 'stat', '-c', '%s', target).decode().strip()
    if (len(details) != 2 or details[0] != expected['sha256'] or details[1] != target or
            size != str(expected['bytes'])):
        raise UpdateError('template target bytes differ; retain exact staging for review')


def transfer(candidate, operation, source_box, target_box, controller_candidate,
             cache_directory):
    """Transfer checked bytes; a failed attempt leaves target staging visible."""
    validate(candidate, operation, source_box)
    if not BOX.fullmatch(target_box) or target_box == source_box:
        raise UpdateError('template transfer target is invalid or equals the builder')
    if not Path(controller_candidate).is_file() or Path(controller_candidate).is_symlink():
        raise UpdateError('controller build receipt is unavailable')
    raw = Path(controller_candidate).read_bytes()
    if len(raw) > 1024 * 1024 or json.loads(raw) != candidate:
        raise UpdateError('controller build receipt differs from selected template')
    source = BASE + '/' + operation
    target = BASE + '/' + operation
    remote_manifest = remote(source_box, 'doas', 'cat', source + '/candidate.json')
    if remote_manifest != raw:
        raise UpdateError('builder candidate manifest differs from the controller receipt')
    with tempfile.TemporaryDirectory(prefix='vm-template-transfer-', dir=cache_directory) as temporary:
        temporary = Path(temporary)
        stage = BASE + '/.transfer-' + operation
        remote(target_box, 'doas', 'sh', '-s', '--', operation,
               input_bytes=(
                   'set -eu\n'
                   'mountpoint -q /mnt/dom0_data\n'
                   f'test ! -e {target}\n'
                   f'test ! -L {target}\n'
                   f'mkdir -m 0700 {stage}\n'
               ).encode())
        for name in LIMITS:
            expected = candidate['artifacts'][name]
            local = temporary / name
            source_file(source_box, source + '/' + name, local, expected)
            target_file(target_box, local, stage + '/' + name, expected)
        target_file(target_box, controller_candidate, stage + '/candidate.json',
                    {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        remote(target_box, 'doas', 'mv', '-nT', stage, target)
        remote(target_box, 'doas', 'sh', '-s', '--', operation,
               input_bytes=(f'set -eu\ntest ! -e {stage}\ntest ! -L {stage}\n'
                            f'test -d {target}\n').encode())
        if remote(target_box, 'doas', 'cat', target + '/candidate.json') != raw:
            raise UpdateError('published target candidate differs from the controller receipt')
    return {'kind': 'klokast.vm-template-transfer.v1', 'operation_id': operation,
            'source_box': source_box, 'target_box': target_box,
            'artifacts': candidate['artifacts'], 'accepted': False}
