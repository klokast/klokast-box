"""VPN identity preservation on a fresh image inside the isolated finalizer."""
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil

import vm_infrastructure_personalize as personalize

from vm_guest_identity import preserve_identity


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
