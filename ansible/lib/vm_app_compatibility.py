"""Fixed component tests, never deployment or application-update authority.

The controller stages a catalog-pinned public image. Archive parsing and image
loading run only inside the disposable Xen guest. No production data is used.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

MIB = 1024 * 1024
MAX_ARCHIVE = 256 * MIB
MAX_CAPSULE = MAX_ARCHIVE + MIB
TESTS = ('image_identity', 'rootless', 'read_only', 'http_content', 'directory_redirect',
         'missing_page', 'restart', 'synthetic_data_unchanged')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError('duplicate application test field')
        result[key] = value
    return result


def checksum(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(MIB), b''):
            value.update(chunk)
    return value.hexdigest()


def hash_value(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def blob(value, maximum):
    if (not isinstance(value, dict) or set(value) != {'sha256', 'bytes'} or
            not hash_value(value['sha256']) or type(value['bytes']) is not int or
            not 0 < value['bytes'] <= maximum):
        raise RuntimeError('invalid application test artifact identity')


def validate(selection):
    if (not isinstance(selection, dict) or set(selection) !=
            {'kind', 'app', 'image_ref', 'manifest_sha256', 'image_id', 'archive', 'config_sha256', 'adapter_sha256'} or
            selection['kind'] != 'klokast.vm-app-test-input.v1' or selection['app'] != 'static-site-web' or
            any(not hash_value(selection[key]) for key in ('manifest_sha256', 'image_id', 'config_sha256', 'adapter_sha256')) or
            selection['image_ref'] != 'ghcr.io/static-web-server/static-web-server@sha256:' + selection['manifest_sha256']):
        raise RuntimeError('invalid fixed application test selection')
    blob(selection['archive'], MAX_ARCHIVE)


def validate_request(request):
    if not isinstance(request, dict) or set(request) != {'selection', 'capsule'}:
        raise RuntimeError('invalid application test request')
    validate(request['selection'])
    blob(request['capsule'], MAX_CAPSULE)


def verify_result(result, selection):
    validate(selection)
    if (not isinstance(result, dict) or result.get('production_qualified') is not False or
            not isinstance(result.get('tests'), dict) or any(v is not True for v in result['tests'].values()) or
            result != {'kind': 'klokast.vm-app-test-result.v1', 'selection': selection,
                  'tests': {name: True for name in TESTS}, 'production_qualified': False}):
        raise RuntimeError('application component test evidence is incomplete or differs from its selection')


def invoke(argv, timeout=300):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if result.returncode or len(result.stdout) > MIB:
        raise RuntimeError('application image staging failed: ' + argv[0])
    return result.stdout


def prepare(repo, work):
    """Copy an unchanged catalog image with native digest verification, no build."""
    import yaml  # Controller only; the test guest needs only the standard library.
    repo, work = Path(repo), Path(work)
    app = repo / 'apps/static-site'
    lock = yaml.safe_load((app / 'images.lock.yml').read_text())['static_site_images']['static_web_server']
    digest = lock['amd64_digest']
    reference = lock['pull_ref']
    if (not isinstance(digest, str) or not re.fullmatch('sha256:[0-9a-f]{64}', digest) or
            reference != 'ghcr.io/static-web-server/static-web-server@' + digest):
        raise RuntimeError('Static Site catalog must select one immutable amd64 image')
    work.mkdir(mode=0o700)  # Refuse reuse, including partial earlier input.
    auth = work / 'auth.json'
    auth.write_text('{"auths":{}}\n')  # Public input; never read controller registry credentials.
    auth.chmod(0o600)
    archive = work / 'image.oci'
    invoke(['skopeo', '--command-timeout', '5m', 'copy', '--src-no-creds', '--authfile', str(auth),
            '--preserve-digests', '--override-os', 'linux', '--override-arch', 'amd64',
            'docker://' + reference, 'oci-archive:' + str(archive) + ':static-site-test'])
    if archive.is_symlink() or not archive.is_file() or not 0 < archive.stat().st_size <= MAX_ARCHIVE:
        raise RuntimeError('application image archive exceeds the component test size limit')
    raw = invoke(['skopeo', 'inspect', '--raw', 'oci-archive:' + str(archive)])
    if hashlib.sha256(raw).hexdigest() != digest[7:]:
        raise RuntimeError('staged OCI manifest differs from the catalog digest')
    manifest = json.loads(raw, object_pairs_hook=unique)
    config_digest = manifest.get('config', {}).get('digest', '')
    if not isinstance(config_digest, str) or not re.fullmatch('sha256:[0-9a-f]{64}', config_digest):
        raise RuntimeError('staged application image has no exact configuration identity')
    config = app / 'ansible/roles/static-site-runtime/templates/static-site-sws.toml.j2'
    # This catalog configuration has no private or templated fields. Refuse a
    # future template change until its component test handles rendering.
    config_bytes = config.read_bytes()
    if b'{{' in config_bytes or b'{%' in config_bytes or len(config_bytes) > 65536:
        raise RuntimeError('Static Site test requires the fixed public web configuration')
    (work / 'config.toml').write_bytes(config_bytes)
    adapter = app / 'ansible/roles/static-site-runtime/files/vm_compatibility.py'
    selection = {'kind': 'klokast.vm-app-test-input.v1', 'app': 'static-site-web',
                 'image_ref': reference, 'manifest_sha256': digest[7:], 'image_id': config_digest[7:],
                 'archive': {'sha256': checksum(archive), 'bytes': archive.stat().st_size},
                 'config_sha256': hashlib.sha256(config_bytes).hexdigest(), 'adapter_sha256': checksum(adapter)}
    validate(selection)
    (work / 'request.json').write_bytes(canonical(selection))
    output = work.parent / 'app-test.tar'
    with tarfile.open(output, 'x', format=tarfile.USTAR_FORMAT) as target:
        for name in ('request.json', 'config.toml', 'image.oci'):
            target.add(work / name, arcname=name, recursive=False)
    result = {'selection': selection, 'capsule': {'sha256': checksum(output), 'bytes': output.stat().st_size}}
    validate_request(result)
    return result


def unpack(device, destination, expected, adapter):
    """Called only after the PID 1 test verifies the networkless Xen boundary."""
    blob(expected, MAX_CAPSULE)
    destination.mkdir(mode=0o755)
    capsule = destination / 'input.tar'
    value = hashlib.sha256()
    remaining = expected['bytes']
    with device.open('rb') as source, capsule.open('xb') as target:
        while remaining:
            chunk = source.read(min(remaining, MIB))
            if not chunk:
                raise RuntimeError('application capsule is truncated')
            remaining -= len(chunk)
            value.update(chunk)
            target.write(chunk)
    if value.hexdigest() != expected['sha256']:
        raise RuntimeError('application capsule checksum differs')
    limits = {'request.json': 65536, 'config.toml': 65536, 'image.oci': MAX_ARCHIVE}
    seen = set()
    with tarfile.open(capsule, 'r|') as archive:
        for member in archive:
            if (member.name not in limits or member.name in seen or not member.isfile() or
                    not 0 < member.size <= limits[member.name]):
                raise RuntimeError('application capsule has unsafe, duplicate, or unexpected entries')
            seen.add(member.name)
            with archive.extractfile(member) as source, (destination / member.name).open('xb') as target:
                while chunk := source.read(MIB):
                    target.write(chunk)
    if seen != set(limits):
        raise RuntimeError('application capsule is incomplete')
    selection = json.loads((destination / 'request.json').read_bytes(), object_pairs_hook=unique)
    validate(selection)
    if (checksum(destination / 'image.oci') != selection['archive']['sha256'] or
            (destination / 'image.oci').stat().st_size != selection['archive']['bytes'] or
            checksum(destination / 'config.toml') != selection['config_sha256'] or
            checksum(adapter) != selection['adapter_sha256']):
        raise RuntimeError('application image, configuration, or installed adapter differs')
    capsule.unlink()
    return selection
