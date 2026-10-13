"""Copy only bounded machine identity inside an isolated guest."""
import hashlib
import os
from pathlib import Path
import shutil

IDENTITY = ('var/lib/tailscale/tailscaled.state', 'etc/machine-id',
            'etc/ssh/ssh_host_ed25519_key', 'etc/ssh/ssh_host_ed25519_key.pub',
            'etc/ssh/ssh_host_rsa_key', 'etc/ssh/ssh_host_rsa_key.pub',
            'etc/ssh/ssh_host_ecdsa_key', 'etc/ssh/ssh_host_ecdsa_key.pub')
REQUIRED = {'var/lib/tailscale/tailscaled.state',
            'etc/ssh/ssh_host_ed25519_key', 'etc/ssh/ssh_host_ed25519_key.pub'}


def preserve_identity(source, target, *, tailscale_source=None):
    copied = {}
    for name in IDENTITY:
        override = name == 'var/lib/tailscale/tailscaled.state' and tailscale_source is not None
        old = tailscale_source if override else source/name
        boundary = Path('/srv/retained') if override else source
        if not old.exists() and not old.is_symlink():
            if name in REQUIRED: raise RuntimeError('required VM identity is absent: '+name)
            if name == 'etc/machine-id':
                # Absence is recorded explicitly; do not invent a legacy identity.
                new=target/name
                if new.is_symlink():raise RuntimeError('new machine ID path is a link')
                new.unlink(missing_ok=True)
            continue
        if old.is_symlink() or not old.is_file() or not old.resolve().is_relative_to(boundary):
            raise RuntimeError('VM identity has an unsafe path: '+name)
        if old.stat().st_size > 2*1024*1024: raise RuntimeError('VM identity exceeds its size limit')
        new = target/name
        new.parent.mkdir(parents=True, exist_ok=True)
        if new.is_symlink(): raise RuntimeError('new image identity path is a link')
        shutil.copy2(old, new)
        os.chown(new, old.stat().st_uid, old.stat().st_gid)
        checksum = hashlib.sha256(old.read_bytes()).hexdigest()
        if hashlib.sha256(new.read_bytes()).hexdigest() != checksum:
            raise RuntimeError('VM identity copy failed verification')
        copied[name] = checksum
    return copied

