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


def _ssh_directory(root, *, create):
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


def _validate_public_key(key):
    if (not isinstance(key, str) or not 1 <= len(key.encode()) <= 4096 or
            '\n' in key or '\r' in key or '\0' in key):
        raise ValueError('router first-contact job or approved key is invalid')
    fields = key.split()
    if (len(fields) not in (2, 3) or fields[0] not in
            ('ssh-ed25519', 'ecdsa-sha2-nistp256', 'ssh-rsa') or
            not re.fullmatch('[A-Za-z0-9+/]+={0,2}', fields[1]) or
            len(fields) == 3 and not re.fullmatch(r'[ -~]{1,256}', fields[2])):
        raise ValueError('router first-contact key must be one plain supported SSH public key')
    with tempfile.TemporaryDirectory(prefix='klokast-router-key-') as directory:
        path = Path(directory) / 'key.pub'
        path.write_text(key + '\n')
        path.chmod(0o600)
        checked = subprocess.run(['ssh-keygen', '-lf', str(path), '-E', 'sha256'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15)
        if checked.returncode:
            raise ValueError('router first-contact key is not a valid SSH public key')
    return key


def seed(root, *, job, key, personalization, backend_address, backend_prefix):
    """Seed one approved public key for first boot; install no packages or services."""
    personalize.environment()
    root = Path(root)
    if (not isinstance(job, dict) or job.get('mode') != 'initial-install' or
            job.get('role') != 'router'):
        raise ValueError('router first-contact job or approved key is invalid')
    _validate_public_key(key)
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
    interfaces = first_contact_interfaces(backend_address, backend_prefix)
    for relative in ('usr/sbin/sshd', 'etc/init.d/sshd'):
        path = Path(root) / relative
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_uid != os.geteuid() or info.st_dev != Path(root).stat().st_dev):
            raise ValueError('router template lacks safe first-contact OpenSSH; rebuild the template')
    ssh_directory = _ssh_directory(root, create=True)
    authorized = ssh_directory / 'authorized_keys'
    if authorized.exists() or authorized.is_symlink() or list(ssh_directory.iterdir()):
        raise ValueError('router template already has first-contact SSH state')
    key_bytes = (key + '\n').encode()
    personalize.put(root, 'root/.ssh/authorized_keys', key + '\n', 0o600)
    personalize.put(root, 'etc/network/interfaces', interfaces, 0o644)
    _service_link(root, enable=True)
    command = subprocess.run(['chroot', str(root), '/usr/bin/ssh-keygen', '-A'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    if command.returncode:
        raise ValueError('router first-contact host-key generation failed')
    host_keys = {}
    for name in ('rsa', 'ecdsa', 'ed25519'):
        private = Path(root) / ('etc/ssh/ssh_host_' + name + '_key')
        public = Path(root) / ('etc/ssh/ssh_host_' + name + '_key.pub')
        if private.exists() or private.is_symlink():
            info = private.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_dev != Path(root).stat().st_dev or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('router first-contact host key has unsafe metadata')
            public_info = public.lstat()
            if not stat.S_ISREG(public_info.st_mode) or public_info.st_uid != os.geteuid() or public_info.st_nlink != 1:
                raise ValueError('router first-contact public host key is unsafe')
            host_keys[name] = digest_bytes(public.read_bytes())
    if not host_keys:
        raise ValueError('router first-contact did not create an SSH host identity')
    return {'kind':'klokast.router-first-contact.v1',
            'authorized_key_sha256':digest_bytes(key_bytes),
            'interfaces_sha256':digest_bytes(interfaces.encode()),
            'host_key_public_sha256':host_keys}


def retire(root, *, manifest, personalization, runtime_packages, accounts,
           enrolled_state_sha256, authorized_key_sha256):
    """Remove only the recorded bootstrap key and restore approved runtime config."""
    personalize.environment()
    root = Path(root)
    personalize.validate(personalization)
    if (not re.fullmatch('[0-9a-f]{64}', authorized_key_sha256) or
            personalization['inputs_sha256'] != manifest.get('inputs_sha256') or
            not isinstance(runtime_packages, dict)):
        raise ValueError('router first-contact retirement evidence is incomplete')
    ssh_directory = _ssh_directory(root, create=False)
    key_present = False
    if ssh_directory is not None:
        entries = list(ssh_directory.iterdir())
        key_path = ssh_directory / 'authorized_keys'
        if key_path in entries:
            key = regular(root, 'root/.ssh/authorized_keys')
            if digest_bytes(key.read_bytes()) != authorized_key_sha256:
                raise ValueError('router first-contact key differs from the recorded enrollment')
            key_path.unlink()
            key_present = True
        if any(path.name != 'authorized_keys' for path in entries):
            raise ValueError('router root SSH directory contains undeclared files')
    link = Path(root) / 'etc/runlevels/default/sshd'
    if link.exists() or link.is_symlink():
        _service_link(root, enable=False)
    elif personalize.packages(root) != runtime_packages:
        raise ValueError('router first-contact SSH service disappeared before package retirement')
    personalize.put(root, 'etc/network/interfaces', personalization['files']['etc/network/interfaces'], 0o644)
    result = router_finalize.finalize(root, manifest, enrolled_accounts=accounts,
        runtime_packages=runtime_packages, enrolled_state_sha256=enrolled_state_sha256)
    if ssh_directory is not None and ssh_directory.exists() and not any(ssh_directory.iterdir()):
        ssh_directory.rmdir()
    return result
