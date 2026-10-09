"""Same-box image inventory and bounded public qualification receipts.

These records contain build evidence, never controller private state. Importing
a receipt does not select an image or authorize a guest change.
"""
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from platform_updates import UpdateError, canonical, digest, no_application_release, unique_object

FILES = ('selection.json', 'request.json', 'inputs.json', 'candidate.json',
         'lifecycle.json', 'test-lifecycle.json', 'release-evidence.json', 'build-result.json')
MAX_FILE = 2 * 1024 * 1024
MAX_RECEIPT = len(FILES) * MAX_FILE
OP = re.compile(r'[0-9a-f]{24}')
BOX = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,29}[a-z0-9])?')


def inventory(repo, directory, box, tailscale):
    """Address only local dom0 using this controller's own Tailnet identity."""
    name = tailscale.get('Self', {}).get('DNSName', '').rstrip('.')
    prefix = box + '-ops.'
    if (not BOX.fullmatch(box) or not name.startswith(prefix) or
            not re.fullmatch(r'[a-z0-9-]+\.ts\.net', name[len(prefix):])):
        raise UpdateError('local Tailscale identity must be ' + box + '-ops on its Tailnet')
    directory.mkdir(mode=0o700)
    (directory / 'group_vars').symlink_to(repo / 'ansible/inventory-policy/group_vars')
    path = directory / 'hosts.json'
    host = box + '-dom0'
    path.write_text(canonical({'all': {'children': {'dom0': {'hosts': {host: {
        'ansible_host': host + '.' + name[len(prefix):],
        'node_name': box, 'node_domain_role': 'dom0'}}}}, 'hosts': {'localhost': {
        'ansible_connection': 'local', 'ansible_python_interpreter': '/usr/bin/python3'}}}}) + '\n')
    path.chmod(0o600)
    return path


def validate(box, operation, files):
    if not BOX.fullmatch(box) or not OP.fullmatch(operation) or set(files) != set(FILES):
        raise UpdateError('image receipt has an invalid box, operation, or file set')
    if any(not isinstance(value, dict) or len(canonical(value).encode()) > MAX_FILE for value in files.values()):
        raise UpdateError('image receipt files must be bounded JSON objects')
    request, inputs, candidate = (files[name] for name in ('request.json', 'inputs.json', 'candidate.json'))
    outcome, release, selection = (files[name] for name in ('build-result.json', 'release-evidence.json', 'selection.json'))
    boot = candidate['boot_test']
    expected = no_application_release(inputs, candidate, boot['openrc_test'], boot['personalized_test'], boot['maintenance_restore'])
    if (request.get('kind') != 'klokast.vm-template-build-request.v1' or
            request.get('box') != box or request.get('operation_id') != operation or 'app_test' in request or
            request.get('inputs_sha256') != inputs['inputs_sha256'] or
            candidate.get('box') != box or candidate.get('operation_id') != operation or
            candidate.get('validation') != 'base-boot-tested' or release != expected or
            outcome.get('operation_id') != operation or outcome.get('state') != 'candidate-built' or
            outcome.get('accepted') is not False or outcome.get('inputs_sha256') != inputs['inputs_sha256'] or
            outcome.get('artifacts') != candidate['artifacts'] or
            outcome.get('release_evidence_sha256') != release['release_sha256'] or
            selection.get('profile') != inputs['profile'] or selection.get('branch') != inputs['branch'] or
            selection.get('architecture') != inputs['architecture']):
        raise UpdateError('image receipt differs from its box, request, or qualification')
    observed = dt.datetime.fromisoformat(selection['observed_at'].replace('Z', '+00:00'))
    if observed.utcoffset() != dt.timedelta(0):
        raise UpdateError('image receipt observation must use UTC')
    for filename, expected in (
            ('lifecycle.json', {'stage': 'cleaned', 'domain': 'vm-build-' + operation}),
            ('test-lifecycle.json', {'stage': 'cleaned', **{key + '_domain': prefix + operation for key, prefix in (
                ('openrc', 'vm-openrc-'), ('personalize', 'vm-personalize-'),
                ('profile', 'vm-profile-'), ('restore', 'vm-restore-'))}, 'domain': 'vm-test-' + operation})):
        if any(files[filename].get(key) != value for key, value in expected.items()):
            raise UpdateError('image receipt has incomplete disposable guest cleanup')


def read_file(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or
            info.st_mode & 0o022 or not 0 < info.st_size <= MAX_FILE):
        raise UpdateError('image receipt file has unsafe type, owner, permissions, or size: ' + path.name)
    return json.loads(path.read_text(), object_pairs_hook=unique_object)


def export_receipt(builds, box, operation):
    if not OP.fullmatch(operation):
        raise UpdateError('image receipt requires a 24-character build identifier')
    directory = builds / operation
    if directory.is_symlink() or not directory.is_dir():
        raise UpdateError('image receipt directory is absent or unsafe')
    files = {name: read_file(directory / name) for name in FILES}
    validate(box, operation, files)
    result = {'kind': 'klokast.vm-image-receipt.v1', 'box': box, 'operation_id': operation, 'files': files}
    result['receipt_sha256'] = digest(result)
    return result


def import_receipt(builds, box, receipt):
    if (set(receipt) != {'kind', 'box', 'operation_id', 'files', 'receipt_sha256'} or
            receipt['kind'] != 'klokast.vm-image-receipt.v1' or receipt['box'] != box or
            receipt['receipt_sha256'] != digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})):
        raise UpdateError('image receipt envelope or checksum is invalid')
    operation, files = receipt['operation_id'], receipt['files']
    validate(box, operation, files)
    destination = builds / operation
    if destination.exists() or destination.is_symlink():
        if export_receipt(builds, box, operation) != receipt:
            raise UpdateError('image receipt conflicts with retained local evidence; original files preserved')
        return operation
    temporary = Path(tempfile.mkdtemp(prefix='.receipt-', dir=builds))
    try:
        for name, value in files.items():
            with (temporary / name).open('x') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(canonical(value) + '\n'); stream.flush(); os.fsync(stream.fileno())
        # Caller holds the existing controller build lock. Never merge a
        # partial import or overwrite a completed operation.
        temporary.rename(destination)
        descriptor = os.open(builds, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return operation
