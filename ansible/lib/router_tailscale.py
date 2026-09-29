"""Freeze one signed upstream Tailscale archive for a router template build.

The resolver runs on the active controller. It has no guest, enrollment, or
installation authority. APK packages and this upstream component stay separate.
"""

import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import urllib.request

from platform_updates import UpdateError

METADATA = 'https://pkgs.tailscale.com/stable/?mode=json'
VERIFIER = Path('/usr/local/bin/tailscale-distsign')
BUILD_RECORD = Path('/usr/local/share/klokast/toolchains/tailscale-distsign.json')
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+')
HASH = re.compile(r'[0-9a-f]{64}')
SOURCE_TREE = re.compile(r'[0-9a-f]{40}')
MAX_METADATA = 64 * 1024
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_BINARY = 64 * 1024 * 1024
GO_SHA256 = '708effb774be8237570d0add163225abbdfaf4fca28b2611df167beba4feef89'
OPENRC = Path(__file__).resolve().parents[1] / 'roles/router-alpine-rootfs/files/tailscale-openrc'
OPENRC_FILE = 'components/tailscale-openrc'


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError('Tailscale source contains a duplicate JSON field')
        result[key] = value
    return result


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def select(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_METADATA:
        raise UpdateError('upstream Tailscale stable metadata is empty or too large')
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as error:
        raise UpdateError('upstream Tailscale stable metadata is not valid JSON') from error
    if not isinstance(value, dict):
        raise UpdateError('upstream Tailscale stable metadata is not an object')
    version = value.get('Version')
    tarball_version = value.get('TarballsVersion')
    tarballs = value.get('Tarballs')
    if (not isinstance(version, str) or not VERSION.fullmatch(version) or
            version != tarball_version or not isinstance(tarballs, dict) or
            tarballs.get('amd64') != 'tailscale_' + version + '_amd64.tgz'):
        raise UpdateError('latest upstream stable Tailscale has no matching Linux amd64 archive')
    return version, tarballs['amd64'], hashlib.sha256(raw).hexdigest()


def fetch_metadata():
    request = urllib.request.Request(METADATA, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.url != METADATA or response.status != 200:
                raise UpdateError('Tailscale stable metadata redirected or failed')
            raw = response.read(MAX_METADATA + 1)
    except (OSError, TimeoutError) as error:
        raise UpdateError('cannot read fresh upstream Tailscale stable metadata') from error
    return select(raw)


def verifier_identity(repo, executable=VERIFIER, record_path=BUILD_RECORD):
    executable, record_path = Path(executable), Path(record_path)
    try:
        info = executable.lstat()
        record_info = record_path.lstat()
    except OSError as error:
        raise UpdateError('installed Tailscale signature verifier or build record is missing') from error
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or
            not 0 < info.st_size <= 32 * 1024 * 1024):
        raise UpdateError('installed Tailscale signature verifier is not root-owned and fixed')
    if (not stat.S_ISREG(record_info.st_mode) or record_info.st_uid != 0 or
            record_info.st_mode & 0o022 or not 0 < record_info.st_size <= 4096):
        raise UpdateError('Tailscale signature verifier build record is unsafe')
    try:
        record = json.loads(record_path.read_text(), object_pairs_hook=unique)
        tree = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD:tools/tailscale-distsign'],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise UpdateError('cannot verify the checked Tailscale signature verifier source') from error
    if (not isinstance(record, dict) or set(record) != {
            'kind', 'source_tree', 'upstream_module', 'go_version', 'go_archive_sha256', 'binary_sha256'} or
            record['kind'] != 'klokast.tailscale-distsign-build.v1' or
            not SOURCE_TREE.fullmatch(tree) or record['source_tree'] != tree or
            record['upstream_module'] != 'tailscale.com@v1.102.4' or
            record['go_version'] != 'go1.26.6' or
            record['go_archive_sha256'] != GO_SHA256 or
            record['binary_sha256'] != sha256(executable)):
        raise UpdateError('installed Tailscale signature verifier differs from its checked source')
    return tree, record['binary_sha256']


def inspect_archive(path, version):
    path = Path(path)
    if (path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_ARCHIVE or
            not VERSION.fullmatch(version)):
        raise UpdateError('signed Tailscale archive is missing or exceeds its size limit')
    prefix = 'tailscale_' + version + '_amd64/'
    allowed = {prefix.rstrip('/'), prefix + 'tailscale', prefix + 'tailscaled', prefix + 'systemd',
               prefix + 'systemd/tailscaled.service', prefix + 'systemd/tailscaled.defaults',
               prefix + 'systemd/tailscale-online.target', prefix + 'systemd/tailscale-wait-online.service'}
    binaries = {}
    try:
        with tarfile.open(path, 'r:gz') as archive:
            members = []
            expanded = 0
            for member in archive:
                members.append(member)
                expanded += member.size
                if len(members) > len(allowed) or expanded > 2 * MAX_BINARY + 4 * 1024 * 1024:
                    raise UpdateError('signed Tailscale archive exceeds its bounded file set')
            if len(members) != len(allowed) or {m.name for m in members} != allowed:
                raise UpdateError('signed Tailscale archive has an unsupported file set')
            for member in members:
                if member.name in {prefix.rstrip('/'), prefix + 'systemd'}:
                    if not member.isdir():
                        raise UpdateError('signed Tailscale archive has an unsafe directory')
                elif (not member.isfile() or member.size <= 0 or
                      member.size > (MAX_BINARY if member.name in {prefix + 'tailscale', prefix + 'tailscaled'} else 1024 * 1024)):
                    raise UpdateError('signed Tailscale archive has an unsafe file')
                if member.name in {prefix + 'tailscale', prefix + 'tailscaled'}:
                    stream = archive.extractfile(member)
                    if stream is None or stream.read(4) != b'\x7fELF':
                        raise UpdateError('signed Tailscale archive has a non-ELF binary')
                    value = hashlib.sha256(b'\x7fELF')
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        value.update(chunk)
                    binaries[member.name.rsplit('/', 1)[1] + '_sha256'] = value.hexdigest()
    except (OSError, tarfile.TarError) as error:
        raise UpdateError('signed Tailscale archive cannot be inspected') from error
    return binaries


def freeze(directory, repo, *, executable=VERIFIER, record_path=BUILD_RECORD,
           metadata=None):
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise UpdateError('Tailscale source requires a new router input directory')
    tree, verifier_sha256 = verifier_identity(repo, executable, record_path)
    version, archive_name, metadata_sha256 = metadata or fetch_metadata()
    if (not isinstance(version, str) or not VERSION.fullmatch(version) or
            archive_name != 'tailscale_' + version + '_amd64.tgz' or
            not isinstance(metadata_sha256, str) or not HASH.fullmatch(metadata_sha256)):
        raise UpdateError('Tailscale stable selection has an invalid identity')
    components = directory / 'components'
    try:
        components.mkdir(mode=0o700)
    except OSError as error:
        raise UpdateError('Tailscale source requires a new empty component directory') from error
    try:
        result = subprocess.run([str(executable), '--archive', archive_name, '--directory', str(components)],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=240, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError('Tailscale archive signature verifier failed or timed out') from error
    if result.returncode or len(result.stdout) > 4096:
        raise UpdateError('Tailscale archive signature verification failed')
    try:
        proof = json.loads(result.stdout, object_pairs_hook=unique)
    except ValueError as error:
        raise UpdateError('Tailscale archive verifier returned an invalid result') from error
    path = components / archive_name
    if (not isinstance(proof, dict) or set(proof) != {
            'kind', 'archive', 'sha256', 'bytes', 'verifier', 'signed'} or
            proof['kind'] != 'klokast.tailscale-upstream-archive.v1' or
            proof['archive'] != archive_name or proof['signed'] is not True or
            proof['verifier'] != 'tailscale.com/clientupdate/distsign@v1.102.4' or
            not isinstance(proof['bytes'], int) or type(proof['bytes']) is not int or
            not 0 < proof['bytes'] <= MAX_ARCHIVE or not HASH.fullmatch(proof['sha256']) or
            path.is_symlink() or not path.is_file() or path.stat().st_size != proof['bytes'] or
            sha256(path) != proof['sha256']):
        raise UpdateError('Tailscale verifier proof differs from the downloaded archive')
    binaries = inspect_archive(path, version)
    service = directory / OPENRC_FILE
    if (OPENRC.is_symlink() or not OPENRC.is_file() or
            not 0 < OPENRC.stat().st_size <= 4096):
        raise UpdateError('checked Tailscale OpenRC service is missing or unsafe')
    service.write_bytes(OPENRC.read_bytes())
    return {'kind': 'klokast.router-tailscale-input.v1', 'version': version,
            'file': 'components/' + archive_name, 'bytes': proof['bytes'],
            'sha256': proof['sha256'], 'openrc_file': OPENRC_FILE,
            'openrc_sha256': sha256(service), **binaries, 'metadata_sha256': metadata_sha256,
            'verifier_source_tree': tree, 'verifier_sha256': verifier_sha256,
            'signature_verified': True}


def validate(value):
    if (not isinstance(value, dict) or set(value) != {
            'kind', 'version', 'file', 'bytes', 'sha256', 'tailscale_sha256',
            'tailscaled_sha256', 'metadata_sha256', 'verifier_source_tree',
            'verifier_sha256', 'signature_verified', 'openrc_file', 'openrc_sha256'} or
            value['kind'] != 'klokast.router-tailscale-input.v1' or
            not isinstance(value['version'], str) or not VERSION.fullmatch(value['version']) or
            value['file'] != 'components/tailscale_' + value['version'] + '_amd64.tgz' or
            value['openrc_file'] != OPENRC_FILE or
            type(value['bytes']) is not int or not 0 < value['bytes'] <= MAX_ARCHIVE or
            any(not isinstance(value[k], str) or not HASH.fullmatch(value[k]) for k in (
                'sha256', 'tailscale_sha256', 'tailscaled_sha256', 'metadata_sha256',
                'verifier_sha256', 'openrc_sha256')) or
            not isinstance(value['verifier_source_tree'], str) or
            not SOURCE_TREE.fullmatch(value['verifier_source_tree']) or
            value['signature_verified'] is not True):
        raise UpdateError('Tailscale upstream input has an invalid frozen contract')
    return value


def verify(directory, value):
    validate(value)
    directory = Path(directory)
    components = directory / 'components'
    path = directory / value['file']
    service = directory / value['openrc_file']
    if (components.is_symlink() or not components.is_dir() or
            {p.name for p in components.iterdir()} != {path.name, service.name} or
            path.is_symlink() or not path.is_file() or path.stat().st_size != value['bytes'] or
            sha256(path) != value['sha256'] or
            service.is_symlink() or not service.is_file() or sha256(service) != value['openrc_sha256'] or
            inspect_archive(path, value['version']) != {k: value[k] for k in ('tailscale_sha256', 'tailscaled_sha256')}):
        raise UpdateError('frozen Tailscale archive or binary hashes changed')
    return value
