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


def finalize(root, manifest, *, enrolled_accounts=None, runtime_packages=None, enrolled_state_sha256=None):
    """Remove first-contact tools offline, optionally preserving enrolled state.

    The caller must stop the initial router and prove management access before
    removing its recorded first-contact key. Its durable bootstrap record must
    bind the retained state checksum before this helper's first invocation.
    This helper refuses any remaining
    key, runs only in networkless Xen, and never enrolls or starts a service.
    """
    router_personalize.environment()
    root = Path(root)
    before = {p['name']: p['version'] for p in manifest['packages']}
    retained = None
    if enrolled_accounts is not None:
        import router_state
        if (not isinstance(enrolled_accounts, dict) or
                set(enrolled_accounts) != {'dnsmasq_uid', 'dnsmasq_gid', 'tailscale_gid'} or
                any(type(v) is not int or not 1 <= v <= 65535 for v in enrolled_accounts.values())):
            raise ValueError('router finalization requires exact enrolled service accounts')
        validate_packages(before, runtime_packages, manifest['world'])
        for relative in ('var/lib/tailscale/tka', 'var/lib/tailscale/tpm-sealed'):
            path = root / relative
            if path.exists() or path.is_symlink():
                raise ValueError('router finalization refuses unsupported enrolled state')
        retained = router_state.evidence(router_state.enrolled_snapshot(root, **enrolled_accounts))
        if router_personalize.digest(retained) != enrolled_state_sha256:
            raise ValueError('router finalization enrolled state differs from the recorded bootstrap state')
    elif runtime_packages is not None or enrolled_state_sha256 is not None:
        raise ValueError('router finalization runtime selection requires enrolled state evidence')
    installed = router_personalize.packages(root)
    resumed = retained is not None and installed == runtime_packages
    if installed != before and not resumed:
        raise ValueError('router finalization requires the exact generic package manifest')
    expected_world = '\n'.join(name + '=' + before[name] for name in manifest['world']
                               if not resumed or name != 'openssh') + '\n'
    if router_personalize.regular(root, 'etc/apk/world').read_text() != expected_world:
        raise ValueError('router finalization requires the frozen generic world')
    absent = ('root/.ssh/authorized_keys',)
    if retained is None:
        absent += ('var/lib/tailscale/tailscaled.state',
                     'var/lib/dhcpcd/duid', 'var/lib/dhcpcd/secret', 'var/lib/misc/dnsmasq.leases')
    for relative in absent:
        path = root / relative
        if path.exists() or path.is_symlink():
            raise ValueError('router finalization refuses an existing machine identity or first-contact key')
    command = ['apk', '--no-network', '--no-cache', 'del', 'openssh']
    if root != Path('/'):
        command = ['chroot', str(root), *command]
    if not resumed:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=120)
        if result.returncode:
            raise ValueError('native APK could not retire router first-contact packages without network access')
    after = router_personalize.packages(root)
    validate_packages(before, after, manifest['world'])
    if runtime_packages is not None and after != runtime_packages:
        raise ValueError('router finalization differs from the qualified runtime manifest')
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
            info.st_dev != root.stat().st_dev or stat.S_IMODE(info.st_mode) != 0o700 or
            (retained is None and any(fallback.iterdir()))):
        raise ValueError('router finalization requires an empty private SSH fallback directory')
    world = '\n'.join(name + '=' + after[name] for name in manifest['world'] if name != 'openssh') + '\n'
    if router_personalize.regular(root, 'etc/apk/world').read_text() != world:
        raise ValueError('native APK did not preserve the exact frozen runtime world')
    roots = [row.split(':') for row in router_personalize.regular(root, 'etc/shadow').read_text().splitlines()
             if row.startswith('root:')]
    if len(roots) != 1 or not roots[0][1].startswith(('!', '*')):
        raise ValueError('finalized router root password is not locked')
    if retained is not None and router_state.evidence(router_state.enrolled_snapshot(root, **enrolled_accounts)) != retained:
        raise ValueError('router finalization changed enrolled identity or lease state; keep the router stopped')
    result = {'kind': 'klokast.router-finalization.v1', 'packages': after,
            'removed_packages': sorted(before.keys() - after.keys()),
            'tests': dict.fromkeys(('frozen_packages', 'no_openssh_server', 'locked_root', 'pinned_world'), True)}
    if retained is not None:
        result['enrolled_state_preserved'] = True
    return result
