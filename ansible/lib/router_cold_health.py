"""Validate controller observations before recording cold router recovery.

A checksum binds the observation to one operation. It is not an outage grant;
the controller workflow must produce it after its complete Ansible playbook.
"""
import re
import ipaddress
import time

import router_cold_identity as identity
import router_cold_recovery as recovery
import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import TransactionError


def create(observation, now):
    """Return a bounded receipt for one fresh, complete recovery observation."""
    if (not isinstance(observation, dict) or
            set(observation) != {'kind', 'box', 'operation_id', 'engine_commit', 'observed_at',
                'baseline', 'identity', 'accepted_manifest', 'guest_status',
                'controller_status', 'direct_ping', 'routes'} or
            observation['kind'] != 'klokast.router-cold-recovery-observation.v1' or
            not generations.matches('[a-z0-9][a-z0-9-]{0,30}', observation['box']) or
            not generations.matches('[0-9a-f]{24}', observation['operation_id']) or
            not generations.matches('[0-9a-f]{40}', observation['engine_commit']) or
            type(now) is not int or type(observation['observed_at']) is not int or
            not observation['observed_at'] <= now <= observation['observed_at'] + 120):
        raise TransactionError('cold recovery observation is incomplete, stale, or for another source')
    box, operation, engine = (observation[key] for key in ('box', 'operation_id', 'engine_commit'))
    baseline, original = observation['baseline'], observation['identity']
    generations.check_seal(baseline)
    generations.check_seal(original)
    domains = baseline.get('domains')
    if (baseline.get('kind') != 'klokast.router-cold-dependent-baseline.v1' or
            original.get('kind') != 'klokast.router-cold-original-identity.v1' or
            any(value.get('box') != box or value.get('operation_id') != operation or
                value.get('engine_commit') != engine for value in (baseline, original)) or
            baseline.get('identity_sha256') != original['record_sha256'] or
            baseline.get('metadata_sha256') != original.get('metadata_sha256') or
            baseline.get('generation_sha256') != original.get('generation_sha256') or
            not isinstance(domains, dict) or not set(domains) <= recovery.KNOWN_ROUTED or
            any(not generations.matches('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value)
                for value in domains.values())):
        raise TransactionError('cold recovery observation differs from its saved original')
    manifest = observation['accepted_manifest']
    if (not isinstance(manifest, dict) or manifest.get('kind') != 'klokast.router-accepted-manifest.v1' or
            manifest.get('box') != box or manifest.get('generation_sha256') != original['generation_sha256'] or
            manifest.get('origin') != 'legacy' or manifest.get('tailscale') is not None or
            not isinstance(manifest.get('packages'), dict) or not manifest['packages'] or
            not isinstance(manifest.get('configuration_files'), dict) or
            not manifest['configuration_files']):
        raise TransactionError('cold recovery accepted manifest is not the saved original')
    live = identity.create(box, operation, engine, original['metadata_sha256'],
        original['generation_sha256'], observation['guest_status'],
        observation['controller_status'], observation['observed_at'],
        expected_machine_id=original.get('machine_id'))
    if live['machine_id'] != original.get('machine_id'):
        raise TransactionError('cold recovery live Tailnet identity changed from the original')
    peer = next(value for value in observation['controller_status']['Peer'].values()
                if isinstance(value, dict) and value.get('ID') == original['machine_id'])
    addresses = peer.get('TailscaleIPs')
    if (not isinstance(addresses, list) or not addresses or
            addresses != observation['guest_status']['Self'].get('TailscaleIPs') or
            any(not isinstance(address, str) for address in addresses)):
        raise TransactionError('cold recovery lacks matching addresses for the saved Tailnet identity')
    try:
        for address in addresses:
            ipaddress.ip_address(address)
    except ValueError as error:
        raise TransactionError('cold recovery saved Tailnet peer has an invalid address') from error
    direct = observation['direct_ping']
    reply = (re.search(r'pong from ' + re.escape(box) +
                r'-router \(([^()\s]+)\) via (?:\[[0-9a-fA-F:]+\]|[0-9.]+):[0-9]+ ', direct)
             if isinstance(direct, str) and len(direct) <= 4096 else None)
    if (not isinstance(direct, str) or len(direct) > 4096 or
            reply is None or reply.group(1) not in addresses):
        raise TransactionError('cold recovery lacks a direct controller peer reply')
    routes = observation['routes']
    if (not isinstance(routes, dict) or set(routes) != {box + '-' + name for name in domains}):
        raise TransactionError('cold recovery has missing or unexpected dependent routes')
    for name, uuid in domains.items():
        route = routes[box + '-' + name]
        if (not isinstance(route, dict) or set(route) != {'uuid', 'gateway', 'route', 'gateway_ping'} or
                route['uuid'] != uuid or
                not generations.matches(r'(?:[0-9]{1,3}\.){3}[0-9]{1,3}', route['gateway']) or
                not isinstance(route['route'], str) or len(route['route']) > 1024 or
                not re.search(r'\bvia ' + re.escape(route['gateway']) + r' dev eth0\b', route['route']) or
                not isinstance(route['gateway_ping'], str) or len(route['gateway_ping']) > 4096 or
                not re.search(r'1 packets transmitted, 1 (?:packets )?received', route['gateway_ping'])):
            raise TransactionError('cold recovery dependent route or gateway proof changed: ' + name)
    return generations.seal({'kind': 'klokast.router-cold-health.v1',
        'box': box, 'operation_id': operation, 'engine_commit': engine,
        'metadata_sha256': original['metadata_sha256'],
        'generation_sha256': original['generation_sha256'],
        'identity_sha256': original['record_sha256'],
        'baseline_sha256': baseline['record_sha256'],
        'accepted_manifest_sha256': generations.digest(manifest),
        'guest_status_sha256': live['guest_status_sha256'],
        'controller_status_sha256': live['controller_status_sha256'],
        'direct_ping_sha256': generations.digest(direct),
        'routes_sha256': generations.digest(routes),
        'observed_at': observation['observed_at']})


class Health:
    """Stage one controller health receipt without removing the boot fence."""

    def __init__(self, bundle):
        self.bundle, self.storage = bundle, bundle.storage
        self.baseline = recovery.Baseline(bundle)
        self.path = bundle.directory / 'health.json'
        self.input = bundle.directory / 'health-input.json'
        self.completion = bundle.directory / 'completion.json'

    def validate(self, value, metadata, generation, baseline, now):
        generations.check_seal(value)
        original = self.baseline.identity.verify()
        manifest = {'kind': 'klokast.router-accepted-manifest.v1', 'box': self.storage.box,
            'generation_sha256': generation['record_sha256'],
            'kernel_release': generation['kernel_release'], 'origin': generation['origin'],
            'tailscale': generation.get('tailscale'), 'packages': generation['packages'],
            'configuration_files': generation['configuration_files']}
        if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit', 'metadata_sha256',
                'generation_sha256', 'identity_sha256', 'baseline_sha256',
                'accepted_manifest_sha256', 'guest_status_sha256',
                'controller_status_sha256', 'direct_ping_sha256', 'routes_sha256',
                'observed_at', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-health.v1' or
                value['box'] != self.storage.box or value['operation_id'] != self.bundle.operation or
                value['engine_commit'] != self.bundle.engine or
                value['metadata_sha256'] != metadata['record_sha256'] or
                value['generation_sha256'] != generation['record_sha256'] or
                value['identity_sha256'] != original['record_sha256'] or
                value['baseline_sha256'] != baseline['record_sha256'] or
                value['accepted_manifest_sha256'] != generations.digest(manifest) or
                any(not generations.matches('[0-9a-f]{64}', value[key]) for key in (
                    'guest_status_sha256', 'controller_status_sha256',
                    'direct_ping_sha256', 'routes_sha256')) or
                type(now) is not int or type(value['observed_at']) is not int or
                not value['observed_at'] <= now <= value['observed_at'] + 120):
            raise TransactionError('cold recovery health receipt is stale or differs from the restored original')
        return value

    def stage(self, boot_check, *, now=None):
        """Require the current boot assignment and Xen set before staging."""
        now = int(time.time()) if now is None else now
        with self.storage.lock():
            metadata, generation, baseline = self.baseline.restored()
            if boot_check() != 'accepted-assignment-verified':
                raise TransactionError('cold recovery boot assignment is not the restored original')
            value = self.validate(records.read(self.input), metadata, generation, baseline, now)
            if self.path.exists() or self.path.is_symlink():
                prior = records.read(self.path)
                self.validate(prior, metadata, generation, baseline, prior['observed_at'])
                if value['observed_at'] < prior['observed_at']:
                    raise TransactionError('cold recovery health retry moved back to an older observation')
                if prior == value:
                    return value
            records.write(self.path, value)
            return value

    def clear_fence(self, boot_check, *, now=None):
        """Remove the persistent boot fence only after fresh full recovery."""
        now = int(time.time()) if now is None else now
        with self.storage.lock():
            marker = self.storage.cold_test()
            metadata, generation, baseline = self.baseline.restored(fenced=marker is not None)
            if boot_check() != 'accepted-assignment-verified':
                raise TransactionError('cold recovery boot assignment is not the restored original')
            if native.command(['/usr/sbin/lbu', 'status'], time.monotonic() + 30).strip():
                raise TransactionError('cold recovery requires clean persisted dom0 boot files')
            if marker is None:
                if not self.completion.exists() and not self.completion.is_symlink():
                    raise TransactionError('cold recovery boot fence is absent without its completion record')
                value = records.read(self.completion)
                generations.check_seal(value)
                health = records.read(self.path)
                self.validate(health, metadata, generation, baseline, health['observed_at'])
                if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit',
                        'metadata_sha256', 'generation_sha256', 'identity_sha256',
                        'baseline_sha256', 'health_sha256', 'marker_sha256',
                        'cleared_at', 'record_sha256'} or
                        value['kind'] != 'klokast.router-cold-completion.v1' or
                        value['box'] != self.storage.box or value['operation_id'] != self.bundle.operation or
                        value['engine_commit'] != self.bundle.engine or
                        value['metadata_sha256'] != metadata['record_sha256'] or
                        value['generation_sha256'] != generation['record_sha256'] or
                        value['identity_sha256'] != self.baseline.identity.verify()['record_sha256'] or
                        value['baseline_sha256'] != baseline['record_sha256'] or
                        value['health_sha256'] != health['record_sha256'] or
                        type(value['cleared_at']) is not int or value['cleared_at'] > now or
                        not generations.matches('[0-9a-f]{64}', value['marker_sha256'])):
                    raise TransactionError('cold recovery completion differs from the restored original')
                return value
            health = self.validate(records.read(self.path), metadata, generation, baseline, now)
            value = generations.seal({'kind': 'klokast.router-cold-completion.v1',
                'box': self.storage.box, 'operation_id': self.bundle.operation,
                'engine_commit': self.bundle.engine,
                'metadata_sha256': metadata['record_sha256'],
                'generation_sha256': generation['record_sha256'],
                'identity_sha256': health['identity_sha256'],
                'baseline_sha256': baseline['record_sha256'],
                'health_sha256': health['record_sha256'],
                'marker_sha256': marker['record_sha256'], 'cleared_at': now})
            if self.completion.exists() or self.completion.is_symlink():
                prior = records.read(self.completion)
                generations.check_seal(prior)
                if (prior.get('kind') != value['kind'] or
                        any(prior.get(key) != value[key] for key in (
                            'box', 'operation_id', 'engine_commit', 'metadata_sha256',
                            'generation_sha256', 'identity_sha256', 'baseline_sha256',
                            'marker_sha256')) or
                        prior.get('cleared_at', now + 1) > now):
                    raise TransactionError('cold recovery prior completion targets another original')
            records.write(self.completion, value)
            self.storage.base.joinpath('cold-test.json').unlink()
            records.syncdir(self.storage.base)
            if self.storage.cold_test() is not None:
                raise TransactionError('cold recovery boot fence remains after its removal')
            return value
