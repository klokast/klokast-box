"""Disposable Xen filesystem fixture for the future common router image.

Requires an anonymous router test guest, no NICs, and a new zeroed 1 GiB LV at
xvde. This is a guest payload, never a dom0 or controller command. It tests
native ext4 and bind mounts; it does not qualify real Tailscale or router traffic.
The common router builder must run it before enabling its public profile.
"""
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import tempfile
import uuid

import vm_router_state as state


def exercise():
    state.isolated()
    if state.filesystem_uuid('/dev/xvde'):
        raise RuntimeError('router fixture requires a fresh disposable data disk')
    work = Path(tempfile.mkdtemp(prefix='router-state-fixture-'))
    source = work / 'old'; source.mkdir()
    accounts = dict(dnsmasq_uid=pwd.getpwnam('dnsmasq').pw_uid,
                    dnsmasq_gid=grp.getgrnam('dnsmasq').gr_gid,
                    tailscale_gid=grp.getgrnam('tailscale').gr_gid)
    contract = dict(kind=state.KIND, box='fixture', filesystem_uuid=str(uuid.uuid4()), accounts=accounts)
    seed = uuid.uuid4().hex[:24]
    for name, (_, _, required) in state.FILES.items():
        if required or name in ('etc/machine-id', 'etc/ssh/ssh_host_ed25519_key',
                                'var/lib/tailscale/ssh/ssh_host_ed25519_key'):
            state.write(source, name, ('synthetic ' + name).encode())
    before = state.evidence(state.snapshot(source, contract))
    latest = b'synthetic native state after candidate writes'
    for number in range(2):
        root = work / ('root-' + str(number)); root.mkdir()
        for name in ('passwd', 'group'):
            state.write(root, 'etc/' + name, Path('/etc/' + name).read_bytes(), 0o644)
        state.write(root, 'etc/dnsmasq.conf', b'dhcp-leasefile=/var/lib/misc/dnsmasq.leases\n', 0o644)
        state.prepare(contract, 'migrate' if number == 0 else 'attach', seed,
                      source=source if number == 0 else None, root=root)
        state.run(['mount', '-t', 'ext4', '/dev/xvde', str(state.DATA)])
        mounted = []
        try:
            value = state.attach(state.DATA, contract)
            if number == 0:
                if state.evidence(state.snapshot(state.DATA, contract, retained=True)) != before:
                    raise RuntimeError('native router copy differs from synthetic source')
            elif (state.DATA / 'tailscale/tailscaled.state').read_bytes() != latest:
                raise RuntimeError('later root lost the latest retained state')
            for stored, target in state.bindings(value):
                target = root / target
                state.run(['mount', '--bind', str(state.DATA / stored), str(target)])
                mounted.append(target)
            state.verify(root=root)
            state.write(state.DATA, 'tailscale/tailscaled.state', latest)
            state.write(state.DATA, 'dnsmasq/dnsmasq.leases', b'latest synthetic DHCP lease')
            # A missing bind must stop verification even with the correct disk.
            state.run(['umount', str(mounted[-1])]); mounted.pop()
            try:
                state.verify(root=root)
            except RuntimeError:
                pass
            else:
                raise RuntimeError('router accepted a missing identity bind mount')
        finally:
            for target in reversed(mounted): state.run(['umount', str(target)])
            state.run(['umount', str(state.DATA)])
        try:
            state.verify(root=root)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('router accepted a missing retained filesystem')
    # Failed fixtures leave their tree for inspection. Do not recursively
    # remove a tree until every native mount has been released successfully.
    shutil.rmtree(work)
    return {'kind': 'klokast.router-state-fixture.v1', 'success': True,
            'checks': ['native-copy', 'two-roots', 'latest-state', 'missing-bind', 'missing-data']}


if __name__ == '__main__':
    print(json.dumps(exercise(), sort_keys=True))
