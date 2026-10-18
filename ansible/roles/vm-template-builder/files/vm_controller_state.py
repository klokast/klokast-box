"""Explicit same-controller preservation; managed authority is reconstructed."""

# Same-controller state only. Managed /etc trees, source checkouts and obsolete
# authority publications are absent. Provider files have a separate allowlist.
PRESERVE = (
    'var/lib/tailscale', 'etc/machine-id',
    'etc/ssh/ssh_host_ed25519_key', 'etc/ssh/ssh_host_ed25519_key.pub',
    'etc/ssh/ssh_host_rsa_key', 'etc/ssh/ssh_host_rsa_key.pub',
    'etc/ssh/ssh_host_ecdsa_key', 'etc/ssh/ssh_host_ecdsa_key.pub',
    'home/neo/.ssh/authorized_keys', 'home/smith/.ssh/authorized_keys',
    'home/smith/.ssh/github-klokast-codex', 'home/smith/.ssh/github-klokast-codex.pub',
    'home/smith/.ssh/github-klokast-instance', 'home/smith/.ssh/github-klokast-instance.pub',
    'home/smith/private/klokast/dom0-console.yml',
    'home/smith/private/klokast/platform-resources.yml',
    'home/smith/private/klokast/platform-users.yml',
    'home/smith/private/klokast/controller-ha.yml',
    'home/smith/private/klokast/controller-switch',
    'home/smith/private/klokast/air-maintenance',
    'home/smith/private/klokast/air-workspace',
    'home/smith/private/klokast/openclaw-vpn.yml',
    'home/smith/private/klokast/vpn-egress',
    'home/smith/private/klokast/huawei-tailscale-pinhole.json',
    'home/smith/private/klokast/archive', 'home/smith/private/klokast/codex-archives',
    'home/smith/private/klokast/apps', 'home/smith/private/klokast/evidence',
    'home/smith/private/klokast/logs',
    'var/lib/klokast/operations-journal', 'var/lib/klokast/updates',
    'var/lib/klokast/apply-used-nonces', 'var/lib/klokast/apply-executions',
    'var/lib/klokast/freebox-ipv6',
    'var/lib/klokast/airunner-migration', 'var/lib/klokast/legacy-input-recovery',
    'var/lib/klokast/private-source-recoveries', 'var/lib/klokast/instance-recovery-manifests',
    'var/log/klokast',
)
PROVIDER_FILES = ('etc/klokast/tailscale-policy.env', 'etc/klokast/tailscale-devices.env',
                  'etc/klokast/freebox-ipv6.json', 'etc/klokast/secret-authority/github-app.env',
                  'etc/klokast/secret-authority/github-app.pem',
                  'etc/klokast/secret-authority/air-git.json',
                  'home/smith/.ssh/github-klokast-instance-write',
                  'etc/klokast/freebox-ipv6.pending.json',
                  'etc/klokast/secret-authority/apps/static-site/cloudflared-token',
                  'etc/klokast/secret-authority/instance-bootstrap/github-app.env',
                  'etc/klokast/secret-authority/instance-bootstrap/github-app.pem',
                  'etc/klokast/secret-authority/instance-development/github-app.env',
                  'etc/klokast/secret-authority/instance-development/github-app.pem')

# Git credentials live in retained SSH directories. Provider files stay regular
# files on the OS root, so their existing atomic update tools keep working.
ROOT_CREDENTIALS = tuple(p for p in PROVIDER_FILES if p.startswith('etc/'))
PRESERVE += PROVIDER_FILES


def preserve_credentials(source, target):
    """Copy this stopped controller's exact credential files, never managed /etc."""
    import hashlib
    import os
    import shutil
    import stat
    import tempfile
    for relative in ROOT_CREDENTIALS:
        old = source / relative
        if not old.exists() and not old.is_symlink():
            continue
        info = old.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 2 * 1024 * 1024 or
                not old.resolve().is_relative_to(source.resolve())):
            raise RuntimeError('same-controller credential has unsafe metadata: ' + relative)
        new = target / relative
        new.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if new.is_symlink() or not new.parent.resolve().is_relative_to(target.resolve()):
            raise RuntimeError('candidate credential path escapes its root')
        descriptor, name = tempfile.mkstemp(dir=new.parent, prefix='.' + new.name + '.replacement-')
        temporary = new.with_name(name.split('/')[-1])
        try:
            with os.fdopen(descriptor, 'wb') as output, old.open('rb') as input_file:
                shutil.copyfileobj(input_file, output)
                output.flush(); os.fsync(output.fileno())
                os.fchown(output.fileno(), info.st_uid, info.st_gid)
            if hashlib.sha256(temporary.read_bytes()).digest() != hashlib.sha256(old.read_bytes()).digest():
                raise RuntimeError('same-controller credential copy failed verification')
            temporary.replace(new)
            directory = os.open(new.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

