"""Synthetic personalization fixtures; no production identities or data."""
import copy
import fcntl
import json
from pathlib import Path
import os
import struct
import time

import vm_personalize as p


def fixture(root, retained):
    runtime = {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[300000, 65536]]}
    request = {'kind': 'klokast.vm-personalize.v1', 'operation_id': 'a' * 24, 'box': 'boxa', 'role': 'iot',
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
