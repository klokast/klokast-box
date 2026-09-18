"""Personalize a cloned OS filesystem inside a networkless Xen guest.

The controller must verify the sealed image, approved configuration bundle,
retained-data receipt, backup, and authority before attaching these disks.
This primitive cannot select a release, authorize adoption, start services,
resolve packages, or download images. It never copies an old /etc or home.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

import retained_data as data

ROOT = Path('/personalize')
RETAINED = Path('/retained')
FILES = {
    'etc/hostname': 0o644,
    'etc/hosts': 0o644,
    'etc/network/interfaces': 0o644,
    'etc/resolv.conf': 0o644,
    'etc/nftables.nft': 0o644,
    'etc/klokast/overlay-ipv6-input.nft': 0o644,
    'etc/klokast/app-resources/vm-input.nft': 0o644,
    'etc/containers/registries.conf': 0o644,
    'etc/containers/containers.conf.d/10-klokast-network.conf': 0o644,
    'etc/init.d/klokast-podman-runroot-cleanup': 0o755,
}
IDENTITY = 'platform-tailscale-state'


class PersonalizeError(RuntimeError):
    pass


def validate(request):
    fields = {'kind', 'operation_id', 'box', 'role', 'engine_commit', 'release_sha256',
              'inputs_sha256', 'root_uuid', 'retained_uuid', 'runtime', 'files',
              'packages', 'admin_password_hash', 'retained_receipt_sha256'}
    if (not isinstance(request, dict) or set(request) != fields or
            request['kind'] != 'klokast.vm-personalize.v2'):
        raise PersonalizeError('unsupported or incomplete personalization request')
    patterns = {'operation_id': r'[0-9a-f]{24}', 'box': r'[a-z0-9][a-z0-9-]{0,30}',
                'engine_commit': r'[0-9a-f]{40}', 'release_sha256': r'[0-9a-f]{64}',
                'inputs_sha256': r'[0-9a-f]{64}',
                'retained_receipt_sha256': r'[0-9a-f]{64}',
                'root_uuid': r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',
                'retained_uuid': r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'}
    if (any(not isinstance(request[k], str) or not re.fullmatch(p, request[k]) for k, p in patterns.items()) or
            request['role'] not in {'bak', 'dmz', 'iot'} or request['root_uuid'] == request['retained_uuid']):
        raise PersonalizeError('personalization source, machine, or filesystem identity is invalid')
    # Reuse the numeric ownership contract of the retained-data primitive.
    data.validate({'kind': 'klokast.vm-retained-copy.v1', 'operation_id': request['operation_id'],
                   'source_uuid': request['root_uuid'], 'destination_uuid': request['retained_uuid'],
                   'runtime': request['runtime'], 'entries': [{'key': 'probe', 'source': 'srv/retained/probe'}]})
    if any(request['runtime'][k] < 1000 for k in ('uid', 'gid')):
        raise PersonalizeError('runtime ownership must not use reserved system identities')
    files = request['files']
    if (not isinstance(files, dict) or set(files) != set(FILES) or
            any(not isinstance(v, str) or not v or '\0' in v or len(v.encode()) > 65536 for v in files.values()) or
            files['etc/hostname'] != request['box'] + '-' + request['role'] + '\n'):
        raise PersonalizeError('approved configuration must contain the exact supported file set and hostname')
    password = request['admin_password_hash']
    if (not isinstance(password, str) or
            (password != '!' and not re.fullmatch(r'\$6\$(?:rounds=[0-9]{1,9}\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}', password))):
        raise PersonalizeError('admin password input must be a supported encrypted hash or a locked account')
    packages = request['packages']
    if (not isinstance(packages, dict) or not {'linux-virt', 'tailscale', 'podman'} <= packages.keys() or
            not 3 <= len(packages) <= 512 or
            any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+_.-]*', k) or
                not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+_.~-]*', v)
                for k, v in packages.items())):
        raise PersonalizeError('personalization requires the complete frozen package set')


def mounted(request):
    data.environment()
    records = data.mount_records()
    devices = []
    for root, device, access, key in ((ROOT, '/dev/xvdd', 'rw', 'root_uuid'),
                                      (RETAINED, '/dev/xvdc', 'ro', 'retained_uuid')):
        matches = [r for r in records if r['path'] == str(root)]
        if len(matches) != 1 or root.is_symlink() or not root.is_dir():
            raise PersonalizeError('personalization requires both exact mounted filesystems')
        item = matches[0]
        info = Path(device).lstat()
        if (item['source'] != device or item['type'] != 'ext4' or item['root'] != '/' or
                access not in item['options'] or not stat.S_ISBLK(info.st_mode) or
                item['device'] != f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' or
                sum(r['device'] == item['device'] for r in records) != 1 or
                any(r['path'].startswith(str(root) + '/') for r in records) or
                data.filesystem_uuid(device) != request[key]):
            raise PersonalizeError('personalization mount identity, access, or isolation differs')
        devices.append(item['device'])
    if len(set(devices)) != 2 or Path('/sys/class/block/xvdc/ro').read_text().strip() != '1':
        raise PersonalizeError('retained state must be a distinct read-only block device')


def regular(root, relative, maximum=1024 * 1024):
    path = data.below(root, relative)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or
            info.st_mode & 0o022 or info.st_size > maximum or info.st_dev != root.stat().st_dev):
        raise PersonalizeError('personalization file has unsafe metadata: ' + relative)
    return path


def put(relative, content, mode):
    path = ROOT
    for part in relative.split('/')[:-1]:
        path = path / part
        if not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o755)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022 or
                info.st_dev != ROOT.stat().st_dev):
            raise PersonalizeError('personalization destination directory is unsafe')
    path = path / relative.split('/')[-1]
    if path.exists() or path.is_symlink():
        regular(ROOT, relative)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(content if isinstance(content, bytes) else content.encode())
        stream.flush(); os.fsync(stream.fileno())
        os.fchmod(stream.fileno(), mode)


def ssh_public_digest(path, kind, deadline):
    # Native OpenSSH parses private keys. Only a public-key digest leaves this
    # helper; key contents and native diagnostics never enter a receipt/log.
    result = subprocess.run(['ssh-keygen', '-y', '-P', '', '-f', str(path)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=min(10, data.remaining(deadline)))
    fields = result.stdout.split()
    algorithms = {'rsa': {b'ssh-rsa'}, 'ed25519': {b'ssh-ed25519'},
                  'ecdsa': {b'ecdsa-sha2-nistp256', b'ecdsa-sha2-nistp384', b'ecdsa-sha2-nistp521'}}
    if result.returncode or not 2 <= len(fields) or len(result.stdout) > 16384 or fields[0] not in algorithms[kind]:
        raise PersonalizeError('retained SSH host key is invalid, encrypted, or has the wrong algorithm')
    return hashlib.sha256(b' '.join(fields[:2])).hexdigest()


def package_set():
    result = {}
    for block in regular(ROOT, 'lib/apk/db/installed', 16 * 1024 * 1024).read_text().strip().split('\n\n'):
        fields = {}
        for line in block.splitlines():
            if line.startswith(('P:', 'V:')):
                if line[:1] in fields:
                    raise PersonalizeError('installed package identity is ambiguous')
                fields[line[0]] = line[2:]
        if set(fields) != {'P', 'V'} or fields['P'] in result:
            raise PersonalizeError('installed package database is incomplete or duplicated')
        result[fields['P']] = fields['V']
    return result


def account_files(request):
    runtime = request['runtime']
    result = {}
    tables = {}
    for name, count in (('passwd', 7), ('group', 4), ('shadow', 9)):
        rows = [v.split(':') for v in regular(ROOT, 'etc/' + name, 65536).read_text().splitlines()]
        if (any(len(v) != count for v in rows) or len({v[0] for v in rows}) != len(rows) or
                any(v[0] == 'neo' for v in rows)):
            raise PersonalizeError('generic template accounts are malformed or already personalized')
        tables[name] = rows
    for name, key in (('passwd', 'uid'), ('group', 'gid')):
        try:
            ids = [int(v[2]) for v in tables[name]]
        except ValueError as error:
            raise PersonalizeError('template accounts have a nonnumeric identity') from error
        if runtime[key] in ids or any(start <= identity < start + count
                                     for start, count in runtime['sub' + key] for identity in [*ids, runtime[key]]):
            raise PersonalizeError('runtime or subordinate identity overlaps a template account')
    roots = [v for v in tables['shadow'] if v[0] == 'root']
    wheels = [v for v in tables['group'] if v[0] == 'wheel']
    if len(roots) != 1 or not roots[0][1].startswith(('!', '*')) or len(wheels) != 1:
        raise PersonalizeError('template must have a locked root account and one wheel group')
    wheels[0][3] = ','.join(filter(None, [wheels[0][3], 'neo']))
    tables['passwd'].append(['neo', 'x', str(runtime['uid']), str(runtime['gid']), 'Platform runtime', '/home/neo', '/bin/ash'])
    tables['group'].append(['neo', 'x', str(runtime['gid']), ''])
    tables['shadow'].append(['neo', request['admin_password_hash'], str(int(time.time()) // 86400), '0', '99999', '7', '', '', ''])
    for name, rows in tables.items():
        result['etc/' + name] = ('\n'.join(':'.join(v) for v in rows) + '\n', 0o600 if name == 'shadow' else 0o644)
    for name in ('subuid', 'subgid'):
        path = data.below(ROOT, 'etc/' + name)
        if path.exists() and regular(ROOT, 'etc/' + name, 65536).read_text().strip():
            raise PersonalizeError('generic template has undeclared subordinate identities')
        result['etc/' + name] = (''.join('neo:' + str(start) + ':' + str(count) + '\n'
                                       for start, count in runtime[name]), 0o644)
    return result


def personalize(request, deadline):
    validate(request)
    if data.remaining(deadline) > 1800:
        raise PersonalizeError('personalization must fit within the remaining 30-minute budget')
    mounted(request)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PersonalizeError('template marker has duplicate fields')
            result[key] = value
        return result
    marker = json.loads(regular(ROOT, 'etc/klokast-template.json').read_bytes(), object_pairs_hook=unique)
    if marker != {'kind': 'klokast.vm-template-marker.v1', 'engine_commit': request['engine_commit'],
                  'profile': 'shared-alpine-v1', 'inputs_sha256': request['inputs_sha256']}:
        raise PersonalizeError('cloned template provenance differs from the request')
    if package_set() != request['packages']:
        raise PersonalizeError('cloned template packages differ from the frozen set')
    retained = data.read_record(regular(RETAINED, '.klokast-retained-identity.json'))
    if retained != {'kind': 'klokast.vm-retained-identity.v1', 'runtime': request['runtime']}:
        raise PersonalizeError('retained numeric identity does not match personalization')
    identity = regular(RETAINED, IDENTITY, data.MAX_IDENTITY_BYTES)
    if identity.stat().st_size == 0 or stat.S_IMODE(identity.stat().st_mode) != 0o600:
        raise PersonalizeError('retained Tailscale identity is absent or not private')
    final = data.read_record(regular(RETAINED, '.klokast-final-result.json'))
    if (not isinstance(final, dict) or set(final) != {'kind', 'request_sha256', 'stage_receipt_sha256',
            'entries', 'copy_verified', 'adoption_accepted', 'receipt_sha256'} or
            final['kind'] not in {'klokast.vm-retained-final-result.v2', 'klokast.vm-retained-final-result.v3'} or
            final['copy_verified'] is not True or final['adoption_accepted'] is not False or
            final['receipt_sha256'] != request['retained_receipt_sha256'] or
            data.digest({k: v for k, v in final.items() if k != 'receipt_sha256'}) != request['retained_receipt_sha256'] or
            not isinstance(final['entries'], dict) or final['entries'].get(IDENTITY) != data.tree(identity, deadline) or
            any((RETAINED / name).exists() or (RETAINED / name).is_symlink()
                for name in ('.klokast-stage-pending', '.klokast-final-pending', '.klokast-copy-pending'))):
        raise PersonalizeError('retained identity differs from its completed final-sync receipt')
    claim = ROOT / '.klokast-personalize-pending'
    receipt_path = ROOT / 'etc/klokast-personalization.json'
    if claim.exists() or claim.is_symlink() or receipt_path.exists() or receipt_path.is_symlink():
        raise PersonalizeError('clone is already personalized or has an interrupted attempt')
    ssh_files, ssh_public = {}, {}
    for key, relative in data.SSH_IDENTITIES.items():
        path = regular(RETAINED, key, 65536)
        if (not path.stat().st_size or stat.S_IMODE(path.stat().st_mode) != 0o600 or
                final['entries'].get(key) != data.tree(path, deadline)):
            raise PersonalizeError('retained SSH host key differs from its private final-sync receipt')
        ssh_public[key] = ssh_public_digest(path, key.removeprefix('platform-ssh-'), deadline)
        # A generic template must never supply a machine's host key.
        destination = data.below(ROOT, relative)
        if destination.exists() or destination.is_symlink():
            raise PersonalizeError('generic template contains an SSH host key')
        ssh_files[relative] = (path.read_bytes(), 0o600)
    accounts = account_files(request)
    home = data.below(ROOT, 'home/neo')
    if home.exists() or home.is_symlink() or any(data.below(ROOT, 'var/lib/tailscale').iterdir()):
        raise PersonalizeError('generic template contains a machine home or Tailscale state')
    # The failed clone remains poisoned. Never resume partially written accounts
    # or reuse a disk after an interrupted personalization.
    data.create_record(claim, {'operation_id': request['operation_id'], 'request_sha256': data.digest(request)})
    data.run(['sync', '-f', str(ROOT)], deadline)
    files = {k: (v, FILES[k]) for k, v in request['files'].items()}
    files.update(accounts)
    files.update(ssh_files)
    files.update({
        'etc/fstab': ('/dev/xvda / ext4 defaults 0 1\nUUID=' + request['retained_uuid'] + ' /srv/retained ext4 defaults 0 2\n', 0o644),
        'etc/conf.d/tailscale': ('no_logs_no_support=yes\ncommand_args="--state=/srv/retained/' + IDENTITY + '"\nrc_need="localmount nftables"\n', 0o644),
        'etc/doas.d/doas.conf': ('permit nopass :wheel\n', 0o600),
        'etc/klokast/app-resources/vm-input.d/000-empty.nft': ('# Empty placeholder so nft include globs always match.\n', 0o644),
    })
    for relative, (content, mode) in files.items():
        data.remaining(deadline); put(relative, content, mode)
    for relative, mode in (('home/neo', 0o700), ('srv/retained', 0o700)):
        path = data.below(ROOT, relative)
        path.mkdir(mode=mode, parents=True, exist_ok=False)
    os.chown(home, request['runtime']['uid'], request['runtime']['gid'])
    for service in ('networking', 'nftables', 'tailscale', 'klokast-podman-runroot-cleanup'):
        if not regular(ROOT, 'etc/init.d/' + service).stat().st_mode & 0o100:
            raise PersonalizeError('personalized boot service is not executable')
        directory = data.below(ROOT, 'etc/runlevels/default')
        directory.mkdir(mode=0o755, exist_ok=True)
        link = directory / service
        if link.exists() or link.is_symlink():
            raise PersonalizeError('generic template already enables a personalized service')
        link.symlink_to('/etc/init.d/' + service)
    if package_set() != request['packages'] or data.runtime_identity(ROOT) != request['runtime']:
        raise PersonalizeError('personalization changed packages or numeric identity')
    mounted(request)
    receipt = {'kind': 'klokast.vm-personalization-result.v2', 'operation_id': request['operation_id'],
               'request_sha256': data.digest(request), 'release_sha256': request['release_sha256'],
               'inputs_sha256': request['inputs_sha256'], 'engine_commit': request['engine_commit'],
               'profile': 'shared-alpine-v1', 'hostname': request['box'] + '-' + request['role'],
               'root_uuid': request['root_uuid'], 'retained_uuid': request['retained_uuid'],
               'retained_receipt_sha256': request['retained_receipt_sha256'],
               'runtime': request['runtime'], 'files': {k: {'sha256': hashlib.sha256(regular(ROOT, k).read_bytes()).hexdigest(),
                                                          'mode': mode} for k, (_content, mode) in files.items()},
               'ssh_public_sha256': ssh_public,
               'packages_unchanged': True, 'adoption_accepted': False}
    data.create_record(receipt_path, receipt)
    data.run(['sync', '-f', str(ROOT)], deadline)
    claim.unlink()
    data.run(['sync', '-f', str(ROOT)], deadline)
    return receipt
