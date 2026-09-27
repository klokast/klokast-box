"""Render approved router configuration onto a new template clone inside Xen.

The caller verifies the clone and configuration provenance before mounting it.
This primitive neither grants authority nor selects disks, starts services,
enrolls an identity, reads an old OS, or installs packages.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

FILES = {
    'etc/hostname': 0o644, 'etc/hosts': 0o644,
    'etc/network/interfaces': 0o644, 'etc/resolv.conf': 0o644,
    'etc/dhcpcd.conf': 0o644, 'etc/dnsmasq.conf': 0o644,
    'etc/nftables.nft': 0o644, 'etc/sysctl.conf': 0o644,
    'etc/klokast/overlay-ipv6.nft': 0o600,
    'etc/klokast/app-resources/router-forward.nft': 0o644,
    'etc/klokast/app-resources/router-forward.d/000-empty.nft': 0o644,
}
SERVICES = ('networking', 'dhcpcd', 'dnsmasq', 'nftables', 'tailscale')


def file_modes(files):
    """Only the common core and keyed compiler-owned firewall includes are writable."""
    if not isinstance(files, dict) or not set(FILES) <= files.keys() or len(files) > 1033:
        raise ValueError('router personalization lacks its fixed core configuration')
    modes = dict(FILES)
    for name, content in files.items():
        if name not in modes:
            if not isinstance(name, str) or not re.fullmatch(
                    r'etc/klokast/app-resources/router-forward.d/[A-Za-z0-9_-]+\.nft', name):
                raise ValueError('router personalization has an unsupported configuration path')
            modes[name] = 0o644
        if not isinstance(content, str) or not content or '\0' in content or len(content.encode()) > 128 * 1024:
            raise ValueError('router personalization has an invalid configuration file')
    if sum(len(v.encode()) for v in files.values()) > 768 * 1024:
        raise ValueError('router personalization configuration exceeds its bound')
    return modes


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def validate(value):
    fields = {'kind', 'box', 'role', 'inputs_sha256', 'files', 'packages'}
    if (not isinstance(value, dict) or set(value) != fields or
            value['kind'] != 'klokast.router-personalization.v1' or value['role'] != 'router' or
            not isinstance(value['box'], str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', value['box']) or
            not isinstance(value['inputs_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['inputs_sha256'])):
        raise ValueError('router personalization has an unknown target or input identity')
    files = value['files']
    file_modes(files)
    if files['etc/hostname'] != value['box'] + '-router\n':
        raise ValueError('router personalization requires the exact bounded configuration and hostname')
    # This first adapter cannot reconstruct an enabled overlay repair or take
    # arbitrary extra files as desired state. The compiler must own expansion.
    if files['etc/klokast/overlay-ipv6.nft'] != '# Ops IPv6 downstream is disabled.\n':
        raise ValueError('router personalization does not support the overlay IPv6 repair')
    if re.findall(r'^\s*dhcp-leasefile\s*=\s*(\S+)\s*$', files['etc/dnsmasq.conf'], re.M) != [
            '/var/lib/misc/dnsmasq.leases']:
        raise ValueError('router personalization must use the fixed retained lease path')
    packages = value['packages']
    if (not isinstance(packages, dict) or not 1 <= len(packages) <= 512 or
            not {'linux-virt', 'tailscale', 'dhcpcd', 'dnsmasq', 'nftables'} <= packages.keys() or
            any(not isinstance(k, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9+_.-]*', k) or
                not isinstance(v, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9+_.~-]*', v)
                for k, v in packages.items())):
        raise ValueError('router personalization requires its complete frozen package manifest')


def directory(root, relative, *, create=False, service_owner=None):
    path = Path(root)
    device = path.stat().st_dev
    for part in ('.', *Path(relative).parts):
        if part == '..' or part.startswith('/'):
            raise ValueError('router configuration path escapes the new root')
        path = path / part
        if create and not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o755)
        info = path.lstat()
        owned = info.st_uid == os.geteuid() or (
            path == Path(root) / relative and service_owner == (info.st_uid, info.st_gid))
        if (not stat.S_ISDIR(info.st_mode) or not owned or
                info.st_mode & 0o022 or info.st_dev != device):
            raise ValueError('router configuration parent is unsafe: ' + str(path) +
                             ' (uid=' + str(info.st_uid) + ', mode=' + oct(stat.S_IMODE(info.st_mode)) + ')')
    return path


def service_directory(root, relative):
    services = {'var/lib/tailscale': 'tailscale', 'var/lib/dhcpcd': 'dhcpcd'}
    rows = [line.split(':') for line in regular(root, 'etc/passwd').read_text().splitlines()]
    selected = [row for row in rows if len(row) == 7 and row[0] == services[relative]]
    if len(selected) != 1 or any(not value.isdecimal() or not 1 <= int(value) < 1000
                                  for value in selected[0][2:4]):
        raise ValueError('router template lacks the declared service account: ' + services[relative])
    return directory(root, relative, create=True, service_owner=tuple(map(int, selected[0][2:4])))


def regular(root, relative):
    directory(root, str(Path(relative).parent))
    path = Path(root) / relative
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or
            info.st_mode & 0o022 or info.st_dev != Path(root).stat().st_dev or info.st_size > 16 * 1024 * 1024):
        raise ValueError('router configuration file is unsafe: ' + relative)
    return path


def put(root, relative, content, mode):
    parent = directory(root, str(Path(relative).parent), create=True)
    target = Path(root) / relative
    if target.exists() or target.is_symlink():
        regular(root, relative)
    descriptor, name = tempfile.mkstemp(prefix='.router-', dir=parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content.encode())
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def packages(root):
    result = {}
    for block in regular(root, 'lib/apk/db/installed').read_text().strip().split('\n\n'):
        values = [line.split(':', 1) for line in block.splitlines() if line.startswith(('P:', 'V:'))]
        fields = dict(values)
        if len(values) != 2 or set(fields) != {'P', 'V'} or fields['P'] in result:
            raise ValueError('router package database is incomplete or ambiguous')
        result[fields['P']] = fields['V']
    return result


def account_files(root):
    result, tables = {}, {}
    for name, count in (('passwd', 7), ('group', 4), ('shadow', 9)):
        rows = [line.split(':') for line in regular(root, 'etc/' + name).read_text().splitlines()]
        if (any(len(row) != count for row in rows) or len({row[0] for row in rows}) != len(rows) or
                any(row[0] == 'neo' for row in rows)):
            raise ValueError('router clone already has a management account or malformed account table')
        tables[name] = rows
    for name in ('passwd', 'group'):
        if any(int(row[2]) == 1000 for row in tables[name]):
            raise ValueError('router template reserves the management UID and GID')
    roots = [row for row in tables['shadow'] if row[0] == 'root']
    wheels = [row for row in tables['group'] if row[0] == 'wheel']
    if len(roots) != 1 or not roots[0][1].startswith(('!', '*')) or len(wheels) != 1:
        raise ValueError('router template needs a locked root and one wheel group')
    wheels[0][3] = ','.join(filter(None, [wheels[0][3], 'neo']))
    tables['passwd'].append(['neo', 'x', '1000', '1000', 'Platform administrator', '/home/neo', '/bin/ash'])
    tables['group'].append(['neo', 'x', '1000', ''])
    tables['shadow'].append(['neo', '!', '0', '0', '99999', '7', '', '', ''])
    for name, rows in tables.items():
        result['etc/' + name] = ('\n'.join(':'.join(row) for row in rows) + '\n',
                                 0o600 if name == 'shadow' else 0o644)
    return result


def environment():
    if (os.geteuid() != 0 or Path('/sys/hypervisor/type').read_text().strip() != 'xen' or
            Path('/sys/hypervisor/uuid').read_text().strip() == '00000000-0000-0000-0000-000000000000' or
            {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'}):
        raise ValueError('router personalization requires a networkless Xen guest, never dom0')


def personalize(root, request):
    """Called only on a verified new clone inside the networkless job boundary."""
    environment()
    root = Path(root)
    validate(request)
    marker = json.loads(regular(root, 'etc/klokast-router-inputs.json').read_text())
    if (marker.get('profile') != 'router-alpine-v1' or
            marker.get('inputs_sha256') != request['inputs_sha256'] or
            digest({k: v for k, v in marker.items() if k != 'inputs_sha256'}) != request['inputs_sha256'] or
            {p['name']: p['version'] for p in marker['packages']} != request['packages'] or
            packages(root) != request['packages'] or
            regular(root, 'etc/hostname').read_text() != 'klokast-router-template\n'):
        raise ValueError('router clone differs from its generic input and package identity')
    for relative in ('root/.ssh', 'var/lib/misc/dnsmasq.leases', 'etc/machine-id',
                     'var/lib/dbus/machine-id', 'etc/sysctl.d/91-klokast-ops-ipv6.conf'):
        directory(root, str(Path(relative).parent), create=True)
        path = root / relative
        if path.exists() or path.is_symlink():
            raise ValueError('router clone contains existing machine or service state')
    for relative in ('var/lib/tailscale', 'var/lib/dhcpcd'):
        path = service_directory(root, relative)
        if any(path.iterdir()):
            raise ValueError('router clone contains existing service identity')
    if list(directory(root, 'etc/ssh').glob('ssh_host_*')):
        raise ValueError('router clone contains SSH host identity')
    accounts = account_files(root)
    qualification_hook = regular(root, 'usr/local/libexec/router-template-test')
    for name in SERVICES:
        regular(root, 'etc/init.d/' + name)
    level = directory(root, 'etc/runlevels/default', create=True)
    if any(level.iterdir()):
        raise ValueError('router clone has unexpected default services')
    modes = file_modes(request['files'])
    for relative, content in request['files'].items():
        put(root, relative, content, modes[relative])
    for relative, (content, mode) in accounts.items():
        put(root, relative, content, mode)
    put(root, 'etc/doas.d/20-klokast.conf', 'permit nopass :wheel\n', 0o600)
    home = directory(root, 'home/neo', create=True)
    home.chmod(0o700)
    os.chown(home, 1000, 1000)
    directory(root, 'etc/dnsmasq.d', create=True)
    for name in SERVICES:
        (level / name).symlink_to('/etc/init.d/' + name)
    qualification_hook.unlink()
    if packages(root) != request['packages']:
        raise ValueError('router personalization changed the frozen packages')
    return {'kind': 'klokast.router-personalization-result.v1', 'box': request['box'], 'role': 'router',
            'configuration_sha256': digest(request), 'inputs_sha256': request['inputs_sha256'],
            'files': {k: hashlib.sha256(regular(root, k).read_bytes()).hexdigest() for k in modes},
            'packages_unchanged': True, 'identity_copied': False, 'replacement_authorized': False}
