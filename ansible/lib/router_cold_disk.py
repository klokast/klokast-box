"""Independent cold LV backup for a supervised legacy-router test.

This root-only dom0 primitive does not stop, rename, or restore a router.
The metadata bundle fixes the source. Only its newly allocated and recorded
backup LV can be written. An exact pre-outage abort can retire an unused backup.
The caller supplies outage authority for copying the stopped router.
"""
import time
from pathlib import Path

import router_candidate_disk as disks
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
from xen_build_runtime import checksum


def selection(operation):
    if not generations.matches('[0-9a-f]{24}', operation):
        raise TransactionError('cold disk backup requires an exact operation ID')
    name = 'routercold_' + operation + '_backup'
    return '/dev/vg0/' + name, name


class DiskBackup:
    def __init__(self, bundle):
        self.bundle = bundle
        self.storage, self.host = bundle.storage, bundle.host
        self.path, self.tag = selection(bundle.operation)
        self.record = bundle.directory / 'disk.json'

    def observe(self, identity=None):
        rows = [row for row in disks.inventory() if row['lv_path'] == self.path or
                self.tag in row['lv_tags'].split(',') or
                identity is not None and row['lv_uuid'] == identity]
        if len(rows) > 1 or rows and rows[0]['lv_path'] != self.path:
            raise TransactionError('cold backup LV was renamed or its ownership selector is ambiguous')
        return rows[0] if rows else None

    def validate(self, value, metadata, generation):
        generations.check_seal(value)
        if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit', 'metadata_sha256',
                'source', 'backup', 'stage', 'source_sha256', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-disk.v1' or value['box'] != self.storage.box or
                value['operation_id'] != self.bundle.operation or value['engine_commit'] != self.bundle.engine or
                value['metadata_sha256'] != metadata['record_sha256'] or
                value['source'] != generation['disk'] or
                not isinstance(value['backup'], dict) or set(value['backup']) != {'path', 'uuid', 'bytes'} or
                value['backup']['path'] != self.path or value['backup']['bytes'] != disks.BYTES or
                value['stage'] not in ('planned', 'allocated', 'copying', 'copied') or
                (value['stage'] == 'planned' and value['backup']['uuid'] is not None) or
                (value['stage'] != 'planned' and not generations.matches(
                    '[A-Za-z0-9-]{1,64}', value['backup']['uuid'])) or
                value['backup']['uuid'] == value['source']['uuid'] or
                (value['stage'] in ('planned', 'allocated') and value['source_sha256'] is not None) or
                (value['stage'] in ('copying', 'copied') and not generations.matches(
                    '[0-9a-f]{64}', value['source_sha256']))):
            raise TransactionError('cold disk record has a different source, backup, or stage')
        return value

    def save(self, value, **changes):
        updated = generations.seal({**{key: item for key, item in value.items() if key != 'record_sha256'},
                                    **changes})
        records.write(self.record, updated)
        return updated

    def source(self):
        self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
        metadata, generation = self.bundle.verify()
        self.bundle.idle()
        if self.storage.accepted()['current_sha256'] != generation['record_sha256']:
            raise TransactionError('cold disk backup source is no longer the accepted legacy router')
        return metadata, generation

    def backup_disk(self, identity):
        row = self.observe(identity)
        if (row is None or row['lv_uuid'] != identity or row['lv_size'] != str(disks.BYTES) or
                row['origin'] or not row['lv_attr'].startswith('-wi-a') or row['lv_tags'] != self.tag):
            raise TransactionError('cold backup LV identity, ownership, or independent allocation changed')
        return {'path': self.path, 'uuid': identity, 'bytes': disks.BYTES}

    def abort_prepared(self, cleanup_engine):
        """Retire only an unused pre-outage backup after an expired preparation."""
        if not generations.matches('[0-9a-f]{40}', cleanup_engine):
            raise TransactionError('cold prepared abort needs the activated cleanup engine')
        with self.storage.lock():
            self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
            metadata, generation = self.bundle.verify()
            self.bundle.idle()
            if (self.storage.cold_test() is not None or
                    self.storage.accepted() != records.read(self.bundle.directory / 'accepted.json') or
                    self.storage.accepted()['current_sha256'] != generation['record_sha256'] or
                    self.host.guest({'accepted': generation}, deadline=time.monotonic() + 30) is None or
                    any(path.exists() or path.is_symlink() for path in (
                        self.bundle.directory / 'outage-authorization.json',
                        self.bundle.directory / 'supervisor-ready.json',
                        self.bundle.directory / 'supervisor-result.json',
                        self.bundle.directory / 'filesystem.json',
                        self.bundle.directory / 'return-intent.json',
                        self.bundle.directory / 'completion.json'))):
                raise TransactionError('cold prepared abort requires the unchanged live original before any outage')
            value = self.validate(records.read(self.record), metadata, generation)
            if value['stage'] != 'allocated':
                raise TransactionError('cold prepared abort refuses a backup that was copied or used')
            request_path = self.bundle.directory / 'supervised-request.json'
            if request_path.exists() or request_path.is_symlink():
                request = records.read(request_path)
                generations.check_seal(request)
                if (set(request) != {'kind', 'box', 'operation_id', 'engine_commit',
                        'metadata_sha256', 'generation_sha256', 'identity_sha256',
                        'baseline_sha256', 'backup_uuid', 'bootstrap_sha256',
                        'original_xen_uuid', 'initial_operation', 'initial_provision',
                        'issued_at', 'expires_at', 'record_sha256'} or
                        request.get('kind') != 'klokast.router-cold-supervised-request.v1' or
                        request.get('box') != self.storage.box or
                        request.get('operation_id') != self.bundle.operation or
                        request.get('engine_commit') != self.bundle.engine or
                        request.get('metadata_sha256') != metadata['record_sha256'] or
                        request.get('generation_sha256') != generation['record_sha256'] or
                        request.get('backup_uuid') != value['backup']['uuid']):
                    raise TransactionError('cold prepared abort found a different staged request')
            intent = generations.seal({'kind': 'klokast.router-cold-prepared-abort-intent.v1',
                'box': self.storage.box, 'operation_id': self.bundle.operation,
                'source_engine_commit': self.bundle.engine, 'cleanup_engine_commit': cleanup_engine,
                'metadata_sha256': metadata['record_sha256'],
                'backup_uuid': value['backup']['uuid']})
            intent_path = self.bundle.directory / 'prepared-abort-intent.json'
            completion_path = self.bundle.directory / 'prepared-abort-completion.json'
            if intent_path.exists() or intent_path.is_symlink():
                if records.read(intent_path) != intent:
                    raise TransactionError('cold prepared abort retry changed its exact backup identity')
            elif self.observe(value['backup']['uuid']) is None:
                raise TransactionError('cold prepared abort found a missing backup before its removal intent')
            if completion_path.exists() or completion_path.is_symlink():
                completion = records.read(completion_path)
                generations.check_seal(completion)
                if (completion != generations.seal({
                        'kind': 'klokast.router-cold-prepared-abort.v1',
                        'box': self.storage.box, 'operation_id': self.bundle.operation,
                        'source_engine_commit': self.bundle.engine,
                        'cleanup_engine_commit': cleanup_engine,
                        'intent_sha256': intent['record_sha256'], 'status': 'retired'}) or
                        self.observe(value['backup']['uuid']) is not None):
                    raise TransactionError('cold prepared abort completion differs from the retired backup')
                return completion
            if self.observe(value['backup']['uuid']) is not None:
                backup = self.backup_disk(value['backup']['uuid'])
                self.host.disk(backup, deadline=time.monotonic() + 30)
                self.host.wait_detached([backup['path']], deadline=time.monotonic() + 30)
                disks.refuse_referenced_disk(self.storage.box, self.bundle.operation, backup)
                if not intent_path.exists() and not intent_path.is_symlink():
                    records.write(intent_path, intent)
                disks.native.command(['/sbin/lvremove', '--yes', backup['path']],
                                     time.monotonic() + 60, maximum_seconds=60)
            if self.observe(value['backup']['uuid']) is not None:
                raise TransactionError('cold prepared backup remains after its exact retirement')
            completion = generations.seal({'kind': 'klokast.router-cold-prepared-abort.v1',
                'box': self.storage.box, 'operation_id': self.bundle.operation,
                'source_engine_commit': self.bundle.engine,
                'cleanup_engine_commit': cleanup_engine,
                'intent_sha256': intent['record_sha256'], 'status': 'retired'})
            records.write(completion_path, completion)
            return completion

    def detached_pair(self, value):
        deadline = time.monotonic() + 30
        backup = self.backup_disk(value['backup']['uuid'])
        source_device = self.host.disk(value['source'], deadline=deadline)
        backup_device = self.host.disk(backup, deadline=deadline)
        if source_device == backup_device:
            raise TransactionError('cold backup aliases the original router device')
        self.host.detached([value['source']['path'], backup['path']], deadline=deadline)

    def allocate(self):
        """Reserve the backup before an outage; never adopt an unrecorded LV."""
        with self.storage.lock():
            metadata, generation = self.source()
            if self.record.exists() or self.record.is_symlink():
                value = self.validate(records.read(self.record), metadata, generation)
                if value['stage'] != 'planned':
                    self.backup_disk(value['backup']['uuid'])
                    return value
            else:
                if self.observe() is not None:
                    raise TransactionError('cold backup found an unrecorded LV; reconcile it before retry')
                value = self.save({'kind': 'klokast.router-cold-disk.v1', 'box': self.storage.box,
                    'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
                    'metadata_sha256': metadata['record_sha256'], 'source': generation['disk'],
                    'backup': {'path': self.path, 'uuid': None, 'bytes': disks.BYTES},
                    'stage': 'planned', 'source_sha256': None})
            if self.observe() is not None:
                raise TransactionError('cold backup allocation has no recorded UUID; reconcile it before retry')
            disks.native.command(['/sbin/lvcreate', '--size', '2G', '--name', self.tag, '--addtag', self.tag,
                                  '--wipesignatures', 'y', '--yes', 'vg0'],
                                 time.monotonic() + 120, maximum_seconds=120, lvm_diagnostic=True)
            row = self.observe()
            if row is None or not generations.matches('[A-Za-z0-9-]{1,64}', row['lv_uuid']):
                raise TransactionError('cold backup LV creation has no exact identity')
            backup = self.backup_disk(row['lv_uuid'])
            if backup['uuid'] == generation['disk']['uuid']:
                raise TransactionError('cold backup LV reused the original UUID')
            return self.save(value, backup=backup, stage='allocated')

    def copy(self):
        """Hash, copy, and rehash two detached exact LVs; keep a verified copy."""
        with self.storage.lock():
            metadata, generation = self.source()
            value = self.validate(records.read(self.record), metadata, generation)
            if value['stage'] == 'planned':
                raise TransactionError('cold backup cannot copy without its recorded allocation UUID')
            if self.host.guest({'accepted': generation}, deadline=time.monotonic() + 30) is not None:
                raise TransactionError('cold disk copy requires the accepted router stopped')
            self.detached_pair(value)
            source = checksum(Path(value['source']['path']), disks.BYTES)
            if value['source_sha256'] is not None and value['source_sha256'] != source:
                raise TransactionError('cold router source changed after its copy intent; retain the backup')
            if value['stage'] == 'allocated':
                value = self.save(value, stage='copying', source_sha256=source)
            if value['stage'] == 'copying':
                self.detached_pair(value)
                disks.native.command(['/bin/dd', 'if=' + value['source']['path'], 'of=' + self.path,
                                      'bs=4M', 'count=512', 'conv=notrunc,fsync'],
                                     time.monotonic() + 180, maximum_seconds=180)
            self.detached_pair(value)
            if (checksum(Path(self.path), disks.BYTES) != source or
                    checksum(Path(value['source']['path']), disks.BYTES) != source):
                raise TransactionError('cold disk copy does not match the stopped original; retain both LVs')
            return value if value['stage'] == 'copied' else self.save(value, stage='copied')
