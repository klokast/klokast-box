"""Native old/new/old service tests, after isolated fixture sanitization.

The fixture marker binds a single operation and exact software. Only synthetic
key digests and lease metadata leave this guest. No control-plane enrollment or
production router identity is part of this test.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import router_fixture
import router_personalize
import router_service_probe as probe
import router_state


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def state():
    values = router_state.snapshot(Path('/'), **router_fixture.accounts(Path('/')))
    files = router_state.evidence(values)
    fingerprints = {}
    for kind in router_state.KEY_TYPES:
        fingerprints[kind] = probe.run(['ssh-keygen', '-lf', '/etc/ssh/ssh_host_' + kind + '_key']).stdout.split()[1]
    saved = json.loads(Path('/var/lib/tailscale/tailscaled.state').read_text())
    machine = saved.get('_machinekey')
    if not isinstance(machine, str) or not machine:
        raise RuntimeError('synthetic Tailscale state has no native machine key')
    return {'files': files, 'ssh_fingerprints': fingerprints,
            'machine_key_sha256': checksum(machine.encode()),
            'lan_leases': [row.split() for row in Path('/var/lib/misc/dnsmasq.leases').read_text().splitlines()]}


def validate_copy(expected):
    current = state()
    if (current['ssh_fingerprints'] != expected['ssh_fingerprints'] or
            current['machine_key_sha256'] != expected['machine_key_sha256'] or
            current['lan_leases'] != expected['lan_leases'] or current['files'].keys() != expected['files'].keys()):
        raise RuntimeError('synthetic identity or lease state differs after the fixed copy')
    # Service-account numbers may differ between package closures. The copy
    # helper validates that translation. Content, modes and lease ages cannot.
    for name, metadata in current['files'].items():
        for field in ('sha256', 'bytes', 'mode', 'mtime_ns'):
            if metadata[field] != expected['files'][name][field]:
                raise RuntimeError('synthetic state bytes, mode or timestamp changed during copying')
    return current


def resume_tailscale(phase):
    socket = str(probe.WORK / 'tailscale.sock')
    cli = ['tailscale', '--socket=' + socket]
    daemon = ['tailscaled', '--tun=userspace-networking', '--port=0', '--socket=' + socket,
              '--state=/var/lib/tailscale/tailscaled.state']
    old_hostname = 'router-probe-latest' if phase == 'new' else 'router-probe-candidate'
    with probe.process('resume-tailscale', daemon) as child:
        probe.wait_for(lambda: Path(socket).exists(), [child], 'native persisted Tailscale profile')
        prefs = json.loads(probe.run([*cli, 'debug', 'prefs']).stdout)
        if prefs.get('RunSSH') is not True or prefs.get('Hostname') != old_hostname:
            raise RuntimeError('native Tailscale could not read the previous writer profile')
        if phase == 'new':
            probe.run([*cli, 'set', '--hostname=router-probe-candidate'])
    if phase == 'new':
        # A second native daemon creates the new machine key. The development
        # store API exercises the ordinary file store with those native bytes.
        # This is an offline storage test, not a Tailnet key-rotation protocol.
        rotation_socket = str(probe.WORK / 'rotation.sock')
        rotation_state = probe.WORK / 'rotation.state'
        with probe.process('rotation-source', ['tailscaled', '--tun=userspace-networking', '--port=0',
                '--socket=' + rotation_socket, '--state=' + str(rotation_state)]) as child:
            probe.wait_for(lambda: Path(rotation_socket).exists(), [child], 'native rotation fixture')
            probe.run(['tailscale', '--socket=' + rotation_socket, 'up',
                       '--login-server=https://127.0.0.1:1', '--timeout=2s'], check=False, timeout=10)
            new_key = json.loads(rotation_state.read_text())['_machinekey']
        with probe.process('rotate-tailscale', daemon) as child:
            probe.wait_for(lambda: Path(socket).exists(), [child], 'native state writer')
            probe.run([*cli, 'debug', 'dev-store-set', '--danger', '_machinekey', '-'], data=new_key)
    with probe.process('verify-tailscale', daemon) as child:
        probe.wait_for(lambda: Path(socket).exists(), [child], 'native state restart')
        prefs = json.loads(probe.run([*cli, 'debug', 'prefs']).stdout)
        if prefs.get('RunSSH') is not True or prefs.get('Hostname') != 'router-probe-candidate':
            raise RuntimeError('native Tailscale did not retain the latest writer profile')
    return {'latest_tailscale_preferences': True, 'native_state_restart': True}


def execute(phase, request, expected=None):
    router_personalize.environment()
    if (phase not in ('seed', 'new', 'old') or request.get('kind') != 'klokast.router-compatibility-request.v1' or
            not re.fullmatch('[0-9a-f]{24}', request.get('operation_id', '')) or
            Path('/etc/hostname').read_text() != 'boxa-router\n'):
        raise RuntimeError('router compatibility requires the exact isolated fixture')
    wanted = request['source_packages'] if phase in ('seed', 'old') else request['runtime_packages']
    if router_personalize.packages(Path('/')) != wanted:
        raise RuntimeError('router compatibility fixture does not have the exact selected software')
    before = None
    if phase == 'seed':
        for relative in router_state.ALLOWLIST:
            if (Path('/') / relative).exists() or (Path('/') / relative).is_symlink():
                raise RuntimeError('router compatibility seed still contains identity state')
    else:
        if not isinstance(expected, dict):
            raise RuntimeError('router compatibility lost its previous phase evidence')
        before = validate_copy(expected)
    probe.WORK.mkdir(mode=0o700)
    try:
        probe.network()
        probe.run(['nft', '-f', '/etc/nftables.nft'])
        tests = probe.dhcp_dns(2 if phase == 'seed' else 3, seed_expiry=phase == 'seed', verify_expiry=phase == 'new')
        if phase == 'seed':
            probe.run(['ssh-keygen', '-A'])
            tests.update(probe.tailscale_state())
        else:
            tests.update(resume_tailscale(phase))
        after = state()
        if before is not None:
            for relative in ('var/lib/dhcpcd/duid', 'var/lib/dhcpcd/secret'):
                if before['files'][relative]['sha256'] != after['files'][relative]['sha256']:
                    raise RuntimeError('native DHCP client changed the copied DUID or privacy secret')
            if before['ssh_fingerprints'] != after['ssh_fingerprints']:
                raise RuntimeError('native service boot changed a copied SSH host key')
            rotated = before['machine_key_sha256'] != after['machine_key_sha256']
            if rotated != (phase == 'new'):
                raise RuntimeError('native Tailscale machine-key storage did not preserve the expected writer')
            tests.update(identity_continuity=True, latest_machine_key=True, copied_timestamps=True)
        return {'tests': tests, 'state': after, 'packages': wanted,
                'production_identity': False, 'control_plane_rotation_tested': False}
    finally:
        probe.run(['ip', 'netns', 'delete', 'router-probe-upstream'], check=False)
