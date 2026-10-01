"""Pin the non-router Xen guests across a supervised router-only outage."""
import time

import router_cold_identity as cold_identity
import router_generations as generations
import router_records as records
from router_transaction import TransactionError

KNOWN_ROUTED = frozenset({'bak', 'dmz', 'iot', 'ops'})


class Baseline:
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.path = bundle.directory / 'dependent-baseline.json'
        self.identity = cold_identity.Identity(bundle)

    def domains(self, generation):
        observed, router_seen = {}, False
        for entry in self.host.inventory(deadline=time.monotonic() + 30):
            if entry['domid'] == 0:
                continue
            info = entry['config']['c_info']
            name, identity = info['name'], info['uuid']
            if name == 'router':
                if router_seen or identity != generation['xen']['uuid']:
                    raise TransactionError('cold router baseline found another router Xen identity')
                router_seen = True
                continue
            if (not generations.matches('[A-Za-z0-9_-]{1,64}', name) or
                    not generations.matches('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', identity) or
                    len(observed) >= 64 or name in observed):
                raise TransactionError('cold router baseline has an unsafe dependent Xen inventory')
            observed[name] = identity
        if not router_seen:
            raise TransactionError('cold router baseline did not find its exact Xen guest')
        return observed

    def validate(self, value, metadata, generation, identity):
        generations.check_seal(value)
        domains = value.get('domains')
        if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit',
                'metadata_sha256', 'generation_sha256', 'identity_sha256',
                'domains', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-dependent-baseline.v1' or
                value['box'] != self.storage.box or value['operation_id'] != self.bundle.operation or
                value['engine_commit'] != self.bundle.engine or
                value['metadata_sha256'] != metadata['record_sha256'] or
                value['generation_sha256'] != generation['record_sha256'] or
                value['identity_sha256'] != identity['record_sha256'] or
                not isinstance(domains, dict) or len(domains) > 64 or
                any(not generations.matches('[A-Za-z0-9_-]{1,64}', name) or
                    not generations.matches('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', uuid)
                    for name, uuid in domains.items()) or
                len(set(domains.values())) != len(domains)):
            raise TransactionError('cold router dependent baseline differs from its original source')
        return value

    def verify(self):
        metadata, generation = self.bundle.verify()
        identity = self.identity.verify()
        return self.validate(records.read(self.path), metadata, generation, identity)

    def capture(self):
        """Record the exact live guests before the original router stops."""
        with self.storage.lock():
            self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
            metadata, generation = self.bundle.verify()
            identity = self.identity.verify()
            if (self.storage.cold_test() is not None or self.storage.pending() is not None or
                    self.storage.installation() is not None or
                    self.storage.accepted() != records.read(self.bundle.directory / 'accepted.json') or
                    self.host.guest({'accepted': generation}, deadline=time.monotonic() + 30) is None):
                raise TransactionError('cold dependent baseline requires the live accepted original')
            value = generations.seal({'kind': 'klokast.router-cold-dependent-baseline.v1',
                'box': self.storage.box, 'operation_id': self.bundle.operation,
                'engine_commit': self.bundle.engine,
                'metadata_sha256': metadata['record_sha256'],
                'generation_sha256': generation['record_sha256'],
                'identity_sha256': identity['record_sha256'],
                'domains': self.domains(generation)})
            if self.path.exists() or self.path.is_symlink():
                if records.read(self.path) != value:
                    raise TransactionError('cold dependent baseline retry found changed Xen guests')
            else:
                records.write(self.path, value)
            return self.verify()

    def restored(self, *, fenced=True):
        """Check the original and guest set while the caller holds the lock."""
        self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
        metadata, generation = self.bundle.verify()
        baseline = self.verify()
        marker = self.storage.cold_test()
        if ((fenced and (marker is None or marker['operation_id'] != self.bundle.operation or
                marker['engine_commit'] != self.bundle.engine or marker['phase'] != 'restoring' or
                marker['metadata_sha256'] != metadata['record_sha256'] or
                marker['generation_sha256'] != generation['record_sha256'])) or
                (not fenced and marker is not None) or
                self.storage.pending() is not None or self.storage.installation() is not None or
                self.storage.accepted() != records.read(self.bundle.directory / 'accepted.json') or
                self.host.guest({'accepted': generation}, deadline=time.monotonic() + 30) is None):
            raise TransactionError('cold dependent recovery requires the restored accepted router')
        if self.domains(generation) != baseline['domains']:
            raise TransactionError('cold recovery changed the running non-router Xen guests')
        return metadata, generation, baseline

    def verify_restored(self):
        """Require the same other guests after the original is back online."""
        with self.storage.lock():
            return self.restored()[2]
