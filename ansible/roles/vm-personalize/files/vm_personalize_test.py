"""Synthetic personalization fixtures; no production identities or data."""
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import os
import struct
import stat
import time

import vm_personalize as p


def ssh_fixture(retained, deadline):
    """Generate disposable keys; no private key fixture is committed to Git."""
    for key in p.data.SSH_IDENTITIES:
        p.data.run(['ssh-keygen', '-q', '-t', key.removeprefix('platform-ssh-'),
                    '-N', '', '-f', str(retained / key)], deadline)
        (retained / (key + '.pub')).unlink()
    return {key: p.data.tree(retained / key, deadline) for key in p.data.SSH_IDENTITIES}


def prepare_boot(run, operation, inputs_sha256, source_sha256):
    """Prepare a separate synthetic machine from the sealed read-only disk.

    Called only by a dedicated custom-init test guest. All three writable
    devices belong to the disposable builder operation, never production.
    """
    p.data.environment()
    devices = {'xvdc': 256 * 1024**2, 'xvdd': 4 * 1024**3, 'xvdf': 4 * 1024**3}
    for name, size in devices.items():
        path = Path('/dev/' + name)
        if (not stat.S_ISBLK(path.lstat().st_mode) or
                int(Path('/sys/class/block/' + name + '/size').read_text()) * 512 != size or
                any(r['source'].startswith(str(path)) for r in p.data.mount_records())):
            raise RuntimeError('personalized boot fixture has an unexpected or mounted disk')
    if Path('/sys/class/block/xvdf/ro').read_text().strip() != '1':
        raise RuntimeError('sealed personalization source must be attached read-only')
    deadline = time.monotonic() + 240
    value = hashlib.sha256()
    with open('/dev/xvdf', 'rb', buffering=0) as source, open('/dev/xvdd', 'wb', buffering=0) as target:
        while chunk := source.read(1024 * 1024):
            p.data.remaining(deadline); value.update(chunk)
            if target.write(chunk) != len(chunk):
                raise RuntimeError('personalized test root copy has a short device write')
        os.fsync(target.fileno())
    if value.hexdigest() != source_sha256:
        raise RuntimeError('personalized boot source differs from the sealed root image')
    copied = hashlib.sha256()
    with open('/dev/xvdd', 'rb', buffering=0) as target:
        while chunk := target.read(1024 * 1024):
            p.data.remaining(deadline); copied.update(chunk)
    if copied.hexdigest() != source_sha256:
        raise RuntimeError('personalized test root copy differs from the sealed source')
    # Remove the preceding synthetic partition fixture on this exact test disk.
    with open('/dev/xvdc', 'r+b', buffering=0) as disk:
        disk.write(b'\0' * (4 * 1024 * 1024)); os.fsync(disk.fileno())
        fcntl.ioctl(disk.fileno(), 0x125f)
    retained_uuid = '22222222-2222-4222-8222-222222222222'
    run(['mkfs.ext4', '-F', '-U', retained_uuid, '/dev/xvdc'])
    mounted = []
    try:
        for device, path in (('/dev/xvdc', p.RETAINED), ('/dev/xvdd', p.ROOT)):
            path.mkdir(mode=0o700, exist_ok=True)
            run(['mount', '-o', 'nodev,nosuid,noexec', device, str(path)]); mounted.append(path)
        marker = json.loads((p.ROOT / 'etc/klokast-template.json').read_text())
        if marker['inputs_sha256'] != inputs_sha256:
            raise RuntimeError('personalized boot template marker differs')
        runtime = {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[300000, 65536]]}
        p.data.create_record(p.RETAINED / '.klokast-retained-identity.json',
                             {'kind': 'klokast.vm-retained-identity.v1', 'runtime': runtime})
        identity = p.RETAINED / p.IDENTITY
        identity.write_bytes(b'{}\n'); identity.chmod(0o600)  # Valid, unenrolled synthetic state.
        final = {'kind': 'klokast.vm-retained-final-result.v3', 'request_sha256': 'e' * 64,
                 'stage_receipt_sha256': 'f' * 64, 'entries': {p.IDENTITY: p.data.tree(identity, deadline)},
                 'copy_verified': True, 'adoption_accepted': False}
        final['entries'].update(ssh_fixture(p.RETAINED, deadline))
        final['receipt_sha256'] = p.data.digest(final)
        p.data.create_record(p.RETAINED / '.klokast-final-result.json', final)
        request = {'kind': 'klokast.vm-personalize.v2', 'operation_id': operation, 'box': 'boxa', 'role': 'iot',
                   'engine_commit': marker['engine_commit'], 'inputs_sha256': inputs_sha256,
                   'release_sha256': p.data.digest({'synthetic_release': operation, 'inputs_sha256': inputs_sha256}),
                   'root_uuid': p.data.filesystem_uuid('/dev/xvdd'), 'retained_uuid': retained_uuid,
                   'retained_receipt_sha256': final['receipt_sha256'], 'runtime': runtime,
                   'packages': p.package_set(), 'admin_password_hash': '!',
                   'files': json.loads(Path('/usr/local/libexec/personalization-config.json').read_text())}
        run(['mount', '-o', 'remount,ro', str(p.RETAINED)])
        with open('/dev/xvdc', 'rb', buffering=0) as disk:
            fcntl.ioctl(disk.fileno(), 0x125d, struct.pack('i', 1))
        receipt = p.personalize(request, deadline)
        p.data.create_record(p.ROOT / 'etc/klokast-personalization-test.json', request)
        # Add this service only to the disposable personalized copy.
        service = p.ROOT / 'etc/init.d/klokast-template-personalized-test'
        service.write_text('#!/sbin/openrc-run\n'
                           'depend() { need cgroups localmount bootmisc networking nftables tailscale klokast-podman-runroot-cleanup; }\n'
                           'start() { /usr/local/libexec/klokast-template-test --personalized >/dev/console 2>&1; }\n')
        service.chmod(0o700)
        (p.ROOT / 'etc/runlevels/default/klokast-template-personalized-test').symlink_to('/etc/init.d/klokast-template-personalized-test')
        p.data.run(['sync', '-f', str(p.ROOT)], deadline)
        return {'request_sha256': p.data.digest(request), 'receipt_sha256': p.data.digest(receipt),
                'source_root_sha256': source_sha256, 'production_data_used': False}
    finally:
        for path in reversed(mounted):
            run(['umount', str(path)])
        with open('/dev/xvdc', 'rb', buffering=0) as disk:
            fcntl.ioctl(disk.fileno(), 0x125d, struct.pack('i', 0))


def verify_boot(run, operation, inputs_sha256):
    """Verify actual OpenRC behavior on the separate synthetic machine."""
    p.ROOT = Path('/')
    request = p.data.read_record(Path('/etc/klokast-personalization-test.json'))
    receipt = p.data.read_record(Path('/etc/klokast-personalization.json'))
    if (request['operation_id'] != operation or request['inputs_sha256'] != inputs_sha256 or
            receipt['request_sha256'] != p.data.digest(request) or receipt['adoption_accepted'] is not False or
            run(['hostname']) != 'boxa-iot'):
        raise RuntimeError('personalized boot provenance or hostname differs')
    for name, record in receipt['files'].items():
        path = p.regular(Path('/'), name)
        if (hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256'] or
                stat.S_IMODE(path.stat().st_mode) != record['mode']):
            raise RuntimeError('personalized configuration changed during normal boot')
    for key, relative in p.data.SSH_IDENTITIES.items():
        kind = key.removeprefix('platform-ssh-')
        expected = receipt['ssh_public_sha256'][key]
        if (p.ssh_public_digest(Path('/') / relative, kind, time.monotonic() + 30) != expected or
                p.ssh_public_digest(Path('/srv/retained') / key, kind, time.monotonic() + 30) != expected):
            raise RuntimeError('personalized SSH host identity differs from retained state')
    if p.package_set() != request['packages'] or p.data.runtime_identity(Path('/')) != request['runtime']:
        raise RuntimeError('personalized packages or numeric ownership differ')
    mounts = [r for r in p.data.mount_records() if r['path'] == '/srv/retained']
    if (len(mounts) != 1 or mounts[0]['type'] != 'ext4' or mounts[0]['root'] != '/' or
            'rw' not in mounts[0]['options'] or mounts[0]['source'] != '/dev/xvdc' or
            p.data.filesystem_uuid('/dev/xvdc') != request['retained_uuid']):
        raise RuntimeError('OpenRC did not mount the recorded retained filesystem')
    for service in ('networking', 'nftables', 'tailscale', 'klokast-podman-runroot-cleanup'):
        run(['/sbin/rc-service', service, 'status'])
    run(['nft', '-c', '-f', '/etc/nftables.nft'])
    rules = json.loads(run(['nft', '-j', 'list', 'table', 'inet', 'klokast_vm_filter']))
    policies = {r['chain']['name']: r['chain'].get('policy') for r in rules['nftables'] if 'chain' in r}
    if policies != {'input': 'drop', 'forward': 'drop', 'output': 'accept'}:
        raise RuntimeError('personalized firewall policies differ from the public recipe')
    status = json.loads(run(['tailscale', 'status', '--json']))
    if status.get('BackendState') != 'NeedsLogin':
        raise RuntimeError('personalized synthetic identity must remain unenrolled')
    processes = []
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            arguments = path.read_bytes().split(b'\0')
        except FileNotFoundError:
            continue
        if arguments[:1] == [b'/usr/sbin/tailscaled']:
            processes.append(arguments)
    if (len(processes) != 1 or
            [v for v in processes[0] if v.startswith(b'--state=')][-1:] != [b'--state=/srv/retained/platform-tailscale-state'] or
            Path('/var/lib/tailscale/tailscaled.state').exists()):
        raise RuntimeError('native Tailscale daemon did not select retained identity state')
    identity = p.regular(Path('/srv/retained'), p.IDENTITY, p.data.MAX_IDENTITY_BYTES)
    if stat.S_IMODE(identity.stat().st_mode) != 0o600 or identity.stat().st_size == 0:
        raise RuntimeError('retained identity lost its private file metadata')
    home = Path('/home/neo').stat()
    if (home.st_uid, home.st_gid) != (request['runtime']['uid'], request['runtime']['gid']):
        raise RuntimeError('personalized runtime home ownership differs')
    return {'request_sha256': p.data.digest(request), 'receipt_sha256': p.data.digest(receipt),
            'production_data_used': False}


def fixture(root, retained):
    runtime = {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[300000, 65536]]}
    request = {'kind': 'klokast.vm-personalize.v2', 'operation_id': 'a' * 24, 'box': 'boxa', 'role': 'iot',
               'engine_commit': 'b' * 40, 'release_sha256': 'c' * 64, 'inputs_sha256': 'd' * 64,
               'root_uuid': '11111111-1111-4111-8111-111111111111',
               'retained_uuid': '22222222-2222-4222-8222-222222222222',
               'runtime': runtime, 'files': {k: '# synthetic approved configuration\n' for k in p.FILES},
               'packages': {'linux-virt': '1-r0', 'tailscale': '2-r0', 'podman': '3-r0'},
               'admin_password_hash': '!'}
    request['files']['etc/hostname'] = 'boxa-iot\n'
    files = {'etc/passwd': 'root:x:0:0:root:/root:/bin/ash\n', 'etc/group': 'root:x:0:\nwheel:x:10:\n',
             'etc/shadow': 'root:!:20000:0:99999:7:::\n',
             'lib/apk/db/installed': ''.join('P:' + k + '\nV:' + v + '\n\n' for k, v in request['packages'].items()),
             'etc/klokast-template.json': json.dumps({'kind': 'klokast.vm-template-marker.v1',
                 'engine_commit': request['engine_commit'], 'profile': 'shared-alpine-v1', 'inputs_sha256': request['inputs_sha256']})}
    files.update({'etc/init.d/' + v: '#!/sbin/openrc-run\n' for v in ('networking', 'nftables', 'tailscale')})
    for name, content in files.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o600 if name == 'etc/shadow' else (0o755 if name.startswith('etc/init.d/') else 0o644))
    for name in ('var/lib/tailscale', 'etc/runlevels'):
        (root / name).mkdir(parents=True)
    retained.mkdir(exist_ok=True)
    p.data.create_record(retained / '.klokast-retained-identity.json',
                         {'kind': 'klokast.vm-retained-identity.v1', 'runtime': runtime})
    (retained / p.IDENTITY).write_bytes(b'opaque-synthetic-state')
    (retained / p.IDENTITY).chmod(0o600)
    final = {'kind': 'klokast.vm-retained-final-result.v3', 'request_sha256': 'e' * 64,
             'stage_receipt_sha256': 'f' * 64,
             'entries': {p.IDENTITY: p.data.tree(retained / p.IDENTITY, time.monotonic() + 60)},
             'copy_verified': True, 'adoption_accepted': False}
    final['entries'].update(ssh_fixture(retained, time.monotonic() + 60))
    final['receipt_sha256'] = p.data.digest(final)
    p.data.create_record(retained / '.klokast-final-result.json', final)
    request['retained_receipt_sha256'] = final['receipt_sha256']
    return request



def test(run, operation):
    p.data.environment()
    for device in ('xvdc', 'xvdd'):
        if int(Path('/sys/class/block/' + device + '/size').read_text()) != 256 * 1024 * 1024 // 512:
            raise RuntimeError('personalization fixture requires its two disposable 256 MiB disks')
    p.ROOT.mkdir(mode=0o700, exist_ok=True)
    p.RETAINED.mkdir(mode=0o700, exist_ok=True)
    root_uuid = '11111111-1111-4111-8111-111111111111'
    retained_uuid = '22222222-2222-4222-8222-222222222222'
    mounted = []
    def readonly(value):
        with open('/dev/xvdc', 'rb', buffering=0) as stream:
            fcntl.ioctl(stream.fileno(), 0x125d, struct.pack('i', value))
    try:
        for device, identity, path in (('/dev/xvdc', retained_uuid, p.RETAINED),
                                        ('/dev/xvdd', root_uuid, p.ROOT)):
            run(['mkfs.ext4', '-F', '-U', identity, device])
            run(['mount', '-o', 'nodev,nosuid,noexec', device, str(path)])
            mounted.append(path)
        request = fixture(p.ROOT, p.RETAINED)
        request['operation_id'] = operation
        run(['mount', '-o', 'remount,ro', str(p.RETAINED)])
        try:
            p.personalize(request, time.monotonic() + 120)
        except p.PersonalizeError as error:
            if 'read-only block device' not in str(error):
                raise
        else:
            raise RuntimeError('personalization accepted a writable retained block device')
        readonly(1)
        identity_before = (p.RETAINED / p.IDENTITY).read_bytes()
        bad = copy.deepcopy(request)
        bad['runtime']['uid'] = 2001
        try:
            p.personalize(bad, time.monotonic() + 120)
        except p.PersonalizeError:
            pass
        else:
            raise RuntimeError('personalization accepted conflicting retained ownership')
        result = p.personalize(request, time.monotonic() + 120)
        home = (p.ROOT / 'home/neo').stat()
        if (not result['packages_unchanged'] or result['adoption_accepted'] or
                (home.st_uid, home.st_gid) != (2000, 2000) or
                (p.RETAINED / p.IDENTITY).read_bytes() != identity_before or
                any((p.ROOT / 'var/lib/tailscale').iterdir()) or
                p.data.runtime_identity(p.ROOT) != request['runtime']):
            raise RuntimeError('personalization did not preserve exact packages and numeric identity')
        try:
            p.personalize(request, time.monotonic() + 120)
        except p.PersonalizeError:
            pass
        else:
            raise RuntimeError('personalization accepted a reused clone')
        return {'configuration_written': True, 'numeric_ownership_verified': True,
                'packages_unchanged': True, 'retained_identity_unchanged': True,
                'production_data_used': False, 'personalized_boot_tested': False}
    finally:
        for path in reversed(mounted):
            run(['umount', str(path)])
        readonly(0)
