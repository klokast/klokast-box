"""Install and retire exact first-contact access on a fresh router clone."""
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

import router_candidate
import router_finalize
import router_personalize as personalize
import router_state


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def regular(root, relative, *, maximum=8192):
    path = Path(root) / relative
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or
            info.st_dev != Path(root).stat().st_dev or info.st_size > maximum):
        raise ValueError('router first-contact file is unsafe: ' + relative)
    return path


def first_contact_interfaces(address, prefix):
    if type(prefix) is not int or not 1 <= prefix <= 32:
        raise ValueError('router first-contact backend prefix is invalid')
    try:
        interface = ipaddress.IPv4Interface(str(address) + '/' + str(prefix))
    except ValueError as error:
        raise ValueError('router first-contact backend address is invalid') from error
    return ('auto lo\niface lo inet loopback\n\n'
            'auto eth0\niface eth0 inet dhcp\n\n'
            'auto eth3\niface eth3 inet static\n'
            '    address ' + str(interface) + '\n')


def first_contact_firewall(rules, source, address, prefix):
    if not isinstance(source, str) or not isinstance(address, str):
        raise ValueError('router first-contact firewall addresses are invalid')
    try:
        network = ipaddress.IPv4Network(str(address) + '/' + str(prefix), strict=False)
        source_ip = ipaddress.IPv4Address(source)
        router_ip = ipaddress.IPv4Address(address)
    except ValueError as error:
        raise ValueError('router first-contact firewall addresses are invalid') from error
    if source_ip not in network or source_ip == router_ip:
        raise ValueError('router first-contact source must be a separate backend host')
    marker = 'klokast-first-contact-ssh'
    opening = '    chain input {\n'
    if rules.count(opening) != 1 or marker in rules:
        raise ValueError('router input firewall does not match the approved template')
    rule = ('        ip saddr ' + str(source_ip) + ' ip daddr ' + str(router_ip) +
            ' iifname "eth3" tcp dport 22 accept comment "' + marker + '"\n')
    return rules.replace(opening, opening + rule, 1)


def first_contact_sshd_config(address):
    address = str(ipaddress.IPv4Address(address))
    return ('ListenAddress ' + address + '\n'
            'PasswordAuthentication no\n'
            'KbdInteractiveAuthentication no\n'
            'PubkeyAuthentication yes\n'
            'PermitRootLogin prohibit-password\n'
            'AllowTcpForwarding no\n'
            'AllowAgentForwarding no\n'
            'X11Forwarding no\n'
            'PermitTunnel no\n'
            'PermitTTY no\n'
            'PermitUserEnvironment no\n')


def verify_effective_sshd_config(output, address):
    expected = {
        'passwordauthentication': 'no',
        'kbdinteractiveauthentication': 'no',
        'pubkeyauthentication': 'yes',
        'permitrootlogin': 'prohibit-password',
        'allowtcpforwarding': 'no',
        'allowagentforwarding': 'no',
        'x11forwarding': 'no',
        'permittunnel': 'no',
        'permittty': 'no',
        'permituserenvironment': 'no',
    }
    actual = {}
    listens = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2:
            actual[fields[0].lower()] = fields[1].lower()
        if len(fields) == 2 and fields[0].lower() == 'listenaddress':
            listens.add(fields[1].lower())
    address = str(ipaddress.IPv4Address(address)).lower()
    if (actual.get('port') != '22' or actual.get('passwordauthentication') != expected['passwordauthentication'] or
            any(actual.get(key) != value for key, value in expected.items()) or
            not listens or not listens <= {address, address + ':22'}):
        raise ValueError('router first-contact effective OpenSSH settings do not restrict backend access')
    return True


def _ssh_directory(root, *, create):
    personalize.directory(root, 'root')
    path = Path(root) / 'root/.ssh'
    if not path.exists() and not path.is_symlink():
        if not create:
            return None
        path.mkdir(mode=0o700)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_dev != Path(root).stat().st_dev or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('router root SSH directory has unsafe ownership or mode')
    return path


def _service_link(root, *, enable):
    level = personalize.directory(root, 'etc/runlevels/default')
    path = level / 'sshd'
    if enable:
        if path.exists() or path.is_symlink():
            raise ValueError('router first-contact SSH service link already exists')
        os.symlink('/etc/init.d/sshd', path)
    elif path.is_symlink():
        if os.readlink(path) != '/etc/init.d/sshd':
            raise ValueError('router first-contact SSH service link changed')
        path.unlink()
    elif path.exists():
        raise ValueError('router first-contact SSH service path is not a symlink')


def _validate_public_key(root, key):
    if (not isinstance(key, str) or not 1 <= len(key.encode()) <= 4096 or
            '\n' in key or '\r' in key or '\0' in key):
        raise ValueError('router first-contact job or approved key is invalid')
    fields = key.split()
    if (len(fields) not in (2, 3) or fields[0] not in
            ('ssh-ed25519', 'ecdsa-sha2-nistp256', 'ssh-rsa') or
            not re.fullmatch('[A-Za-z0-9+/]+={0,2}', fields[1]) or
            len(fields) == 3 and not re.fullmatch(r'[ -~]{1,256}', fields[2])):
        raise ValueError('router first-contact key must be one plain supported SSH public key')
    ssh_directory = personalize.directory(root, 'etc/ssh')
    descriptor, filename = tempfile.mkstemp(prefix='.router-first-contact-', dir=ssh_directory)
    path = Path(filename)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write((key + '\n').encode())
            os.fchmod(stream.fileno(), 0o600)
            stream.flush()
            os.fsync(stream.fileno())
        checked = subprocess.run(['chroot', str(root), '/usr/bin/ssh-keygen', '-lf',
                                  '/etc/ssh/' + path.name, '-E', 'sha256'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if checked.returncode:
            raise ValueError('router first-contact key is not a valid SSH public key')
    finally:
        path.unlink(missing_ok=True)
    return key


def seed(root, *, job, key, personalization, backend_address, backend_prefix):
    """Seed one temporary, backend-only SSH path; install no packages."""
    personalize.environment()
    root = Path(root)
    if (not isinstance(job, dict) or job.get('mode') != 'initial-install' or
            job.get('role') != 'router'):
        raise ValueError('router first-contact job or approved key is invalid')
    personalize.validate(personalization)
    if any(personalization.get(name) != job.get(name)
           for name in ('box', 'role', 'inputs_sha256')):
        raise ValueError('router first-contact personalization differs from its approved job')
    approved_interfaces = personalization['files'].get('etc/network/interfaces')
    if not isinstance(approved_interfaces, str):
        raise ValueError('router first-contact job lacks approved runtime networking')
    current_interfaces = personalize.regular(root, 'etc/network/interfaces')
    if current_interfaces.read_text() != approved_interfaces:
        raise ValueError('router template networking differs from approved personalization')
    router_candidate.manifest(root, job)
    first = job.get('first_contact')
    if (not isinstance(first, dict) or first.get('key') != key or
            first.get('backend_address') != str(backend_address) or
            first.get('backend_prefix') != backend_prefix):
        raise ValueError('router first-contact values differ from the approved candidate job')
    source_address = first.get('backend_source_address')
    nftables = personalization['files'].get('etc/nftables.nft')
    if not isinstance(nftables, str):
        raise ValueError('router first-contact job lacks approved firewall rules')
    temporary_firewall = first_contact_firewall(nftables, source_address, backend_address, backend_prefix)
    sshd_config = first_contact_sshd_config(backend_address)
    interfaces = first_contact_interfaces(backend_address, backend_prefix)
    for relative in ('usr/sbin/sshd', 'etc/init.d/sshd'):
        path = Path(root) / relative
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_uid != os.geteuid() or info.st_dev != Path(root).stat().st_dev):
            raise ValueError('router template lacks safe first-contact OpenSSH; rebuild the template')
    ssh_directory = personalize.directory(root, 'etc/ssh')
    _validate_public_key(root, key)
    sshd_config_directory = personalize.directory(root, 'etc/ssh/sshd_config.d', create=True)
    sshd_config_path = sshd_config_directory / '10-klokast-bootstrap.conf'
    if list(sshd_config_directory.iterdir()) or sshd_config_path.exists() or sshd_config_path.is_symlink():
        raise ValueError('router template already has first-contact SSH configuration')
    ssh_directory = _ssh_directory(root, create=True)
    authorized = ssh_directory / 'authorized_keys'
    if authorized.exists() or authorized.is_symlink() or list(ssh_directory.iterdir()):
        raise ValueError('router template already has first-contact SSH state')
    key_bytes = (key + '\n').encode()
    personalize.put(root, 'root/.ssh/authorized_keys', key + '\n', 0o600)
    personalize.put(root, 'etc/network/interfaces', interfaces, 0o644)
    personalize.put(root, 'etc/nftables.nft', temporary_firewall, 0o644)
    personalize.put(root, 'etc/ssh/sshd_config.d/10-klokast-bootstrap.conf', sshd_config, 0o644)
    _service_link(root, enable=True)
    command = subprocess.run(['chroot', str(root), '/usr/bin/ssh-keygen', '-A'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    if command.returncode:
        raise ValueError('router first-contact host-key generation failed')
    checked = subprocess.run(['chroot', str(root), '/usr/sbin/sshd', '-t'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    if checked.returncode:
        raise ValueError('router first-contact OpenSSH configuration failed its native syntax check')
    checked = subprocess.run(['chroot', str(root), '/usr/sbin/sshd', '-T', '-f', '/etc/ssh/sshd_config'],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    if checked.returncode:
        raise ValueError('router first-contact OpenSSH configuration failed its effective settings check')
    verify_effective_sshd_config(checked.stdout, backend_address)
    checked = subprocess.run(['chroot', str(root), 'nft', '-c', '-f', '/etc/nftables.nft'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    if checked.returncode:
        raise ValueError('router first-contact firewall failed its native syntax check')
    host_keys = {}
    ssh_directory = personalize.directory(root, 'etc/ssh')
    for name in ('rsa', 'ecdsa', 'ed25519'):
        private = ssh_directory / ('ssh_host_' + name + '_key')
        public = ssh_directory / ('ssh_host_' + name + '_key.pub')
        if private.exists() or private.is_symlink():
            info = private.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_dev != Path(root).stat().st_dev or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('router first-contact host key has unsafe metadata')
            public_info = public.lstat()
            if (not stat.S_ISREG(public_info.st_mode) or public_info.st_uid != os.geteuid() or
                    public_info.st_nlink != 1 or public_info.st_dev != Path(root).stat().st_dev or
                    stat.S_IMODE(public_info.st_mode) != 0o644):
                raise ValueError('router first-contact public host key is unsafe')
            host_keys[name] = digest_bytes(public.read_bytes())
    if not host_keys:
        raise ValueError('router first-contact did not create an SSH host identity')
    expected_files = {filename for name in host_keys
                      for filename in ('ssh_host_' + name + '_key', 'ssh_host_' + name + '_key.pub')}
    if {path.name for path in ssh_directory.glob('ssh_host_*')} != expected_files:
        raise ValueError('router first-contact created an undeclared SSH host identity')
    return {'kind':'klokast.router-first-contact.v1',
            'authorized_key_sha256':digest_bytes(key_bytes),
            'interfaces_sha256':digest_bytes(interfaces.encode()),
            'firewall_sha256':digest_bytes(temporary_firewall.encode()),
            'sshd_config_sha256':digest_bytes(sshd_config.encode()),
            'host_key_public_sha256':host_keys}


def retire(root, *, manifest, personalization, runtime_packages, accounts,
           enrolled_state_sha256, first_contact, backend_address, backend_prefix,
           backend_source_address):
    """Remove only the recorded bootstrap key and restore approved runtime config."""
    personalize.environment()
    root = Path(root)
    personalize.validate(personalization)
    if not isinstance(first_contact, dict):
        raise ValueError('router first-contact retirement evidence is incomplete')
    fields = {'kind','authorized_key_sha256','interfaces_sha256','firewall_sha256',
              'sshd_config_sha256','host_key_public_sha256'}
    hashes = (('authorized_key_sha256', first_contact.get('authorized_key_sha256')),
              ('interfaces_sha256', first_contact.get('interfaces_sha256')),
              ('firewall_sha256', first_contact.get('firewall_sha256')),
              ('sshd_config_sha256', first_contact.get('sshd_config_sha256')))
    if (set(first_contact) != fields or
            first_contact.get('kind') != 'klokast.router-first-contact.v1' or
            any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) for _, value in hashes) or
            personalization['inputs_sha256'] != manifest.get('inputs_sha256') or
            not isinstance(runtime_packages, dict)):
        raise ValueError('router first-contact retirement evidence is incomplete')
    host_keys = first_contact['host_key_public_sha256']
    if (not isinstance(host_keys, dict) or not host_keys or
            not set(host_keys) <= {'rsa','ecdsa','ed25519'} or
            any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                for value in host_keys.values())):
        raise ValueError('router first-contact SSH host-key evidence is invalid')
    expected_interfaces = first_contact_interfaces(backend_address, backend_prefix)
    expected_firewall = first_contact_firewall(personalization['files']['etc/nftables.nft'],
        backend_source_address, backend_address, backend_prefix)
    expected_sshd = first_contact_sshd_config(backend_address)
    if (digest_bytes(expected_interfaces.encode()) != first_contact['interfaces_sha256'] or
            digest_bytes(expected_firewall.encode()) != first_contact['firewall_sha256'] or
            digest_bytes(expected_sshd.encode()) != first_contact['sshd_config_sha256']):
        raise ValueError('router first-contact cleanup inputs differ from the recorded access state')

    # Verify every source before removing any access path or replacing config.
    ssh_identity_directory = personalize.directory(root, 'etc/ssh')
    expected_host_files = {filename for name in host_keys
                           for filename in ('ssh_host_' + name + '_key', 'ssh_host_' + name + '_key.pub')}
    actual_host_files = {path.name for path in ssh_identity_directory.glob('ssh_host_*')}
    if actual_host_files != expected_host_files:
        raise ValueError('router first-contact SSH host-key set changed before retirement')
    for name, expected in host_keys.items():
        public = regular(root, 'etc/ssh/ssh_host_' + name + '_key.pub')
        if digest_bytes(public.read_bytes()) != expected:
            raise ValueError('router first-contact SSH host identity changed before retirement')
        private = regular(root, 'etc/ssh/ssh_host_' + name + '_key')
        if stat.S_IMODE(private.stat().st_mode) != 0o600:
            raise ValueError('router first-contact SSH private host key has unsafe mode')
    ssh_directory = _ssh_directory(root, create=False)
    key_path = ssh_directory / 'authorized_keys' if ssh_directory is not None else None
    key_present = bool(key_path is not None and (key_path.exists() or key_path.is_symlink()))
    if ssh_directory is not None:
        entries = list(ssh_directory.iterdir())
        if key_present:
            key = regular(root, 'root/.ssh/authorized_keys')
            if digest_bytes(key.read_bytes()) != first_contact['authorized_key_sha256']:
                raise ValueError('router first-contact key differs from the recorded enrollment')
        if any(path.name != 'authorized_keys' for path in entries):
            raise ValueError('router root SSH directory contains undeclared files')
    link = Path(root) / 'etc/runlevels/default/sshd'
    link_present = link.exists() or link.is_symlink()
    if link_present and (not link.is_symlink() or os.readlink(link) != '/etc/init.d/sshd'):
        raise ValueError('router first-contact SSH service link changed')
    config = Path(root) / 'etc/ssh/sshd_config.d/10-klokast-bootstrap.conf'
    config_present = config.exists() or config.is_symlink()
    config_directory = Path(root) / 'etc/ssh/sshd_config.d'
    if config_directory.exists() or config_directory.is_symlink():
        config_directory = personalize.directory(root, 'etc/ssh/sshd_config.d')
        config_entries = {path.name for path in config_directory.iterdir()}
        if config_entries - {'10-klokast-bootstrap.conf'}:
            raise ValueError('router SSH configuration directory has undeclared files')
    elif config_present:
        raise ValueError('router first-contact SSH configuration parent is absent')
    if config_present:
        current = personalize.regular(root, 'etc/ssh/sshd_config.d/10-klokast-bootstrap.conf')
        if (digest_bytes(current.read_bytes()) != first_contact['sshd_config_sha256'] or
                current.read_text() != expected_sshd):
            raise ValueError('router first-contact SSH configuration changed before retirement')
    interfaces_path = personalize.regular(root, 'etc/network/interfaces')
    if interfaces_path.read_text() not in (expected_interfaces, personalization['files']['etc/network/interfaces']):
        raise ValueError('router first-contact network configuration changed before retirement')
    firewall_path = personalize.regular(root, 'etc/nftables.nft')
    if firewall_path.read_text() not in (expected_firewall, personalization['files']['etc/nftables.nft']):
        raise ValueError('router first-contact firewall changed before retirement')

    if (not re.fullmatch('[0-9a-f]{64}', enrolled_state_sha256 or '') or
            not isinstance(accounts, dict) or
            personalize.digest(router_state.evidence(router_state.snapshot(root, **accounts))) != enrolled_state_sha256):
        raise ValueError('router first-contact enrolled identity differs from its recorded state')

    if key_present:
        key_path.unlink()
    if link_present:
        _service_link(root, enable=False)
    if config_present:
        config.unlink()
    personalize.put(root, 'etc/network/interfaces', personalization['files']['etc/network/interfaces'], 0o644)
    personalize.put(root, 'etc/nftables.nft', personalization['files']['etc/nftables.nft'], 0o644)
    result = router_finalize.finalize(root, manifest, enrolled_accounts=accounts,
        runtime_packages=runtime_packages, enrolled_state_sha256=enrolled_state_sha256)
    if ssh_directory is not None and ssh_directory.exists() and not any(ssh_directory.iterdir()):
        ssh_directory.rmdir()
    return result
