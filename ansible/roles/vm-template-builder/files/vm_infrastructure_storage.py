"""Same-VM state migration and retained mounts inside a networkless Xen guest."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

import vm_infrastructure_personalize as base
from vm_controller_state import PRESERVE, PROVIDER_FILES
from vm_guest_identity import IDENTITY, preserve_identity

DATA = Path('/srv/retained')
DEVICE = '/dev/xvde'
EXTRA_OPS = ('home/smith/private/klokast/ops-replacement-inputs',
             'home/smith/private/klokast/vm-replacement-inputs',
             'var/lib/klokast/ops-nightly')
DIRECTORIES = {'var/lib/tailscale', *EXTRA_OPS,
    *(p for p in PRESERVE if p.startswith(('var/lib/', 'var/log/'))),
    *('home/smith/private/klokast/' + p for p in
      ('vpn-egress', 'archive', 'codex-archives', 'apps', 'evidence', 'logs'))}


def mappings(role):
    if role == 'air':
        return {'home/agent': 'directory', 'var/lib/klokast/runner-archives': 'directory',
                'var/lib/tailscale': 'directory'}
    if role != 'ops':
        raise RuntimeError('infrastructure retained storage requires ops or air')
    return {p: 'directory' if p in DIRECTORIES else 'file'
            for p in (*PRESERVE, *EXTRA_OPS)
            if p not in IDENTITY or p == 'var/lib/tailscale'}


def bind_mappings(role):
    """Bind writable directories so native atomic file replacement still works."""
    if role == 'air': return mappings(role)
    return {p: 'directory' for p in ('home/neo/.ssh', 'home/smith/.ssh',
        'home/smith/private/klokast', *(p for p in mappings(role) if p.startswith('var/')))}


def safe_path(root, relative):
    """Inspect parents without following guest-controlled links."""
    path = root
    for part in Path(relative).parts:
        if part in ('', '.', '..', '/'):
            raise RuntimeError('invalid retained state path')
        path /= part
        if path.is_symlink():
            raise RuntimeError('retained state mapping has a symbolic-link parent: ' + relative)
    return path


def atomic(path, value):
    if path.is_symlink():
        raise RuntimeError('retained state record is a symbolic link')
    temporary = path.with_name('.' + path.name + '.pending')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)


def copy_state(source, destination, kind):
    """Copy links as links; never traverse mounts or evaluate retained files."""
    if source.is_symlink() or not (source.is_dir() if kind == 'directory' else source.is_file()):
        raise RuntimeError('retained source has an unexpected type: ' + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if kind == 'directory': destination.mkdir(exist_ok=True)
    suffix = '/' if kind == 'directory' else ''
    command = ['rsync', '-aHAXS', '--numeric-ids', '--one-file-system']
    # A resumed copy can remove only entries under this operation's own mapping.
    if kind == 'directory': command.append('--delete-delay')
    operands = ['--', str(source) + suffix, str(destination) + suffix]
    def transfer(arguments):
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=1200)
        if result.returncode:
            raise RuntimeError('retained state transfer failed: ' + result.stderr[-1000:])
        return result.stdout.strip()
    transfer(command + operands)
    changed = transfer(command + ['--dry-run', '--checksum', '--itemize-changes'] + operands)
    if changed:
        raise RuntimeError('retained state content or metadata verification failed')


def contract(value):
    role = value['role']; uuid.UUID(value['filesystem_uuid'])
    if (role not in ('ops', 'air') or not isinstance(value['accounts'], dict) or
            not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', value['box'])):
        raise RuntimeError('invalid infrastructure storage role or accounts')
    expected = {'neo', 'smith', 'minion'} if role == 'ops' else {'neo', 'agent'}
    if set(value['accounts']) != expected or value['accounts']['neo'] != [1000, 1000]:
        raise RuntimeError('infrastructure storage account set differs')
    accounts = list(value['accounts'].values())
    if (any(not isinstance(v, list) or len(v) != 2 or
            any(type(i) is not int or not 1000 <= i <= 60000 for i in v) for v in accounts) or
            len({v[0] for v in accounts}) != len(accounts) or
            len({v[1] for v in accounts}) != len(accounts)):
        raise RuntimeError('infrastructure numeric account identities conflict')
    return {'kind': 'klokast.infrastructure-storage.v1', 'box': value['box'],
            'role': role, 'filesystem_uuid': value['filesystem_uuid'],
            'accounts': value['accounts']}


def mount(value, *, initialize=False):
    expected = value['filesystem_uuid']; device = DEVICE
    actual = subprocess.run(['blkid', device], capture_output=True, text=True, timeout=30)
    if actual.returncode not in (0, 2):
        raise RuntimeError('cannot inspect the assigned infrastructure data disk')
    if not actual.stdout.strip():
        if not initialize: raise RuntimeError('retained filesystem is absent; refuse formatting')
        base.run(['mkfs.ext4', '-F', '-U', expected, device])
    elif 'UUID="' + expected + '"' not in actual.stdout or 'TYPE="ext4"' not in actual.stdout:
        raise RuntimeError('retained infrastructure filesystem identity differs')
    checked = subprocess.run(['e2fsck', '-f', '-p', device], capture_output=True, text=True, timeout=300)
    if checked.returncode not in (0, 1):
        raise RuntimeError('retained filesystem check requires intervention: ' + checked.stderr[-1000:] + checked.stdout[-1000:])
    base.run(['resize2fs', device])
    verify_capacity(device)
    if DATA.is_symlink(): raise RuntimeError('retained mount point is a symbolic link')
    DATA.mkdir(parents=True, exist_ok=True)
    if any(DATA.iterdir()): raise RuntimeError('retained mount would hide root files')
    base.run(['mount', '-t', 'ext4', device, str(DATA)])
    DATA.chmod(0o711)


def verify_capacity(device):
    header = base.run(['dumpe2fs', '-h', device])
    fields = {key: re.findall(r'^' + key + r':\s*([0-9]+)\s*$', header, re.MULTILINE)
              for key in ('Block count', 'Block size')}
    if any(len(values) != 1 for values in fields.values()):
        raise RuntimeError('retained filesystem capacity is ambiguous')
    block_size = int(fields['Block size'][0])
    if block_size not in (1024, 2048, 4096):
        raise RuntimeError('retained filesystem block size is unsupported')
    device_bytes = int((Path('/sys/class/block') / Path(device).name / 'size').read_text()) * 512
    if int(fields['Block count'][0]) != device_bytes // block_size:
        raise RuntimeError('retained filesystem does not fill its assigned data device')


def seed(source, value):
    identity = contract(value)
    marker = DATA / 'platform-infrastructure-storage.json'
    receipt = DATA / 'platform-infrastructure-migration.json'
    assignment = dict(identity, source_machine=value['machine_id'], operation=value['operation'])
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text()) != identity:
            raise RuntimeError('retained infrastructure ownership differs')
        if value['migrate'] and (receipt.is_symlink() or not receipt.is_file() or json.loads(receipt.read_text()) != assignment):
            raise RuntimeError('retained data was seeded by another migration; resume that operation or reconcile it explicitly')
        return  # Never restore an old root over already adopted state.
    if not value['migrate']:
        raise RuntimeError('infrastructure state needs explicit first migration')
    progress = DATA / '.infrastructure-migration.json'
    if progress.exists():
        if progress.is_symlink() or json.loads(progress.read_text()) != assignment:
            raise RuntimeError('retained state belongs to another unfinished migration')
    else:
        allowed = {'lost+found'}
        if value['role'] == 'ops':
            legacy = DATA / 'platform-ops-data.json'
            if legacy.is_symlink() or not legacy.is_file() or json.loads(legacy.read_text()) != {
                    'box': value['box'], 'filesystem_uuid': value['filesystem_uuid']}:
                raise RuntimeError('controller mirror volume lacks its exact ownership record')
            allowed |= {'platform-ops-data.json', 'download-sources'}
        if any(p.name not in allowed for p in DATA.iterdir()):
            raise RuntimeError('unassigned infrastructure data volume contains files')
        atomic(progress, assignment)
    selected = mappings(value['role'])
    for relative, kind in selected.items():
        old = safe_path(source, relative)
        target = safe_path(DATA / 'state', relative)
        if old.exists(): copy_state(old, target, kind)
        elif kind == 'directory':
            target.mkdir(parents=True, exist_ok=True)
            account = 'agent' if value['role'] == 'air' and relative == 'home/agent' else 'smith' if value['role'] == 'ops' and relative != 'var/lib/tailscale' else 'neo'
            uid, gid = value['accounts'][account] if relative != 'var/lib/tailscale' else (0, 0)
            os.chown(target, uid, gid); target.chmod(0o700)
    for relative in ('home/neo/.ssh', 'home/smith/.ssh', 'home/smith/private/klokast') if value['role']=='ops' else ():
        target=safe_path(DATA/'state',relative); target.mkdir(parents=True,exist_ok=True)
        os.chown(target,*value['accounts']['neo' if relative.startswith('home/neo/') else 'smith'])
        target.chmod(0o700)
    state = DATA / 'state'; state.chmod(0o700)
    os.sync()
    atomic(receipt, assignment)
    atomic(marker, identity)
    progress.unlink(); os.sync()


def configure(value):
    identity = contract(value)
    entries = ['/dev/xvda / ext4 defaults 0 1',
               'UUID=' + value['filesystem_uuid'] + ' /srv/retained ext4 defaults 0 2']
    bindings = {}
    for relative, kind in bind_mappings(value['role']).items():
        source = safe_path(DATA / 'state', relative)
        if not source.exists(): continue
        target = safe_path(Path('/'), relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == 'directory':
            target.mkdir(exist_ok=True)
            if any(target.iterdir()) and relative != 'var/lib/tailscale':
                raise RuntimeError('retained bind would hide unmanaged candidate files: ' + relative)
        else:
            target.touch(exist_ok=True)
        target.chmod(0)
        bindings[str(target)] = str(source)
        entries.append(str(source) + ' ' + str(target) + ' none bind 0 0')
    if value['role'] == 'ops':
        target = Path('/var/lib/klokast/download-sources'); target.mkdir(parents=True, exist_ok=True); target.chmod(0)
        bindings[str(target)] = str(DATA / 'download-sources')
        entries.append(str(DATA / 'download-sources') + ' ' + str(target) + ' none bind 0 0')
    else:
        archive = '/var/lib/klokast/runner-archives'
        target = DATA / 'state/home/agent/.codex-archives'
        if target.is_symlink(): raise RuntimeError('runner archive mount is a symbolic link')
        target.mkdir(exist_ok=True)
        entries.append(archive + ' /home/agent/.codex-archives none bind,ro 0 0')
    base.put('etc/fstab', '\n'.join(entries) + '\n')
    base.put('etc/klokast-' + value['role'] + '-storage.json', json.dumps(dict(identity, bindings=bindings), sort_keys=True) + '\n', 0o644)
    service = 'klokast-infrastructure-storage'
    base.put('etc/init.d/' + service, '#!/sbin/openrc-run\ndepend() { need localmount; before tailscale crond sshd; }\n'
             'start() { /usr/bin/python3 /usr/local/libexec/vm_infrastructure_storage.py --verify; }\n', 0o755)
    base.put('etc/conf.d/tailscale', 'no_logs_no_support=yes\nrc_need="localmount nftables ' + service + '"\n')
    for name in ('crond', 'sshd'):
        base.put('etc/conf.d/' + name, 'rc_need="localmount ' + service + '"\n')
    base.run(['rc-update', 'add', service, 'boot'])
    return identity


def finalize(value, source):
    """Called after image personalization, with the old root mounted read-only."""
    identity = contract(value)
    machine = source / 'etc/machine-id'
    observed = hashlib.sha256(machine.read_bytes()).hexdigest() if machine.exists() else None
    if observed != value['machine_id']: raise RuntimeError('source machine identity changed')
    if value['role'] == 'ops':
        marker = json.loads((source / 'etc/klokast/controller-ha.json').read_text())
        if marker.get('role') != 'standby' or marker.get('box') != value['box'] or marker.get('active_box') != value['active_box']:
            raise RuntimeError('only the current standby may migrate controller state')
        if any((source / p).exists() or (source / p).is_symlink() for p in PROVIDER_FILES):
            raise RuntimeError('standby retains provider mutation credentials; finish the existing handoff')
    mount(value, initialize=value['initialize'])
    try:
        seed(source, value)
        preserve_identity(source, Path('/'), tailscale_source=DATA / 'state/var/lib/tailscale/tailscaled.state', require_ssh=value['role'] == 'ops')
        configure(value)
        os.sync()
    finally: base.run(['umount', str(DATA)])
    return identity


def verify():
    role = os.uname().nodename.rsplit('-', 1)[-1]
    if role not in ('ops', 'air'): raise RuntimeError('unexpected infrastructure role')
    record = json.loads(Path('/etc/klokast-' + role + '-storage.json').read_text())
    expected = contract(record)
    if not os.path.ismount(DATA): raise RuntimeError('infrastructure retained volume is not mounted')
    if json.loads((DATA / 'platform-infrastructure-storage.json').read_text()) != expected:
        raise RuntimeError('infrastructure retained ownership differs')
    rows = [v.split() for v in Path('/proc/mounts').read_text().splitlines() if v.split()[1] == str(DATA)]
    if len(rows) != 1 or rows[0][2] != 'ext4' or 'UUID="' + record['filesystem_uuid'] + '"' not in base.run(['blkid', rows[0][0]]):
        raise RuntimeError('infrastructure retained filesystem differs')
    verify_capacity(rows[0][0])
    for target, source in record['bindings'].items():
        if not os.path.ismount(target) or not os.path.samefile(target, source):
            raise RuntimeError('infrastructure retained bind is missing or wrong: ' + target)
    if role == 'air' and not os.statvfs('/home/agent/.codex-archives').f_flag & os.ST_RDONLY:
        raise RuntimeError('runner archives must be mounted read-only')
    return expected


if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['--verify']: raise SystemExit('Expected --verify')
    print(json.dumps(verify(), sort_keys=True))
