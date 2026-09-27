"""Retire first-contact packages on a new router clone inside networkless Xen.

Native APK removes packages using the frozen installed database. No repository
refresh, added package, identity copy, enrollment, or service start is permitted.
The resulting package set is build evidence for subsequent runtime verification.
"""
from pathlib import Path
import os
import stat
import subprocess

import router_personalize

BOOTSTRAP_PACKAGES = frozenset({
    'openssh', 'openssh-client-common', 'openssh-client-default',
    'openssh-server', 'openssh-server-common', 'openssh-server-common-openrc',
    'openssh-sftp-server', 'libedit',
})
FORBIDDEN_PACKAGES = BOOTSTRAP_PACKAGES - {'libedit'}
SERVER_PATHS = ('usr/sbin/sshd', 'usr/lib/ssh/sshd-session', 'usr/lib/ssh/sshd-auth',
                'etc/init.d/sshd', 'etc/runlevels/default/sshd')


def validate_packages(before, after, world):
    if (not isinstance(before, dict) or not isinstance(after, dict) or not after or
            not isinstance(world, list) or 'openssh' not in world or
            not set(world) <= before.keys() or
            not (set(world) - {'openssh'}) <= after.keys() or
            not after.keys() <= before.keys() or
            any(after[name] != before[name] for name in after) or
            not (before.keys() - after.keys()) <= BOOTSTRAP_PACKAGES or
            FORBIDDEN_PACKAGES & after.keys()):
        raise ValueError('router finalization changed a frozen runtime package or retained first-contact OpenSSH')


def finalize(root, manifest):
    router_personalize.environment()
    root = Path(root)
    before = {p['name']: p['version'] for p in manifest['packages']}
    if router_personalize.packages(root) != before:
        raise ValueError('router finalization requires the exact generic package manifest')
    expected_world = '\n'.join(name + '=' + before[name] for name in manifest['world']) + '\n'
    if router_personalize.regular(root, 'etc/apk/world').read_text() != expected_world:
        raise ValueError('router finalization requires the frozen generic world')
    # This helper runs before state copy. Bootstrap retirement after enrollment
    # uses the normal role and must independently match this qualified output.
    for relative in ('root/.ssh/authorized_keys', 'var/lib/tailscale/tailscaled.state',
                     'var/lib/dhcpcd/duid', 'var/lib/dhcpcd/secret', 'var/lib/misc/dnsmasq.leases'):
        path = root / relative
        if path.exists() or path.is_symlink():
            raise ValueError('router finalization refuses an existing machine identity or first-contact key')
    command = ['apk', '--no-network', '--no-cache', 'del', 'openssh']
    if root != Path('/'):
        command = ['chroot', str(root), *command]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=120)
    if result.returncode:
        raise ValueError('native APK could not retire router first-contact packages without network access')
    after = router_personalize.packages(root)
    validate_packages(before, after, manifest['world'])
    for relative in SERVER_PATHS:
        path = root / relative
        if path.exists() or path.is_symlink():
            raise ValueError('router first-contact server path remains after finalization: ' + relative)
    # APK can remove its empty SSH directory along with the bootstrap packages.
    # The fixed state-copy primitive requires prepared parents; it never creates
    # arbitrary directories while parsing source state.
    router_personalize.directory(root, 'etc/ssh', create=True)
    parent = router_personalize.service_directory(root, 'var/lib/tailscale')
    fallback = parent / 'ssh'
    if not fallback.exists() and not fallback.is_symlink():
        fallback.mkdir(mode=0o700)
    info = fallback.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_dev != root.stat().st_dev or stat.S_IMODE(info.st_mode) != 0o700 or any(fallback.iterdir())):
        raise ValueError('router finalization requires an empty private SSH fallback directory')
    world = '\n'.join(name + '=' + after[name] for name in manifest['world'] if name != 'openssh') + '\n'
    if router_personalize.regular(root, 'etc/apk/world').read_text() != world:
        raise ValueError('native APK did not preserve the exact frozen runtime world')
    roots = [row.split(':') for row in router_personalize.regular(root, 'etc/shadow').read_text().splitlines()
             if row.startswith('root:')]
    if len(roots) != 1 or not roots[0][1].startswith(('!', '*')):
        raise ValueError('finalized router root password is not locked')
    return {'kind': 'klokast.router-finalization.v1', 'packages': after,
            'removed_packages': sorted(before.keys() - after.keys()),
            'tests': dict.fromkeys(('frozen_packages', 'no_openssh_server', 'locked_root', 'pinned_world'), True)}
