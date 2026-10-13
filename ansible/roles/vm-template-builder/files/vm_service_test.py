"""VPN finalization and OpenRC fixture, only in the disposable Xen test VM."""
import fcntl
import grp
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import pwd
import stat
import struct
import time

UID, GID = 2002, 2003
CHECKS = dict.fromkeys(('numeric_identity', 'file_access', 'openrc_service', 'state_copy'), True)
DAEMON = '''#!/usr/bin/python3
import json, os, pathlib, time
p = pathlib.Path
assert os.getuid() == 2002 and os.getgid() == 2003
assert json.loads(p('/etc/vpn-egress/config.yml').read_text()) == {'fixture': True}
assert not p('/var/lib/vpn-egress/retained').exists()
assert not p('/opt/old-vpn-drift').exists()
assert not p('/var/lib/klokast-service-origin').exists()
p('/var/lib/vpn-egress/probe.json').write_text(json.dumps({
    'uid': os.getuid(), 'gid': os.getgid(),
    'boot_id': p('/proc/sys/kernel/random/boot_id').read_text().strip()}))
while True: time.sleep(1)
'''


def load_finalizer():
    loader = importlib.machinery.SourceFileLoader('service_finalizer', '/usr/local/libexec/klokast-service-finalize')
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def prepare(run):
    """Run the production finalizer against an owned, read-only synthetic disk."""
    import retained_data as data
    data.environment()
    if int(Path('/sys/class/block/xvdd/size').read_text()) * 512 != 256 * 1024**2:
        raise RuntimeError('service fixture requires its disposable 256 MiB source disk')
    if any(row['source'].startswith('/dev/xvdd') for row in data.mount_records()):
        raise RuntimeError('service fixture source is already mounted')
    source = Path('/mnt/service-fixture')
    source.mkdir(mode=0o700)
    with open('/dev/xvdd', 'r+b', buffering=0) as disk:
        disk.write(b'\0' * (4 * 1024**2)); os.fsync(disk.fileno())
        fcntl.ioctl(disk.fileno(), 0x125f)
    run(['mkfs.ext4', '-F', '/dev/xvdd'])
    run(['mount', '/dev/xvdd', str(source)])
    finalizer = load_finalizer()
    try:
        files = json.loads(Path('/usr/local/libexec/personalization-config.json').read_text())
        files.update({
            'etc/hostname': 'boxa-vpn-egress\n',
            'etc/passwd': Path('/etc/passwd').read_text() +
                'neo:x:1000:1000::/home/neo:/bin/ash\n' +
                f'vpn-egress:x:{UID}:{GID}::/var/lib/vpn-egress:/sbin/nologin\n',
            'etc/group': Path('/etc/group').read_text() + f'neo:x:1000:\nvpn-egress:x:{GID}:\n',
            'etc/shadow': Path('/etc/shadow').read_text() + 'neo:!:0:0:99999:7:::\nvpn-egress:!:0:0:99999:7:::\n',
            'etc/fstab': '/dev/xvda / ext4 defaults 0 1\n',
            'etc/network/if-up.d/10-klokast-dns': '#!/bin/sh\nexit 0\n',
            'etc/klokast/dns-filter.nft': '# No network in this test guest.\n',
            'etc/vpn-egress/config.yml': '{"fixture": true}\n',
            'etc/init.d/vpn-egress': Path('/usr/local/libexec/vpn-fixture.openrc').read_text(),
            'usr/local/bin/mihomo': '#!/bin/sh\necho old-binary-must-not-run >&2\nexit 99\n',
            'opt/old-vpn-drift': 'unrelated old state\n',
            'etc/init.d/old-vpn-service': '#!/bin/sh\nexit 99\n',
            'lib/apk/db/installed': 'P:old-extra-package\nV:1\n',
            'var/lib/tailscale/tailscaled.state': '{}\n',
            'etc/machine-id': '11111111111111111111111111111111\n',
            'etc/ssh/ssh_host_ed25519_key': 'synthetic private host key\n',
            'etc/ssh/ssh_host_ed25519_key.pub': 'synthetic public host key\n',
            'var/lib/vpn-egress/retained': 'retained service data\n',
        })
        for name, content in files.items():
            path = source / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            path.chmod(0o755 if name.startswith(('etc/init.d/', 'etc/network/if-up.d/', 'usr/local/bin/')) else 0o644)
        (source/'etc/shadow').chmod(0o600)
        for name, uid, gid, mode in (('etc/vpn-egress', 0, GID, 0o750),
                                    ('etc/vpn-egress/config.yml', 0, GID, 0o640),
                                    ('var/lib/vpn-egress', UID, GID, 0o700),
                                    ('var/lib/vpn-egress/retained', UID, GID, 0o600),
                                    ('home/neo', 1000, 1000, 0o700)):
            path = source/name
            if not path.exists(): path.mkdir(parents=True)
            os.chown(path, uid, gid); path.chmod(mode)
        value = {'kind': 'klokast.vm-service-finalize.v1', 'box': 'boxa', 'role': 'vpn-egress',
                 'root_partition': '', 'accounts': {'neo': [1000, 1000], 'vpn-egress': [UID, GID]},
                 'source_files': {}}
        # These failures must occur before any account mutation.
        before = {name: Path('/etc/'+name).read_bytes() for name in ('passwd', 'group', 'shadow')}
        saved_old = finalizer.OLD; finalizer.OLD = source
        group_path = source/'etc/group'; original = group_path.read_text()
        cases = (original.replace(f'vpn-egress:x:{GID}:\n', ''),
                 original + 'fixture-extra:x:2004:vpn-egress\n',
                 original + f'duplicate:x:{GID}:\n',
                 original.replace(f'vpn-egress:x:{GID}:', 'vpn-egress:x:0:'))
        try:
            for content in cases:
                group_path.write_text(content)
                try: finalizer.accounts(value)
                except (RuntimeError, KeyError): pass
                else: raise RuntimeError('service finalizer accepted an invalid group database')
                if any(Path('/etc/'+name).read_bytes() != content for name, content in before.items()):
                    raise RuntimeError('invalid service groups changed account state')
            group_path.write_text(original)
            bad = dict(value, accounts={'neo': [1000, 1000], 'vpn-egress': [UID, 65533]})
            try: finalizer.accounts(bad)
            except RuntimeError: pass
            else: raise RuntimeError('service finalizer accepted a different primary group')
            run(['addgroup', '-g', str(GID), 'service-fixture-conflict'])
            try:
                try: finalizer.accounts(value)
                except RuntimeError: pass
                else: raise RuntimeError('service finalizer accepted a reused numeric group')
                if any(Path('/etc/'+name).read_bytes() != before[name] for name in ('passwd', 'shadow')):
                    raise RuntimeError('conflicting service group changed accounts')
            finally:
                run(['delgroup', 'service-fixture-conflict'])
        finally:
            group_path.write_text(original); finalizer.OLD = saved_old
        names = ('etc/hostname', 'etc/passwd', 'etc/group', 'etc/shadow', 'etc/fstab',
                 'etc/nftables.nft', 'etc/network/interfaces', 'etc/network/if-up.d/10-klokast-dns',
                 'etc/klokast/dns-filter.nft', 'etc/vpn-egress/config.yml')
        value['source_files'] = {name: hashlib.sha256((source/name).read_bytes()).hexdigest() for name in names}
    finally:
        run(['umount', str(source)])
    with open('/dev/xvdd', 'rb', buffering=0) as disk:
        fcntl.ioctl(disk.fileno(), 0x125d, struct.pack('i', 1))
    value['vpn_machine_id_sha256']=hashlib.sha256(files['etc/machine-id'].encode()).hexdigest()
    value['vpn_creation'] = {'kind':'klokast.infrastructure-config.v1','box':'boxa','role':'vpn-egress',
        'address':'10.100.30.12/24','gateway':'10.100.30.1','bridge':'br-dmz','bootstrap_source':'10.100.10.2',
        'public_key':'ssh-ed25519 ZmFrZS1rZXk=', 'agent_uid':1004,'agent_gid':1004, 'bootstrap_dns':['1.1.1.1','8.8.8.8']}
    try:
        finalizer.finalize(finalizer.validate(value))
        if Path('/usr/local/bin/mihomo').exists() or Path('/etc/init.d/vpn-egress').exists() or Path('/etc/init.d/old-vpn-service').exists():
            raise RuntimeError('clean VPN reconstruction copied old executable code')
        if any(Path(p).exists() for p in ('/opt/old-vpn-drift','/var/lib/vpn-egress/retained','/var/lib/klokast-service-origin')):
            raise RuntimeError('clean VPN reconstruction copied unrelated state')
        if 'old-extra-package' in Path('/lib/apk/db/installed').read_text():
            raise RuntimeError('clean VPN reconstruction copied package drift')
        # Install public fixture artifacts in place of the controller-private production inputs.
        for name, content, mode in (('/usr/local/bin/mihomo',DAEMON,0o755),
                ('/etc/init.d/vpn-egress',Path('/usr/local/libexec/vpn-fixture.openrc').read_text(),0o755),
                ('/etc/vpn-egress/config.yml','{"fixture":true}\n',0o640)):
            path=Path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content);path.chmod(mode)
        os.chown('/etc/vpn-egress/config.yml',0,GID);os.chown('/etc/vpn-egress',0,GID);Path('/etc/vpn-egress').chmod(0o750)
        state=Path('/var/lib/vpn-egress');state.mkdir(mode=0o700);os.chown(state,UID,GID)
        # This qualification guest deliberately has no NIC or usable credentials.
        Path('/etc/network/interfaces').write_text('auto lo\niface lo inet loopback\n')
        run(['rc-update','del','tailscale','default'])
        run(['rc-update','add','vpn-egress','default'])
    finally:
        with open('/dev/xvdd', 'rb', buffering=0) as disk:
            fcntl.ioctl(disk.fileno(), 0x125d, struct.pack('i', 0))
    Path('/etc/klokast-service-test.json').write_text(json.dumps(value, sort_keys=True)+'\n')
    return {'finalized': True, 'invalid_groups_refused': True, 'clean_root': True, 'production_data_used': False}


def verify(run):
    user = pwd.getpwnam('vpn-egress'); group = grp.getgrnam('vpn-egress')
    if (user.pw_uid, user.pw_gid, group.gr_gid) != (UID, GID, GID) or set(os.getgrouplist(user.pw_name, user.pw_gid)) != {GID}:
        raise RuntimeError('finalized VPN account identity differs')
    for name, expected in (('/etc/vpn-egress/config.yml', (0, GID, 0o640)),
                            ('/var/lib/vpn-egress', (UID, GID, 0o700))):
        info = Path(name).stat()
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != expected:
            raise RuntimeError('finalized VPN file ownership differs: '+name)
    request = json.loads(Path('/etc/klokast-service-test.json').read_text())
    copy = json.loads(Path('/etc/klokast-service-state.json').read_text())
    if copy['copy_verified'] is not True or copy['source_files'] != request['source_files']:
        raise RuntimeError('VPN retained-state proof differs')
    run(['/sbin/rc-service', 'vpn-egress', 'status'])
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    for _ in range(50):
        path = Path('/var/lib/vpn-egress/probe.json')
        if path.exists() and json.loads(path.read_text()).get('boot_id') == boot: break
        time.sleep(0.1)
    if json.loads(path.read_text()) != {'uid': UID, 'gid': GID, 'boot_id': boot}:
        raise RuntimeError('VPN test process did not start with the retained identity and file access')
    return {'tests': CHECKS, 'boot_id': boot, 'production_data_used': False}
