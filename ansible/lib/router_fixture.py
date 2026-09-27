"""Remove identity from a disposable legacy copy before any router code boots.

This is test preparation, never a production-state migration. The dom0 caller
must supply a private block copy of a recorded snapshot, with no production VIF.
Filesystem parsing and cleanup stay inside the disposable networkless Xen VM.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import router_personalize as personalizer


def clear_directory(root, relative):
    """Bounded deletion in only a fixed service directory on the disposable copy."""
    if relative not in ('var/lib/tailscale', 'var/lib/dhcpcd',
                        'etc/klokast/app-resources', 'etc/dnsmasq.d'):
        raise ValueError('router fixture refuses an unrecognized directory')
    root, path = Path(root), Path(root) / relative
    personalizer.directory(root, str(Path(relative).parent))
    if not path.exists() and not path.is_symlink():
        return
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError('router fixture service root is not a directory')
    device = root.stat().st_dev
    entries = []
    total = 0
    pending = [(path, 0)]
    while pending:
        item, depth = pending.pop()
        info = item.lstat()
        if (info.st_dev != device or depth > 5 or len(entries) >= 1024 or
                info.st_mode & 0o022 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or
                stat.S_ISREG(info.st_mode) and (info.st_nlink != 1 or info.st_size > 8 * 1024 * 1024)):
            raise ValueError('router fixture service tree is unsafe or exceeds its bounds')
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
        if total > 16 * 1024 * 1024:
            raise ValueError('router fixture service data exceeds its bound')
        entries.append((item, stat.S_ISDIR(info.st_mode)))
        if stat.S_ISDIR(info.st_mode):
            for child in item.iterdir():
                if len(pending) + len(entries) >= 1024:
                    raise ValueError('router fixture service tree has too many entries')
                pending.append((child, depth + 1))
    # Validate the entire tree before deleting any part. Never follow links.
    for item, directory in reversed(entries):
        if item == path:
            continue
        item.rmdir() if directory else item.unlink()


def remove(root, relative, *, owner=0):
    permitted = {'var/lib/misc/dnsmasq.leases', 'etc/machine-id', 'var/lib/dbus/machine-id'} | {
        'etc/ssh/ssh_host_' + kind + '_key' + suffix
        for kind in ('rsa', 'ecdsa', 'ed25519') for suffix in ('', '.pub')}
    if relative not in permitted:
        raise ValueError('router fixture refuses an unrecognized identity file')
    path = Path(root) / relative
    if path.exists() or path.is_symlink():
        personalizer.directory(root, str(Path(relative).parent))
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in (0, owner) or
                info.st_mode & 0o022 or info.st_dev != Path(root).stat().st_dev or
                info.st_size > (4 * 1024 * 1024 if relative.endswith('dnsmasq.leases') else 16384)):
            raise ValueError('router fixture identity file is unsafe')
        path.unlink()


def accounts(root):
    result = {}
    for filename, name, fields in (('passwd', 'dnsmasq', (('dnsmasq_uid', 2), ('dnsmasq_gid', 3))),
                                    ('group', 'tailscale', (('tailscale_gid', 2),))):
        path = personalizer.regular(root, 'etc/' + filename)
        if path.stat().st_size > 65536:
            raise ValueError('router fixture account table exceeds its bound')
        rows = [line.split(':') for line in path.read_text().splitlines()]
        rows = [row for row in rows if row[0] == name]
        if len(rows) != 1:
            raise ValueError('router fixture has no unique native service account')
        for field, index in fields:
            if len(rows[0]) <= index or not rows[0][index].isdecimal() or not 1 <= int(rows[0][index]) <= 65535:
                raise ValueError('router fixture native service account is invalid')
            result[field] = int(rows[0][index])
    return result


def sanitize_legacy(root, *, box, expected_packages, expected_files, fixture):
    personalizer.environment()
    personalizer.validate(fixture)
    root = Path(root)
    if (fixture['box'] != 'boxa' or personalizer.regular(root, 'etc/hostname').read_text().strip() != box + '-router' or
            personalizer.packages(root) != expected_packages):
        raise ValueError('legacy router fixture differs from the inspected hostname or packages')
    for relative, checksum in expected_files.items():
        if (relative not in ('etc/network/interfaces', 'etc/dhcpcd.conf', 'etc/dnsmasq.conf', 'etc/nftables.nft',
                             'etc/klokast/app-resources/router-forward.nft') and not re.fullmatch(
                r'etc/klokast/app-resources/router-forward.d/[A-Za-z0-9_-]+\.nft', relative)):
            raise ValueError('legacy router fixture has an unsafe configuration selector')
        if hashlib.sha256(personalizer.regular(root, relative).read_bytes()).hexdigest() != checksum:
            raise ValueError('legacy router configuration changed before fixture preparation')
    for relative in ('var/lib/tailscale/tka', 'var/lib/tailscale/tpm-sealed',
                     'etc/sysctl.d/91-klokast-ops-ipv6.conf', 'root/.ssh/authorized_keys', 'usr/sbin/sshd'):
        if (root / relative).exists() or (root / relative).is_symlink():
            raise ValueError('legacy router fixture has unsupported state or first-contact access')
    service_accounts = accounts(root)
    for relative in ('var/lib/tailscale', 'var/lib/dhcpcd', 'etc/klokast/app-resources', 'etc/dnsmasq.d'):
        clear_directory(root, relative)
    for relative in ('var/lib/misc/dnsmasq.leases', 'etc/machine-id', 'var/lib/dbus/machine-id'):
        remove(root, relative, owner=service_accounts['dnsmasq_uid'] if relative.endswith('dnsmasq.leases') else 0)
    for kind in ('rsa', 'ecdsa', 'ed25519'):
        for suffix in ('', '.pub'):
            remove(root, 'etc/ssh/ssh_host_' + kind + '_key' + suffix)
    if list((root / 'etc/ssh').glob('ssh_host_*')):
        raise ValueError('legacy router fixture has an unsupported SSH host key')
    modes = personalizer.file_modes(fixture['files'])
    for relative, content in fixture['files'].items():
        personalizer.put(root, relative, content, modes[relative])
    # A custom init runs the test. No production runlevel or local hook runs.
    if personalizer.packages(root) != expected_packages:
        raise ValueError('legacy fixture preparation changed the inspected software')
    return {'packages': expected_packages, 'accounts': service_accounts,
            'production_state_removed': True, 'configuration_sha256': personalizer.digest(fixture['files'])}


def install_probe(root, request, modules, entry):
    root = Path(root)
    target = root / 'usr/local/lib/klokast/router-probe'
    if target.exists() or target.is_symlink():
        raise ValueError('router fixture already has a compatibility probe')
    for name, source in modules.items():
        if name not in ('router_service_probe.py', 'router_state.py', 'router_personalize.py',
                        'router_fixture.py', 'router_compatibility.py', 'router_finalize.py'):
            raise ValueError('router fixture probe module is outside the fixed set')
        personalizer.put(root, 'usr/local/lib/klokast/router-probe/' + name, Path(source).read_text(), 0o600)
    personalizer.put(root, 'usr/local/libexec/router-compatibility-guest', Path(entry).read_text(), 0o700)
    personalizer.put(root, 'etc/klokast-router-compatibility.json', json.dumps(request, sort_keys=True) + '\n', 0o600)
