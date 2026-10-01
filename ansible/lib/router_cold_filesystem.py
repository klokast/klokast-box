"""Inspect a cold backup in a fixed networkless read-only Xen guest.

The active controller stages a versioned boot capsule before the outage.
This module runs only after the original router stops and its raw copy passes.
No guest filesystem is mounted on dom0. An uncertain result stays for exact
reconciliation; a second guest cannot silently reuse the operation.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

import router_cold_disk as disks
import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import TransactionError
import xen_build_runtime as xen

MIB = 1024 * 1024


def loops(path):
    return sorted('/dev/' + item.parents[1].name for item in Path('/sys/block').glob(
        'loop*/loop/backing_file') if item.read_text().strip().lstrip('/') == str(path).lstrip('/'))


class Inspector:
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.backup = disks.DiskBackup(bundle)
        self.work = bundle.directory / 'filesystem'
        self.name = 'router-cold-fs-' + bundle.operation
        self.identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'klokast-router-cold-fs-' +
                                       bundle.operation))

    def capsule(self):
        value = records.read(self.bundle.directory / 'filesystem-bootstrap.json')
        generations.check_seal(value)
        if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit', 'inputs_sha256',
                'boot', 'guest_sha256', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-filesystem-bootstrap.v1' or
                value['box'] != self.storage.box or value['operation_id'] != self.bundle.operation or
                value['engine_commit'] != self.bundle.engine or
                not generations.matches('[0-9a-f]{64}', value['inputs_sha256']) or
                not generations.matches('[0-9a-f]{64}', value['guest_sha256']) or
                not isinstance(value['boot'], dict) or set(value['boot']) != {'kernel', 'initramfs'}):
            raise TransactionError('cold filesystem bootstrap capsule differs from the selected source')
        for name, maximum in (('kernel', 32 * MIB), ('initramfs', 1024 * MIB)):
            expected = value['boot'][name]
            if (not isinstance(expected, dict) or set(expected) != {'bytes', 'sha256'} or
                    type(expected['bytes']) is not int or not 0 < expected['bytes'] <= maximum or
                    not generations.matches('[0-9a-f]{64}', expected['sha256'])):
                raise TransactionError('cold filesystem bootstrap artifact identity is incomplete')
            self.host.artifact({'path': str(self.work / ('bootstrap-' + name)), **expected},
                               deadline=time.monotonic() + 90)
        return value

    def source(self):
        self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
        marker = self.storage.cold_test()
        metadata, generation = self.bundle.verify()
        if (marker is None or marker['phase'] != 'armed' or
                marker['operation_id'] != self.bundle.operation or
                marker['engine_commit'] != self.bundle.engine or
                marker['metadata_sha256'] != metadata['record_sha256'] or
                marker['generation_sha256'] != generation['record_sha256'] or
                time.time() >= marker['expires_at'] or
                self.storage.pending() is not None or self.storage.installation() is not None or
                self.storage.accepted()['current_sha256'] != generation['record_sha256']):
            raise TransactionError('cold filesystem proof requires the stopped accepted router and active test fence')
        disk = self.backup.validate(records.read(self.backup.record), metadata, generation)
        if disk['stage'] != 'copied':
            raise TransactionError('cold filesystem proof requires a completed raw disk copy')
        backup = self.backup.backup_disk(disk['backup']['uuid'])
        deadline = time.monotonic() + 30
        if self.host.guest({'accepted': generation}, deadline=deadline) is not None:
            raise TransactionError('cold filesystem proof requires the accepted router stopped')
        self.host.disk(generation['disk'], deadline=deadline)
        self.host.disk(backup, deadline=deadline)
        self.host.detached([generation['disk']['path'], backup['path']], deadline=deadline)
        if (xen.checksum(Path(generation['disk']['path']), generation['disk']['bytes']) != disk['source_sha256'] or
                xen.checksum(Path(backup['path']), backup['bytes']) != disk['source_sha256']):
            raise TransactionError('cold filesystem proof source or backup changed after raw copying')
        return metadata, generation, disk, backup

    def job(self, capsule, metadata, disk):
        return {'kind': 'klokast.router-cold-filesystem-job.v1', 'box': self.storage.box,
                'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
                'inputs_sha256': capsule['inputs_sha256'],
                'metadata_sha256': metadata['record_sha256'], 'disk_sha256': disk['record_sha256'],
                'backup_uuid': disk['backup']['uuid']}

    def configuration(self, capsule, job, backup, result_loop, job_loop):
        extra = ('console=hvc0 panic=1 klokast_operation=' + self.bundle.operation +
                 ' klokast_inputs=' + capsule['inputs_sha256'] +
                 ' klokast_job=' + generations.digest(job))
        items = {'name': self.name, 'uuid': self.identity, 'type': 'pvh', 'memory': 768,
                 'vcpus': 1, 'kernel': str(self.work / 'bootstrap-kernel'),
                 'ramdisk': str(self.work / 'bootstrap-initramfs'), 'extra': extra,
                 'vif': [], 'on_poweroff': 'destroy', 'on_crash': 'destroy',
                 'on_reboot': 'destroy',
                 'disk': ['phy:' + backup['path'] + ',xvda,r',
                          'phy:' + result_loop + ',xvdb,w',
                          'phy:' + job_loop + ',xvdc,r']}
        return '\n'.join(key + ' = ' + repr(item) for key, item in items.items()) + '\n'

    def paused(self, record, backup, result_loop, job_loop, capsule, job):
        """Check all Xen attachments before the isolated guest can execute."""
        config = record.get('config', {})
        info, boot = config.get('c_info', {}), config.get('b_info', {})
        expected = [('xvda', backup['path'], 0), ('xvdb', result_loop, 1),
                    ('xvdc', job_loop, 0)]
        actual = [(item.get('vdev'), item.get('pdev_path'), item.get('readwrite'))
                  for item in config.get('disks', [])]
        if (info.get('name') != self.name or info.get('uuid') != self.identity or
                info.get('type') != 'pvh' or record.get('domid', 0) <= 0 or
                boot.get('kernel') != str(self.work / 'bootstrap-kernel') or
                boot.get('ramdisk') != str(self.work / 'bootstrap-initramfs') or
                boot.get('cmdline') != ('console=hvc0 panic=1 klokast_operation=' + self.bundle.operation +
                    ' klokast_inputs=' + capsule['inputs_sha256'] + ' klokast_job=' + generations.digest(job)) or
                boot.get('target_memkb') != 768 * 1024 or boot.get('max_vcpus') != 1 or
                config.get('nics') != [] or len(actual) != 3 or
                any(item.get('format') != 'raw' for item in config['disks']) or
                any(observed[0] != vdev or observed[2] != mode or
                    self.host.device(observed[1]) != self.host.device(path)
                    for observed, (vdev, path, mode) in zip(actual, expected))):
            raise TransactionError('paused cold filesystem guest differs from its networkless read-only capsule')

    def slot(self, path, *, content=None):
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w+b') as stream:
            os.posix_fallocate(stream.fileno(), 0, MIB)
            if content is not None:
                if len(content) > 32768:
                    raise TransactionError('cold filesystem job is too large for its fixed slot')
                stream.write(content + b'\0')
            stream.flush()
            os.fsync(stream.fileno())
        records.syncdir(path.parent)

    def loop(self, path, readonly):
        if loops(path):
            raise TransactionError('cold filesystem slot already has a loop attachment')
        native.command(['/sbin/losetup', *(['-r'] if readonly else []), '-f', path],
                       time.monotonic() + 30)
        found = loops(path)
        if len(found) != 1 or not re.fullmatch(r'/dev/loop[0-9]+', found[0]):
            raise TransactionError('cold filesystem loop allocation is ambiguous')
        return found[0]

    def detach_loop(self, path):
        found = loops(path)
        if len(found) > 1:
            raise TransactionError('cold filesystem slot has ambiguous loop attachments')
        if found:
            self.host.detached(found, deadline=time.monotonic() + 30)
            native.command(['/sbin/losetup', '-d', found[0]], time.monotonic() + 30)
            if loops(path):
                raise TransactionError('cold filesystem loop remains attached')

    def run(self):
        """Publish one exact proof after the guest has stopped and detached."""
        with self.storage.lock():
            metadata, generation, disk, backup = self.source()
            capsule = self.capsule()
            records.secure(self.work, directory=True)
            if any((self.work / name).exists() or (self.work / name).is_symlink()
                   for name in ('job.slot', 'result.slot', 'guest.cfg')):
                raise TransactionError('cold filesystem job already started; reconcile its exact guest and slots')
            if xen.domain(self.name) is not None:
                raise TransactionError('cold filesystem guest already exists for this operation')
            job = self.job(capsule, metadata, disk)
            result_slot, job_slot = self.work / 'result.slot', self.work / 'job.slot'
            self.slot(result_slot)
            self.slot(job_slot, content=(json.dumps(job, sort_keys=True, separators=(',', ':')) + '\n').encode())
            result_loop, job_loop = None, None
            try:
                result_loop = self.loop(result_slot, False)
                job_loop = self.loop(job_slot, True)
                content = self.configuration(capsule, job, backup, result_loop, job_loop)
                cfg = self.work / 'guest.cfg'
                records.atomic(cfg, content.encode())
                xen.boot_guest(self.work, self.name, self.identity, cfg, job,
                    kind='klokast.router-cold-filesystem-result.v1', timeout=420,
                    validate_paused=lambda record: self.paused(
                        record, backup, result_loop, job_loop, capsule, job))
            finally:
                if xen.domain(self.name) is None:
                    for path in (job_slot, result_slot):
                        self.detach_loop(path)
            self.host.detached([backup['path']], deadline=time.monotonic() + 30)
            result = xen.read_slot(result_slot)
            if result != {'kind': 'klokast.router-cold-filesystem-result.v1',
                    'operation_id': self.bundle.operation, 'inputs_sha256': capsule['inputs_sha256'],
                    'job_sha256': generations.digest(job), 'success': True,
                    'readonly': True, 'root_verified': True}:
                raise TransactionError('cold filesystem guest did not prove its exact read-only root')
            if xen.checksum(Path(backup['path']), backup['bytes']) != disk['source_sha256']:
                raise TransactionError('cold filesystem guest changed its read-only backup disk')
            if self.source() != (metadata, generation, disk, backup):
                raise TransactionError('cold filesystem source changed during its isolated inspection')
            proof = {'kind': 'klokast.router-cold-filesystem.v1',
                     'operation_id': self.bundle.operation, 'metadata_sha256': metadata['record_sha256'],
                     'disk_sha256': disk['record_sha256'], 'backup_uuid': backup['uuid'],
                     'readonly': True, 'root_verified': True}
            records.write(self.bundle.directory / 'filesystem.json', proof)
            return proof
