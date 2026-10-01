"""Bind a legacy router's live Tailnet identity before a cold test.

The controller compares the guest's Tailscale Self entry with its own peer
view. Only the resulting identity and status hashes enter the dom0 bundle.
"""
import json
import time

import router_generation_device as devices
import router_generations as generations
import router_records as records
from router_transaction import TransactionError

MAX_STATUS = 8 * 1024 * 1024


def checked_status(value):
    if not isinstance(value, dict) or len(json.dumps(value)) > MAX_STATUS:
        raise TransactionError('cold router identity status is missing or too large')
    return value


def create(box, operation, engine, metadata_sha256, generation_sha256,
           guest_status, controller_status, observed_at, *, expected_machine_id=None):
    """Create one bounded proof from two independent live Tailnet views."""
    if (not generations.matches('[a-z0-9][a-z0-9-]{0,30}', box) or
            not generations.matches('[0-9a-f]{24}', operation) or
            not generations.matches('[0-9a-f]{40}', engine) or
            any(not generations.matches('[0-9a-f]{64}', value) for value in (
                metadata_sha256, generation_sha256)) or
            type(observed_at) is not int or observed_at <= 0):
        raise TransactionError('cold router identity needs one exact source')
    guest_status = checked_status(guest_status)
    controller_status = checked_status(controller_status)
    guest = guest_status.get('Self')
    peers = controller_status.get('Peer')
    if (guest_status.get('BackendState') != 'Running' or
            controller_status.get('BackendState') != 'Running' or
            not isinstance(guest, dict) or not isinstance(peers, dict) or
            guest.get('HostName') != box + '-router' or guest.get('Online') is not True or
            not generations.matches('[A-Za-z0-9_-]{1,128}', guest.get('ID'))):
        raise TransactionError('cold router identity lacks a live guest and controller peer view')
    if expected_machine_id is not None and guest['ID'] != expected_machine_id:
        raise TransactionError('cold router expected live Tailnet identity changed')
    matches = [peer for peer in peers.values() if isinstance(peer, dict) and
               (peer.get('ID') == guest['ID'] or
                expected_machine_id is None and peer.get('HostName') == box + '-router')]
    if (len(matches) != 1 or matches[0].get('ID') != guest['ID'] or
            matches[0].get('HostName') != box + '-router' or matches[0].get('Online') is not True):
        raise TransactionError('cold router guest identity differs from the controller peer view')
    return generations.seal({'kind': 'klokast.router-cold-original-identity.v1',
        'box': box, 'operation_id': operation, 'engine_commit': engine,
        'metadata_sha256': metadata_sha256, 'generation_sha256': generation_sha256,
        'machine_id': guest['ID'], 'hostname': box + '-router',
        'observed_at': observed_at,
        'guest_status_sha256': generations.digest(guest_status),
        'controller_status_sha256': generations.digest(controller_status)})


class Identity:
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.path = bundle.directory / 'original-identity.json'

    def validate(self, value, metadata, generation):
        generations.check_seal(value)
        if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit',
                'metadata_sha256', 'generation_sha256', 'machine_id', 'hostname',
                'observed_at', 'guest_status_sha256', 'controller_status_sha256', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-original-identity.v1' or
                value['box'] != self.storage.box or value['operation_id'] != self.bundle.operation or
                value['engine_commit'] != self.bundle.engine or
                value['metadata_sha256'] != metadata['record_sha256'] or
                value['generation_sha256'] != generation['record_sha256'] or
                not generations.matches('[A-Za-z0-9_-]{1,128}', value['machine_id']) or
                value['hostname'] != self.storage.box + '-router' or
                type(value['observed_at']) is not int or value['observed_at'] <= 0 or
                any(not generations.matches('[0-9a-f]{64}', value[key]) for key in (
                    'guest_status_sha256', 'controller_status_sha256'))):
            raise TransactionError('cold router original identity differs from its protected generation')
        if 'device.json' in metadata['files']:
            device = devices.validate(records.read(self.bundle.directory / 'device.json'),
                                      self.storage.box, generation['record_sha256'])
            if device['machine_id'] != value['machine_id']:
                raise TransactionError('cold router live identity differs from its protected device record')
        return value

    def verify(self):
        metadata, generation = self.bundle.verify()
        return self.validate(records.read(self.path), metadata, generation)

    def stage(self, value, *, now=None):
        """Record only a fresh, matching identity while the original is live."""
        now = int(time.time()) if now is None else now
        with self.storage.lock():
            self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
            metadata, generation = self.bundle.verify()
            self.validate(value, metadata, generation)
            if (type(now) is not int or not value['observed_at'] <= now <= value['observed_at'] + 900 or
                    self.storage.cold_test() is not None or
                    self.storage.pending() is not None or self.storage.installation() is not None or
                    self.storage.accepted() != records.read(self.bundle.directory / 'accepted.json') or
                    self.host.guest({'accepted': generation}, deadline=time.monotonic() + 30) is None):
                raise TransactionError('cold router identity must be staged against the live accepted source')
            if self.path.exists() or self.path.is_symlink():
                if records.read(self.path) != value:
                    raise TransactionError('cold router identity retry changed its live proof')
            else:
                records.write(self.path, value)
            return self.verify()
