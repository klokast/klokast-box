"""Router retained state, prepared only inside an isolated filesystem guest.

This module has no enrollment, Xen, allocation or authorization interface.
The caller owns disk exclusivity and the durable transaction start boundary.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

KIND = 'klokast.router-retained-state.v1'
SIZE = 1024 ** 3
DATA = Path('/srv/retained')
MARKER = 'router-state.json'
ROOT_RECORD = 'etc/klokast/router-storage.json'
ROOT_UID = 0
ROOT_GID = 0
KEYS = ('rsa', 'ecdsa', 'ed25519')
FILES = {
    'var/lib/tailscale/tailscaled.state': ('tailscale/tailscaled.state', 16 * 1024**2, True),
    'var/lib/dhcpcd/duid': ('dhcpcd/duid', 4096, True),
    'var/lib/dhcpcd/secret': ('dhcpcd/secret', 4096, True),
    'var/lib/misc/dnsmasq.leases': ('dnsmasq/dnsmasq.leases', 4 * 1024**2, True),
    'etc/machine-id': ('identity/machine-id', 128, False),
}
for directory, target in (('etc/ssh', 'identity/ssh'), ('var/lib/tailscale/ssh', 'tailscale/ssh')):
    for key in KEYS:
        for suffix in ('', '.pub'):
            name = 'ssh_host_' + key + '_key' + suffix
            FILES[directory + '/' + name] = (target + '/' + name, 16384, False)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'kind', 'box', 'filesystem_uuid', 'accounts'} or
            value['kind'] != KIND or not isinstance(value.get('box'), str) or
            not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', value['box']) or
            not isinstance(value.get('filesystem_uuid'), str) or
            not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value.get('filesystem_uuid', ''))):
        raise RuntimeError('router storage requires its exact box and filesystem UUID')
    accounts = value['accounts']
    if (not isinstance(accounts, dict) or set(accounts) != {'dnsmasq_uid', 'dnsmasq_gid', 'tailscale_gid'} or
            any(type(v) is not int or not 1 <= v <= 65534 for v in accounts.values())):
        raise RuntimeError('router storage requires the recorded numeric service accounts')
    return value


def path(root, relative, *, create=False):
    """Reject links and guest mounts before opening a fixed path."""
    root = Path(root)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or root.is_symlink():
        raise RuntimeError('router state root is not a regular directory')
    parts = Path(relative).parts
    if not parts or any(p in ('..', '/', '') for p in parts):
        raise RuntimeError('router state path is outside its fixed root')
    current = root
    for part in parts[:-1]:
        current = current / part
        if create and not current.exists() and not current.is_symlink():
            current.mkdir(mode=0o700)
        item = current.lstat()
        if not stat.S_ISDIR(item.st_mode) or item.st_dev != info.st_dev:
            raise RuntimeError('router state parent is a link or another filesystem: ' + relative)
    return current / parts[-1]


def read(root, relative, maximum, *, optional=False):
    try:
        selected = path(root, relative)
        fd = os.open(selected, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if optional:
            return None
        raise RuntimeError('required router state is absent: ' + relative) from None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_dev != Path(root).stat().st_dev or info.st_size > maximum or
                stat.S_IMODE(info.st_mode) & 0o7022):
            raise RuntimeError('router state has unsafe type, links, size or mode: ' + relative)
        content = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        if (len(content) != info.st_size or
                (info.st_size, info.st_mtime_ns, info.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise RuntimeError('router state changed during the stopped copy: ' + relative)
    return content, info


def ownership(relative, info, accounts):
    allowed = {(ROOT_UID, ROOT_GID)}
    if relative == 'var/lib/misc/dnsmasq.leases':
        allowed.add((accounts['dnsmasq_uid'], accounts['dnsmasq_gid']))
    if relative.startswith('var/lib/tailscale/'):
        allowed.add((ROOT_UID, accounts['tailscale_gid']))
    private = (relative.endswith(('tailscaled.state', '/secret', '_key')))
    if ((info.st_uid, info.st_gid) not in allowed or
            (private and stat.S_IMODE(info.st_mode) & 0o077)):
        raise RuntimeError('router state ownership or private mode differs: ' + relative)


def snapshot(root, contract, *, retained=False):
    validate(contract)
    result = {}
    for relative, (stored, maximum, required) in FILES.items():
        item = read(root, stored if retained else relative, maximum, optional=not required)
        if item is None:
            continue
        content, info = item
        ownership(relative, info, contract['accounts'])
        if not content and relative != 'var/lib/misc/dnsmasq.leases':
            raise RuntimeError('router identity file is empty: ' + relative)
        result[relative] = (content, info)
    return result


def evidence(files):
    return {name: {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
                   'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
            for name, (content, info) in files.items()}


def write(root, relative, content, mode=0o600, uid=None, gid=None):
    uid = ROOT_UID if uid is None else uid
    gid = ROOT_GID if gid is None else gid
    target = path(root, relative, create=True)
    if target.exists() or target.is_symlink():
        read(root, relative, max(len(content), 16 * 1024**2))
    fd, temporary = tempfile.mkstemp(prefix='.router-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), uid, gid)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
        descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def record(data):
    item = read(data, MARKER, 65536)
    if item[1].st_uid != ROOT_UID or stat.S_IMODE(item[1].st_mode) != 0o600:
        raise RuntimeError('router storage marker is not private and root owned')
    value = json.loads(item[0])
    if (not isinstance(value, dict) or set(value) != {'contract', 'stage', 'seed_id', 'files'} or
            value['stage'] not in ('empty', 'seeding', 'ready') or
            not isinstance(value.get('seed_id'), str) or not re.fullmatch('[0-9a-f]{24}', value['seed_id']) or
            not isinstance(value['files'], dict) or not set(value['files']) <= FILES.keys()):
        raise RuntimeError('router retained-state marker is invalid')
    validate(value['contract'])
    for name, proof in value['files'].items():
        if (not isinstance(proof, dict) or set(proof) != {'sha256', 'bytes', 'uid', 'gid', 'mode'} or
                not isinstance(proof.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', proof['sha256']) or
                any(type(proof[k]) is not int for k in ('bytes', 'uid', 'gid', 'mode')) or
                not 0 <= proof['bytes'] <= FILES[name][1] or not 0 <= proof['mode'] <= 0o777):
            raise RuntimeError('router retained-state file evidence is invalid')
    if value['stage'] != 'empty' and not {k for k, v in FILES.items() if v[2]} <= value['files'].keys():
        raise RuntimeError('router retained-state evidence lacks required files')
    return value


def initialize(data, contract, seed_id):
    """Initialize only a newly allocated, mounted filesystem; do not enroll."""
    validate(contract)
    if not isinstance(seed_id, str) or not re.fullmatch('[0-9a-f]{24}', seed_id):
        raise RuntimeError('router state initialization requires an exact operation')
    marker = Path(data) / MARKER
    if marker.exists() or marker.is_symlink():
        value = record(data)
        if value['contract'] != contract or value['seed_id'] != seed_id:
            raise RuntimeError('router data already belongs to another initialization')
        return value
    if {p.name for p in Path(data).iterdir()} - {'lost+found'}:
        raise RuntimeError('router initialization refuses an unknown nonempty filesystem')
    value = dict(contract=contract, stage='empty', seed_id=seed_id, files={})
    write(data, MARKER, json.dumps(value, sort_keys=True).encode())
    return value


def migrate(source, data, contract, seed_id, *, checkpoint=lambda stage: None):
    """Seed from one stopped root. Never overwrite state from a running candidate."""
    value = record(data)
    if value['contract'] != validate(contract) or value['seed_id'] != seed_id:
        raise RuntimeError('router state copy differs from its owned initialization')
    for relative in ('var/lib/tailscale/tka', 'var/lib/tailscale/tpm-sealed'):
        candidate = path(source, relative)
        if candidate.exists() or candidate.is_symlink():
            raise RuntimeError('router migration refuses unsupported enrolled state: ' + relative)
    for directory in ('etc/ssh', 'var/lib/tailscale/ssh'):
        try:
            parent = path(source, directory + '/placeholder')
        except FileNotFoundError:
            continue
        if parent.parent.exists():
            for item in parent.parent.glob('ssh_host_*'):
                if directory + '/' + item.name not in FILES:
                    raise RuntimeError('router migration found an unsupported SSH host key')
    files = snapshot(source, contract)
    proof = evidence(files)
    if value['stage'] != 'empty' and value['files'] != proof:
        raise RuntimeError('router source changed after seeding; abandon the unbooted operation and prepare again')
    if value['stage'] == 'ready':
        if evidence(snapshot(data, contract, retained=True)) != proof:
            raise RuntimeError('router retained state changed; refusing to replay the legacy copy')
        return value
    value.update(stage='seeding', files=proof)
    write(data, MARKER, json.dumps(value, sort_keys=True).encode())
    checkpoint('seeding')
    for name, (content, info) in files.items():
        write(data, FILES[name][0], content, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
        checkpoint(name)
    # dnsmasq drops privilege before it opens its lease file.
    os.chmod(data, 0o711)
    os.chown(Path(data) / 'dnsmasq', contract['accounts']['dnsmasq_uid'], contract['accounts']['dnsmasq_gid'])
    if evidence(snapshot(data, contract, retained=True)) != proof:
        raise RuntimeError('router state copy did not preserve bytes and metadata')
    value['stage'] = 'ready'
    write(data, MARKER, json.dumps(value, sort_keys=True).encode())
    checkpoint('ready')
    return value


def attach(data, contract):
    """Validate current native state, without comparing mutable bytes to old hashes."""
    value = record(data)
    if value['contract'] != validate(contract) or value['stage'] != 'ready':
        raise RuntimeError('router data is not initialized for the selected assignment')
    files = snapshot(data, contract, retained=True)
    if set(files) != set(value['files']):
        raise RuntimeError('router retained identity file presence changed')
    for name, item in evidence(files).items():
        if name.startswith('etc/') or '/ssh/' in name:
            if item != value['files'][name]:
                raise RuntimeError('router host identity changed outside a rotation operation: ' + name)
    return value


def discard_wan_leases(data):
    """Discard only known lease caches while all normal router services are off."""
    directory = path(data, 'dhcpcd/placeholder').parent
    for item in directory.iterdir():
        if re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}\.lease6?', item.name):
            read(data, 'dhcpcd/' + item.name, 4 * 1024**2)
            item.unlink()


def run(argv, *, accepted=(0,)):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    if result.returncode not in accepted:
        raise RuntimeError('router storage command failed: ' + argv[0] + ': ' + result.stderr[-1000:])
    return result.stdout.strip()


def filesystem_uuid(device):
    result = run(['blkid', '-s', 'UUID', '-o', 'value', device], accepted=(0, 2))
    match = re.fullmatch(r'.*\bUUID="([0-9a-f-]{36})".*', result)
    return match[1] if match else result


def isolated():
    if Path('/sys/hypervisor/type').read_text().strip() != 'xen' or os.geteuid() != 0 or (
            Path('/sys/hypervisor/uuid').read_text().strip() == '00000000-0000-0000-0000-000000000000' or
            {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'}):
        raise RuntimeError('router filesystem preparation requires an isolated Xen guest')


def prepare(contract, mode, seed_id, *, source=None, data=DATA, device='/dev/xvde', root=Path('/')):
    """Mounted-filesystem work; caller must be the networkless finalizer."""
    validate(contract)
    if mode not in ('initialize', 'migrate', 'attach') or (mode == 'migrate') != (source is not None):
        raise RuntimeError('router storage requires initialize, migrate with source, or attach')
    isolated()
    if (device != '/dev/xvde' or Path('/sys/class/block/xvde/ro').read_text().strip() != '0' or
            Path('/sys/class/block/xvde/size').read_text().strip() != str(SIZE // 512)):
        raise RuntimeError('router finalizer requires its recorded 1 GiB writable data slot')
    observed = filesystem_uuid(device)
    if not observed and mode in ('initialize', 'migrate'):
        # Missing UUID does not prove that an LV is new. The native allocator
        # zeroes the exact owned LV; reject any residual data before mkfs.
        with open(device, 'rb') as stream:
            if any(chunk.strip(b'\0') for chunk in iter(lambda: stream.read(1024**2), b'')):
                raise RuntimeError('router data is not blank; refusing to format it')
        run(['mkfs.ext4', '-F', '-U', contract['filesystem_uuid'], device])
    elif observed != contract['filesystem_uuid']:
        raise RuntimeError('router data filesystem differs; refusing to format it')
    run(['e2fsck', '-f', '-n', device])
    data = Path(data)
    if data.is_symlink() or data.exists() and any(data.iterdir()):
        raise RuntimeError('router data mount would hide existing files')
    data.mkdir(mode=0o711, parents=True, exist_ok=True)
    run(['mount', '-t', 'ext4', device, str(data)])
    try:
        if mode in ('initialize', 'migrate'):
            initialize(data, contract, seed_id)
        if mode == 'migrate':
            migrate(source, data, contract, seed_id)
        if mode != 'initialize':
            value = attach(data, contract)
            discard_wan_leases(data)
            configure(root, contract, value)
        os.sync()
        return record(data)
    finally:
        run(['umount', str(data)])


def bindings(value):
    result = [('tailscale', 'var/lib/tailscale'), ('dhcpcd', 'var/lib/dhcpcd')]
    result += [(FILES[name][0], name) for name in sorted(value['files']) if name.startswith('etc/')]
    return result


def configure(root, contract, value):
    """The new root contains managed configuration and empty mount targets only."""
    validate(contract)
    account_check(root, contract)
    previous = read(root, ROOT_RECORD, 65536, optional=True)
    if previous and json.loads(previous[0]) != contract:
        raise RuntimeError('router root already selects a different retained filesystem')
    for name, (_stored, maximum, _required) in FILES.items():
        if name.startswith('etc/'):
            existing = read(root, name, maximum, optional=True)
            if existing and existing[0]:
                raise RuntimeError('router image contains a host identity: ' + name)
    # Persist intent first. Empty placeholders can then be retried after an
    # interrupted finalization without accepting an image identity.
    write(root, ROOT_RECORD, json.dumps(contract, sort_keys=True).encode())
    lines = ['/dev/xvda / ext4 defaults 0 1',
             'UUID=' + contract['filesystem_uuid'] + ' /srv/retained ext4 defaults 0 2']
    for stored, target in bindings(value):
        selected = path(root, target, create=True)
        if target.startswith('etc/'):
            if selected.exists() or selected.is_symlink():
                if read(root, target, 16384)[0]:
                    raise RuntimeError('router image contains a host identity: ' + target)
            write(root, target, b'', 0o600)
        else:
            if selected.is_symlink() or selected.exists() and any(selected.iterdir()):
                raise RuntimeError('router image contains service state: ' + target)
            selected.mkdir(mode=0o700, exist_ok=True)
        lines.append('/srv/retained/' + stored + ' /' + target + ' none bind 0 0')
    write(root, 'etc/fstab', ('\n'.join(lines) + '\n').encode(), 0o644)
    config = read(root, 'etc/dnsmasq.conf', 1024**2)[0].decode()
    if len(re.findall(r'^dhcp-leasefile=.*$', config, re.M)) != 1:
        raise RuntimeError('router recipe must declare exactly one DHCP lease path')
    config = re.sub(r'^dhcp-leasefile=.*$', 'dhcp-leasefile=/srv/retained/dnsmasq/dnsmasq.leases', config, flags=re.M)
    write(root, 'etc/dnsmasq.conf', config.encode(), 0o644)
    service = ('#!/sbin/openrc-run\n'
               'description="Verify the router retained state before networking"\n'
               'depend() { need localmount; before networking dhcpcd dnsmasq tailscale; }\n'
               'start() { /usr/bin/python3 /usr/local/libexec/vm_router_state.py --verify; }\n')
    write(root, 'etc/init.d/klokast-router-storage', service.encode(), 0o755)
    for name in ('networking', 'dhcpcd', 'dnsmasq', 'tailscale'):
        relative = 'etc/conf.d/' + name
        old = read(root, relative, 65536, optional=True)
        content = old[0] if old else b''
        if name == 'dnsmasq':
            # Alpine start_pre creates/chowns this path independently of the
            # daemon's dhcp-leasefile setting. Keep both on the retained disk.
            content = re.sub(rb'^leasefile=.*\n?', b'', content, flags=re.M)
            content += b'\nleasefile="/srv/retained/dnsmasq/dnsmasq.leases"\n'
        dependency = b'\nrc_need="${rc_need:-} klokast-router-storage"\n'
        if dependency not in content:
            content += dependency
        write(root, relative, content, 0o644)


def account_check(root, contract):
    """Never reuse numeric state owners for different image accounts."""
    users = [line.split(':') for line in read(root, 'etc/passwd', 65536)[0].decode().splitlines()]
    groups = [line.split(':') for line in read(root, 'etc/group', 65536)[0].decode().splitlines()]
    a = contract['accounts']
    if (any(len(row) != 7 for row in users) or any(len(row) != 4 for row in groups) or
            [row[:4] for row in users if row[0] == 'dnsmasq' or row[2] == str(a['dnsmasq_uid'])] !=
            [['dnsmasq', 'x', str(a['dnsmasq_uid']), str(a['dnsmasq_gid'])]]):
        raise RuntimeError('router image dnsmasq account differs from retained ownership')
    for name in ('dnsmasq', 'tailscale'):
        gid = str(a[name + '_gid'])
        if [row[:3] for row in groups if row[0] == name or row[2] == gid] != [[name, 'x', gid]]:
            raise RuntimeError('router image group differs from retained ownership: ' + name)


def recreate_accounts(root, contract):
    """Keep approved package definitions and restore only recorded service IDs."""
    validate(contract)
    tables = {}
    for name, count in (('passwd',7),('group',4)):
        rows = [line.split(':') for line in read(root,'etc/'+name,65536)[0].decode().splitlines()]
        if any(len(row)!=count for row in rows) or len({row[0] for row in rows})!=len(rows):
            raise RuntimeError('router image account table is malformed')
        tables[name] = rows
    users, groups = tables['passwd'], tables['group']; owners = contract['accounts']
    managed = {'dnsmasq','tailscale'}
    if owners['dnsmasq_gid'] == owners['tailscale_gid']:
        raise RuntimeError('retained router service groups must be distinct')
    for name in ('dnsmasq','tailscale'):
        selected = [row for row in groups if row[0]==name]
        gid = str(owners[name+'_gid'])
        if len(selected)!=1 or any(row[0] not in managed and row[2]==gid for row in groups):
            raise RuntimeError('retained router group conflicts with an image account: '+name)
        old_gid=selected[0][2]
        if any(row[0] not in managed and row[3]==old_gid for row in users):
            raise RuntimeError('router service group is shared with another image account: '+name)
        selected[0][2]=gid
        for row in users:
            if row[0]==name:
                if row[6]!='/sbin/nologin': raise RuntimeError('router service has an unsupported login shell')
                row[3]=gid
    selected=[row for row in users if row[0]=='dnsmasq']; uid=str(owners['dnsmasq_uid'])
    if len(selected)!=1 or any(row[0] not in managed and row[2]==uid for row in users):
        raise RuntimeError('retained dnsmasq UID conflicts with an image account')
    tail = [row for row in users if row[0]=='tailscale']
    if tail and tail[0][2] == uid:
        # Tailscaled runs as root and its retained files are root-owned. Its
        # package account can take the vacated dnsmasq UID when package order
        # changed between releases. Never renumber an unrelated image account.
        tail[0][2] = selected[0][2]
    selected[0][2]=uid
    if (len({row[2] for row in users}) != len(users) or
            len({row[2] for row in groups}) != len(groups)):
        raise RuntimeError('router service account reconstruction has conflicting numeric IDs')
    # All conflicts are checked before either table is written. Repeating this
    # operation after interruption restores the same approved definitions.
    for name, rows in tables.items():
        write(root,'etc/'+name, ('\n'.join(':'.join(row) for row in rows)+'\n').encode(),0o644)
    account_check(root,contract)


def reconstruct(value, source, *, root=Path('/')):
    """Use the frozen router recipe, never old configuration or account tables.

    The common router image must include this module and the existing
    router_personalize renderer. Legacy router-alpine-v2 images cannot use it.
    """
    isolated()
    import router_personalize
    config = value['router_configuration']
    router_personalize.validate(config)
    marker = json.loads(read(root, 'etc/klokast-template.json', 65536)[0])
    if (marker.get('profile') != 'router-alpine-v3' or marker.get('inputs_sha256') != config['inputs_sha256'] or
            router_personalize.packages(root) != config['packages']):
        raise RuntimeError('router reconstruction differs from its frozen image inputs')
    users = read(root, 'etc/passwd', 65536)[0].decode().splitlines()
    if not any(line.startswith('neo:') for line in users):
        for name, (content, mode) in router_personalize.account_files(root).items():
            router_personalize.put(root, name, content, mode)
    # Atomic account-file writes can be interrupted between tables. Refuse a
    # partial account on retry; an unbooted clone can be abandoned and rebuilt.
    for table, expected in (
            ('passwd', 'neo:x:1000:1000:Platform administrator:/home/neo:/bin/ash'),
            ('group', 'neo:x:1000:'), ('shadow', 'neo:!:0:0:99999:7:::')):
        rows = read(root, 'etc/' + table, 65536)[0].decode().splitlines()
        if [line for line in rows if line.startswith('neo:')] != [expected]:
            raise RuntimeError('router management account is incomplete; rebuild the unbooted clone')
    recreate_accounts(root, value['router_state'])
    modes = router_personalize.file_modes(config['files'])
    for name, content in config['files'].items():
        router_personalize.put(root, name, content, modes[name])
    router_personalize.put(root, 'etc/doas.d/20-klokast.conf', 'permit nopass :wheel\n', 0o600)
    home = router_personalize.directory(root, 'home/neo', create=True)
    os.chown(home, 1000, 1000); os.chmod(home, 0o700)
    level = router_personalize.directory(root, 'etc/runlevels/default', create=True)
    if any(target.name not in router_personalize.SERVICES for target in level.iterdir()):
        raise RuntimeError('router image enables a service outside its frozen recipe')
    for name in router_personalize.SERVICES:
        router_personalize.regular(root, 'etc/init.d/' + name)
        target = level / name
        if target.is_symlink() and os.readlink(target) == '/etc/init.d/' + name:
            continue
        if target.exists() or target.is_symlink():
            raise RuntimeError('router image has an unexpected runlevel entry')
        target.symlink_to('/etc/init.d/' + name)
    storage = value['router_storage']
    prepare(value['router_state'], storage['mode'], storage['seed_id'],
            source=source if storage['mode'] == 'migrate' else None, root=root)


def verify(*, root=Path('/'), data=DATA, mounts=None):
    contract = validate(json.loads(read(root, ROOT_RECORD, 65536)[0]))
    account_check(root, contract)
    rows = mounts if mounts is not None else [line.split() for line in Path('/proc/mounts').read_text().splitlines()]
    selected = [row for row in rows if row[1] == str(data)]
    if (len(selected) != 1 or selected[0][2] != 'ext4' or 'rw' not in selected[0][3].split(',') or
            filesystem_uuid(selected[0][0]) != contract['filesystem_uuid']):
        raise RuntimeError('router retained filesystem is missing, read-only or incorrect; services must stay stopped')
    value = attach(data, contract)
    for stored, target in bindings(value):
        target = Path(root) / target
        if not any(row[1] == str(target) for row in rows) or not target.samefile(Path(data) / stored):
            raise RuntimeError('router state bind mount is missing or incorrect: ' + str(target))
    return dict(contract, mounted=True)


if __name__ == '__main__':
    import sys
    try:
        if sys.argv[1:] != ['--verify']:
            raise RuntimeError('Expected --verify inside the router')
        print(json.dumps(verify(), sort_keys=True))
    except (RuntimeError, OSError, ValueError) as error:
        print('router storage: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
