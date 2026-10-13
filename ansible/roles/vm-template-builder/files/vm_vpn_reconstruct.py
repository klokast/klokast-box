"""VPN identity preservation on a fresh image inside the isolated finalizer."""
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil

import vm_infrastructure_personalize as personalize

IDENTITY = ('var/lib/tailscale/tailscaled.state', 'etc/machine-id',
            'etc/ssh/ssh_host_ed25519_key', 'etc/ssh/ssh_host_ed25519_key.pub',
            'etc/ssh/ssh_host_rsa_key', 'etc/ssh/ssh_host_rsa_key.pub',
            'etc/ssh/ssh_host_ecdsa_key', 'etc/ssh/ssh_host_ecdsa_key.pub')
REQUIRED = {'var/lib/tailscale/tailscaled.state',
            'etc/ssh/ssh_host_ed25519_key', 'etc/ssh/ssh_host_ed25519_key.pub'}


def preserve_identity(source, target):
    copied = {}
    for name in IDENTITY:
        old = source/name
        if not old.exists() and not old.is_symlink():
            if name in REQUIRED: raise RuntimeError('required VPN identity is absent: '+name)
            if name == 'etc/machine-id':
                # Absence is recorded explicitly; do not invent a legacy identity.
                new=target/name
                if new.is_symlink():raise RuntimeError('new machine ID path is a link')
                new.unlink(missing_ok=True)
            continue
        if old.is_symlink() or not old.is_file() or not old.resolve().is_relative_to(source):
            raise RuntimeError('VPN identity has an unsafe path: '+name)
        new = target/name
        new.parent.mkdir(parents=True, exist_ok=True)
        if new.is_symlink(): raise RuntimeError('new image identity path is a link')
        shutil.copy2(old, new)
        os.chown(new, old.stat().st_uid, old.stat().st_gid)
        checksum = hashlib.sha256(old.read_bytes()).hexdigest()
        if hashlib.sha256(new.read_bytes()).hexdigest() != checksum:
            raise RuntimeError('VPN identity copy failed verification')
        copied[name] = checksum
    return copied


def service_account(uid, gid):
    # Check every retained numeric identity before any mutation.
    for lookup, key in ((pwd.getpwuid, uid), (pwd.getpwnam, 'vpn-egress')):
        try: user = lookup(key)
        except KeyError: continue
        if (user.pw_name, user.pw_uid, user.pw_gid) != ('vpn-egress', uid, gid):
            raise RuntimeError('fresh image reuses the VPN numeric account identity')
    for lookup, key in ((grp.getgrgid, gid), (grp.getgrnam, 'vpn-egress')):
        try: group = lookup(key)
        except KeyError: continue
        if (group.gr_name, group.gr_gid) != ('vpn-egress', gid):
            raise RuntimeError('fresh image reuses the VPN numeric group identity')
    try: grp.getgrnam('vpn-egress')
    except KeyError: personalize.run(['addgroup', '-g', str(gid), 'vpn-egress'])
    try: pwd.getpwnam('vpn-egress')
    except KeyError:
        personalize.run(['adduser', '-D', '-H', '-u', str(uid), '-G', 'vpn-egress',
                         '-h', '/var/lib/vpn-egress', '-s', '/sbin/nologin', 'vpn-egress'])


def reconstruct(source, value):
    config = personalize.validate(value['vpn_creation'])
    if config['role'] != 'vpn-egress' or config['box'] != value['box']:
        raise RuntimeError('VPN personalization selects another target')
    users = {parts[0]:parts for line in (source/'etc/passwd').read_text().splitlines()
             if len(parts := line.split(':')) == 7}
    groups = {parts[0]:parts for line in (source/'etc/group').read_text().splitlines()
              if len(parts := line.split(':')) == 4}
    for name, numbers in value['accounts'].items():
        if users.get(name, [])[2:4] != [str(v) for v in numbers] or groups.get(name, [])[2:3] != [str(numbers[1])]:
            raise RuntimeError('VPN account ownership changed after preflight')
    machine=source/'etc/machine-id'
    actual=hashlib.sha256(machine.read_bytes()).hexdigest() if machine.exists() else None
    if actual!=value['vpn_machine_id_sha256']:
        raise RuntimeError('VPN machine ID presence or content changed after preflight')
    service_account(*value['accounts']['vpn-egress'])
    personalize.finalize(config, hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())
    copied = preserve_identity(source, Path('/'))
    # The retained Tailscale identity is the maintenance path after reconstruction.
    personalize.run(['rc-update', 'del', 'sshd', 'default'])
    Path('/etc/klokast-service-state.json').write_text(json.dumps({
        'kind': 'klokast.vm-service-state.v1', 'box': value['box'], 'role': value['role'],
        'copy_verified': True, 'source_files': value['source_files']}, sort_keys=True)+'\n')
    Path('/etc/klokast-service-state.json').chmod(0o600)
    return copied
