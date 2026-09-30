"""Native old/new/old DHCP tests, after isolated fixture sanitization.

The fixture marker binds a single operation and exact software. Only synthetic
identity-fixture digests and lease metadata leave this guest. No control-plane enrollment or
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


def state(phase):
    values = router_state.snapshot(Path('/'), **router_fixture.accounts(Path('/')))
    generation = 'candidate' if phase == 'new' else 'legacy'
    identity = {}
    for relative in router_state.IDENTITY:
        data, _ = router_state.read(Path('/'), relative)
        if data != (generation + ':' + relative).encode():
            raise RuntimeError('compatibility copy changed a generation-local identity')
        identity[relative] = checksum(data)
    return {'files': router_state.evidence(values), 'generation_identity': identity,
            'lan_leases': [row.split() for row in Path('/var/lib/misc/dnsmasq.leases').read_text().splitlines()]}


def validate_copy(expected, phase):
    current = state(phase)
    if (current['lan_leases'] != expected['lan_leases'] or
            current['files'].keys() != expected['files'].keys()):
        raise RuntimeError('synthetic DHCP state differs after the fixed copy')
    # Identity is checked against this generation's own fixture by state().
    # Account translation is checked by the copy helper. Bytes and age cannot change.
    for name, metadata in current['files'].items():
        for field in ('sha256', 'bytes', 'mode', 'mtime_ns'):
            if metadata[field] != expected['files'][name][field]:
                raise RuntimeError('synthetic state bytes, mode or timestamp changed during copying')
    for relative in router_state.WAN_CACHE:
        path = Path('/') / relative
        if path.exists() or path.is_symlink():
            raise RuntimeError('compatibility destination retains a WAN lease cache')
    return current


def execute(phase, request, expected=None):
    router_personalize.environment()
    if (phase not in ('seed', 'new', 'old') or request.get('kind') != 'klokast.router-compatibility-request.v2' or
            not re.fullmatch('[0-9a-f]{24}', request.get('operation_id', '')) or
            Path('/etc/hostname').read_text() != 'boxa-router\n'):
        raise RuntimeError('router compatibility requires the exact isolated fixture')
    wanted = request['source_packages'] if phase in ('seed', 'old') else request['runtime_packages']
    if router_personalize.packages(Path('/')) != wanted:
        raise RuntimeError('router compatibility fixture does not have the exact selected software')
    before = None
    if phase == 'seed':
        for relative in router_state.PATHS:
            if (Path('/') / relative).exists() or (Path('/') / relative).is_symlink():
                raise RuntimeError('router compatibility seed still contains identity state')
    else:
        if not isinstance(expected, dict):
            raise RuntimeError('router compatibility lost its previous phase evidence')
        before = validate_copy(expected, phase)
    probe.WORK.mkdir(mode=0o700)
    try:
        probe.network()
        probe.run(['nft', '-f', '/etc/nftables.nft'])
        tests = probe.dhcp_dns(2 if phase == 'seed' else 3, seed_expiry=phase == 'seed', verify_expiry=phase == 'new')
        after = state(phase)
        if before is not None:
            for relative in ('var/lib/dhcpcd/duid', 'var/lib/dhcpcd/secret'):
                if before['files'][relative]['sha256'] != after['files'][relative]['sha256']:
                    raise RuntimeError('native DHCP client changed the copied DUID or privacy secret')
            if before['generation_identity'] != after['generation_identity']:
                raise RuntimeError('native DHCP test changed a generation-local identity')
            tests.update(dhcp_identity_continuity=True, copied_timestamps=True,
                         generation_identity_preserved=True, fresh_wan_negotiation=True)
        return {'tests': tests, 'state': after, 'packages': wanted,
                'production_identity': False, 'enrollment_tested': False}
    finally:
        probe.run(['ip', 'netns', 'delete', 'router-probe-upstream'], check=False)
