"""Bind a future supervised K001 outage to one prepared original router.

The request is an input selector. The separate short controller grant records
explicit supervised outage approval for that exact selector.
"""
import time

import router_cold_disk as cold_disk
import router_cold_filesystem as cold_filesystem
import router_cold_recovery as cold_recovery
import router_generations as generations
import router_initial_installation as initial
import router_records as records
from router_transaction import TransactionError


def authorization(value, request, *, now=None):
    """A prepared request alone must never authorize stopping the router."""
    now = int(time.time()) if now is None else now
    generations.check_seal(value)
    if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit',
            'request_sha256', 'outage_authorized', 'granted_at', 'expires_at', 'record_sha256'} or
            value['kind'] != 'klokast.router-cold-outage-authorization.v1' or
            any(value[key] != request[key] for key in ('box', 'operation_id', 'engine_commit')) or
            value['request_sha256'] != request['record_sha256'] or
            value['outage_authorized'] is not True or
            type(value['granted_at']) is not int or type(value['expires_at']) is not int or
            not value['granted_at'] <= now < value['expires_at'] <= value['granted_at'] + 300):
        raise TransactionError('cold outage approval is stale or differs from its exact supervised request')
    return value


class Request:
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.path = bundle.directory / 'supervised-request.json'

    def validate(self, value, *, now=None):
        """Refuse a request that lost any exact pre-stop recovery input."""
        now = int(time.time()) if now is None else now
        self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
        generations.check_seal(value)
        pointer = initial.provision_pointer(value.get('initial_provision'), self.storage.box,
                                            self.bundle.engine)
        metadata, generation = self.bundle.verify()
        baseline_source = cold_recovery.Baseline(self.bundle)
        baseline = baseline_source.verify()
        original = baseline_source.identity.verify()
        backup = cold_disk.DiskBackup(self.bundle)
        disk = backup.validate(records.read(backup.record), metadata, generation)
        capsule = cold_filesystem.Inspector(self.bundle).capsule()
        if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit',
                'metadata_sha256', 'generation_sha256', 'identity_sha256',
                'baseline_sha256', 'backup_uuid', 'bootstrap_sha256',
                'original_xen_uuid', 'initial_operation', 'initial_provision', 'issued_at',
                'expires_at', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-supervised-request.v1' or
                value['box'] != self.storage.box or value['operation_id'] != self.bundle.operation or
                value['engine_commit'] != self.bundle.engine or
                value['metadata_sha256'] != metadata['record_sha256'] or
                value['generation_sha256'] != generation['record_sha256'] or
                value['identity_sha256'] != original['record_sha256'] or
                value['baseline_sha256'] != baseline['record_sha256'] or
                disk['stage'] != 'allocated' or value['backup_uuid'] != disk['backup']['uuid'] or
                value['bootstrap_sha256'] != capsule['record_sha256'] or
                value['original_xen_uuid'] != generation['xen']['uuid'] or
                not generations.matches('[0-9a-f]{24}', value['initial_operation']) or
                value['initial_operation'] != pointer['operation_id'] or
                value['initial_operation'] == self.bundle.operation or
                type(now) is not int or type(value['issued_at']) is not int or
                type(value['expires_at']) is not int or
                not value['issued_at'] <= now < value['expires_at'] <= value['issued_at'] + 7200 or
                now > value['issued_at'] + 900 or
                not original['observed_at'] <= now <= original['observed_at'] + 900 or
                (self.bundle.directory / 'prepared-abort-intent.json').exists() or
                (self.bundle.directory / 'prepared-abort-intent.json').is_symlink() or
                self.storage.cold_test() is not None or self.storage.pending() is not None or
                self.storage.installation() is not None or
                self.storage.accepted() != records.read(self.bundle.directory / 'accepted.json')):
            raise TransactionError('cold supervisor request is stale or differs from its prepared original')
        backup.backup_disk(disk['backup']['uuid'])
        if (self.host.guest({'accepted': generation}, deadline=time.monotonic() + 30) is None or
                baseline_source.domains(generation) != baseline['domains']):
            raise TransactionError('cold supervisor request needs the unchanged running original')
        return value

    def verify(self, *, now=None):
        return self.validate(records.read(self.path), now=now)

    def stage(self, value, *, now=None):
        """Store one immutable selector while the original is still running."""
        with self.storage.lock():
            self.validate(value, now=now)
            if self.path.exists() or self.path.is_symlink():
                if records.read(self.path) != value:
                    raise TransactionError('cold supervisor request retry changed its prepared source')
            else:
                records.write(self.path, value)
            return self.verify(now=now)
