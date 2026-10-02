"""Inspect a cold backup in a fixed networkless read-only Xen guest.

The active controller stages a versioned boot capsule before the outage.
This module runs only after the original router stops and its raw copy passes.
No guest filesystem is mounted on dom0. An uncertain result stays for exact
reconciliation; a second guest cannot silently reuse the operation.
"""
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import time
import uuid

import router_cold_disk as disks
import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import TransactionError
import xen_build_runtime as xen

MIB = 1024 * 1024
CHUNK = 2 * MIB


def bootstrap_capsule(value, box, operation, engine):
    generations.check_seal(value)
    if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit', 'inputs_sha256',
            'boot', 'guest_sha256', 'record_sha256'} or
            value['kind'] != 'klokast.router-cold-filesystem-bootstrap.v1' or
            (value['box'], value['operation_id'], value['engine_commit']) != (box, operation, engine) or
            any(not generations.matches('[0-9a-f]{64}', value[key]) for key in ('inputs_sha256', 'guest_sha256')) or
            not isinstance(value['boot'], dict) or set(value['boot']) != {'kernel', 'initramfs'}):
        raise TransactionError('cold filesystem bootstrap capsule differs from the selected source')
    for name, maximum in (('kernel', 32 * MIB), ('initramfs', 1024 * MIB)):
        expected = value['boot'][name]
        if (not isinstance(expected, dict) or set(expected) != {'bytes', 'sha256'} or
                type(expected['bytes']) is not int or not 0 < expected['bytes'] <= maximum or
                not generations.matches('[0-9a-f]{64}', expected['sha256'])):
            raise TransactionError('cold filesystem bootstrap artifact identity is incomplete')
    return value


class BootstrapStaging:
    """Receive opaque, bounded boot bytes under the router record lock.

    Intent precedes creation; inode ownership precedes any payload write.
    An interrupted write can only be retired, never implicitly resumed.
    Holding this lock and closing the phase fences all native receivers,
    including a receiver still reading stdin before it takes the lock.
    """
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.directory = bundle.directory
        self.work = self.directory / 'filesystem'
        self.intent_path = self.directory / 'bootstrap-staging-intent.json'
        self.state_path = self.directory / 'bootstrap-staging-state.json'

    def request(self, value):
        generations.check_seal(value)
        if set(value) != {'kind', 'capsule', 'parts', 'record_sha256'} or value['kind'] != 'klokast.router-cold-bootstrap-transfer.v1':
            raise TransactionError('cold bootstrap transfer manifest is incomplete')
        capsule = bootstrap_capsule(value['capsule'], self.storage.box, self.bundle.operation, self.bundle.engine)
        if not isinstance(value['parts'], dict) or set(value['parts']) != {'kernel', 'initramfs'}:
            raise TransactionError('cold bootstrap transfer has invalid artifacts')
        for name, parts in value['parts'].items():
            count = (capsule['boot'][name]['bytes'] + CHUNK - 1) // CHUNK
            if not isinstance(parts, list) or len(parts) != count:
                raise TransactionError('cold bootstrap transfer has invalid part count')
            for index, part in enumerate(parts):
                size = min(CHUNK, capsule['boot'][name]['bytes'] - index * CHUNK)
                if (not isinstance(part, dict) or set(part) != {'name', 'bytes', 'sha256'} or
                        part['name'] != f'part-{index:04d}' or type(part['bytes']) is not int or part['bytes'] != size or
                        not generations.matches('[0-9a-f]{64}', part['sha256'])):
                    raise TransactionError('cold bootstrap transfer has invalid part identity')
        return value

    def original(self, accepted):
        self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
        self.bundle.idle()
        if self.storage.cold_test() is not None or self.storage.accepted() != accepted:
            raise TransactionError('cold bootstrap staging requires the unchanged accepted router')
        generation = self.storage.generation(accepted['current_sha256'])
        if generation['origin'] != 'legacy' or accepted['previous_sha256'] is not None or accepted['policy_sha256'] != records.BASELINE_AUTHORITY_SHA256 or self.host.guest(
                {'accepted': generation}, deadline=time.monotonic() + 30) is None:
            raise TransactionError('cold bootstrap staging requires one running legacy router')

    def stage(self, request):
        request = self.request(request)
        with self.storage.lock():
            accepted = self.storage.accepted()
            self.original(accepted)
            if self.directory.exists() or self.directory.is_symlink():
                raise TransactionError('cold bootstrap staging operation already exists')
            self.directory.parent.mkdir(mode=0o700, exist_ok=True)
            records.secure(self.directory.parent, directory=True)
            self.directory.mkdir(mode=0o700)
            intent = generations.seal({'kind': 'klokast.router-cold-bootstrap-staging-intent.v1',
                'request': request, 'accepted': accepted})
            records.write(self.intent_path, intent)
            # No boot file can exist until this fixed intent is durable.
            self.work.mkdir(mode=0o700)
            self.save({'kind': 'klokast.router-cold-bootstrap-staging-state.v1',
                'intent_sha256': intent['record_sha256'], 'phase': 'receiving', 'files': {}})
            return {'status': 'receiving', 'staging_sha256': intent['record_sha256']}

    def save(self, state):
        records.write(self.state_path, generations.seal({k: v for k, v in state.items() if k != 'record_sha256'}))

    def load(self, *, recover_empty=False):
        intent = records.read(self.intent_path); generations.check_seal(intent)
        if set(intent) != {'kind', 'request', 'accepted', 'record_sha256'} or intent['kind'] != 'klokast.router-cold-bootstrap-staging-intent.v1':
            raise TransactionError('cold bootstrap staging intent changed')
        request = self.request(intent['request'])
        records.assignment(intent['accepted'], self.storage.box)
        if recover_empty and not self.state_path.exists() and not self.state_path.is_symlink():
            # Interruption after durable intent but before ledger initialization.
            # No payload writer can run without the ledger. Unknown bytes refuse.
            self.work.mkdir(mode=0o700, exist_ok=True)
            records.secure(self.work, directory=True)
            if any(self.work.iterdir()):
                raise TransactionError('cold bootstrap uninitialized ledger has unknown files')
            self.save({'kind': 'klokast.router-cold-bootstrap-staging-state.v1',
                'intent_sha256': intent['record_sha256'], 'phase': 'retiring', 'files': {}})
        state = records.read(self.state_path); generations.check_seal(state)
        if (set(state) != {'kind', 'intent_sha256', 'phase', 'files', 'record_sha256'} or
                state['kind'] != 'klokast.router-cold-bootstrap-staging-state.v1' or
                state['intent_sha256'] != intent['record_sha256'] or
                state['phase'] not in ('receiving', 'ready', 'retiring', 'retired') or
                not isinstance(state['files'], dict) or set(state['files']) - {'kernel', 'initramfs'}):
            raise TransactionError('cold bootstrap staging ledger changed')
        for name, item in state['files'].items():
            parts = request['parts'][name]
            if (not isinstance(item, dict) or set(item) != {'device', 'inode', 'next_part', 'inflight'} or
                    type(item['next_part']) is not int or not 0 <= item['next_part'] <= len(parts) or
                    item['inflight'] is not None and (type(item['inflight']) is not int or
                        item['inflight'] != item['next_part'] or item['next_part'] == len(parts)) or
                    (item['device'] is None) != (item['inode'] is None) or
                    item['device'] is None and (item['next_part'] != 0 or item['inflight'] is not None) or
                    item['device'] is not None and (type(item['device']) is not int or item['device'] < 0 or
                        type(item['inode']) is not int or item['inode'] <= 0)):
                raise TransactionError('cold bootstrap staging file ledger changed')
        return intent, request, state

    def inspect(self, path, maximum, *, hash_bytes=True):
        records.parents(path)
        deadline = min(getattr(self, 'deadline', float('inf')), time.monotonic() + 90)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != records.ROOT_UID or
                    stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or before.st_size > maximum):
                raise TransactionError('cold bootstrap staged file has unsafe type, size, or ownership')
            digest = hashlib.sha256(); size = before.st_size
            if hash_bytes:
                size = 0
                with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                    for block in iter(lambda: stream.read(MIB), b''):
                        if time.monotonic() >= deadline: raise TransactionError('cold bootstrap measurement time limit reached')
                        size += len(block)
                        if size > maximum: raise TransactionError('cold bootstrap staged file exceeds its bound')
                        digest.update(block)
            after = os.fstat(descriptor)
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_mtime_ns', 'st_ctime_ns')
            if (any(getattr(before, key) != getattr(after, key) or getattr(path.lstat(), key) != getattr(after, key)
                    for key in fields) or size != before.st_size):
                raise TransactionError('cold bootstrap staged file changed while measured')
            return {'device': before.st_dev, 'inode': before.st_ino, 'bytes': size, 'sha256': digest.hexdigest()}
        finally:
            os.close(descriptor)

    def receive(self, body):
        if (not isinstance(body, dict) or set(body) != {'artifact', 'part', 'data'} or
                body['artifact'] not in ('kernel', 'initramfs') or type(body['part']) is not int or
                not isinstance(body['data'], str) or len(body['data']) > (CHUNK + 2) // 3 * 4):
            raise TransactionError('cold bootstrap receiver requires one bounded part')
        try: payload = base64.b64decode(body['data'], validate=True)
        except ValueError as error: raise TransactionError('cold bootstrap part encoding is invalid') from error
        with self.storage.lock():
            intent, request, state = self.load()
            self.original(intent['accepted'])
            if state['phase'] != 'receiving': raise TransactionError('cold bootstrap receiver phase is closed')
            name, index = body['artifact'], body['part']
            parts = request['parts'][name]
            if not 0 <= index < len(parts) or len(payload) != parts[index]['bytes'] or hashlib.sha256(payload).hexdigest() != parts[index]['sha256']:
                raise TransactionError('cold bootstrap part differs from frozen transfer')
            item = state['files'].get(name)
            if item is None:
                if index != 0: raise TransactionError('cold bootstrap part order is invalid')
                item = {'device': None, 'inode': None, 'next_part': 0, 'inflight': None}
                state['files'][name] = item; self.save(state)
            if item['inflight'] is not None or item['next_part'] != index:
                raise TransactionError('cold bootstrap interrupted or out-of-order write requires retirement')
            path = self.work / ('bootstrap-' + name)
            records.parents(path)
            if item['inode'] is None:
                if path.exists() or path.is_symlink():
                    observed = self.inspect(path, 0)  # Creation intent permits only an empty orphan.
                    item.update(device=observed['device'], inode=observed['inode'])
                else:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    try:
                        info = os.fstat(fd); os.fsync(fd)
                        item.update(device=info.st_dev, inode=info.st_ino)
                        records.syncdir(self.work)
                    finally: os.close(fd)
                self.save(state)
            offset = sum(part['bytes'] for part in parts[:index])
            observed = self.inspect(path, offset, hash_bytes=False)
            if (observed['device'], observed['inode'], observed['bytes']) != (item['device'], item['inode'], offset):
                raise TransactionError('cold bootstrap receiver file identity changed')
            item['inflight'] = index; self.save(state)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino, info.st_size) != (item['device'], item['inode'], offset):
                    raise TransactionError('cold bootstrap receiver file changed before writing')
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(payload); stream.flush(); os.fsync(fd)
            finally: os.close(fd)
            item.update(next_part=index + 1, inflight=None); self.save(state)
            logging.info('Received cold bootstrap operation=%s artifact=%s part=%s bytes=%s', self.bundle.operation, name, index, len(payload))
            return {'status': 'part-received', 'artifact': name, 'part': index}

    def finish(self):
        with self.storage.lock():
            intent, request, state = self.load()
            self.original(intent['accepted'])
            if state['phase'] != 'receiving' or set(state['files']) != {'kernel', 'initramfs'}:
                raise TransactionError('cold bootstrap receiver is closed or incomplete')
            records.secure(self.work, directory=True)
            if {path.name for path in self.work.iterdir()} != {'bootstrap-kernel', 'bootstrap-initramfs'}:
                raise TransactionError('cold bootstrap receiver found unknown files')
            for name, item in state['files'].items():
                if item['inflight'] is not None or item['next_part'] != len(request['parts'][name]):
                    raise TransactionError('cold bootstrap receiver has an incomplete write')
                actual = self.inspect(self.work / ('bootstrap-' + name), request['capsule']['boot'][name]['bytes'])
                if ({key: actual[key] for key in ('bytes', 'sha256')} != request['capsule']['boot'][name] or
                        (actual['device'], actual['inode']) != (item['device'], item['inode'])):
                    raise TransactionError('cold bootstrap assembled bytes differ from the capsule')
            records.write(self.directory / 'filesystem-bootstrap.json', request['capsule'])
            state['phase'] = 'ready'; self.save(state)
            return {'status': 'staged', 'capsule_sha256': request['capsule']['record_sha256']}

    def retire(self, engine):
        """Close the receiver and collect only owned, never-used staging files."""
        if not generations.matches('[0-9a-f]{40}', engine):
            raise TransactionError('cold staging retirement requires the installed cleanup engine')
        with self.storage.lock():
            deadline = time.monotonic() + 600
            self.deadline = deadline
            intent, request, state = self.load(recover_empty=True)
            names = ['bootstrap-' + name for name in ('kernel', 'initramfs') if name in state['files']]
            def fresh():
                if time.monotonic() >= deadline: raise TransactionError('cold staging retirement time limit reached')
                self.original(intent['accepted'])
                records.secure(self.work, directory=True)
                if (any((self.directory / name).exists() or (self.directory / name).is_symlink() for name in (
                        'manifest.json', 'disk.json', 'outage-authorization.json', 'supervisor-ready.json',
                        'supervisor-result.json', 'filesystem.json', 'return-intent.json', 'completion.json')) or
                        {path.name for path in self.work.iterdir()} - set(names)):
                    raise TransactionError('cold staging retirement found used or unknown resources')
                backup = disks.DiskBackup(self.bundle)
                if any(row['lv_path'] == backup.path or backup.tag in row['lv_tags'].split(',') for row in disks.disks.inventory()):
                    raise TransactionError('cold staging retirement found a backup allocation')
                helper = 'router-cold-fs-' + self.bundle.operation
                identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'klokast-router-cold-fs-' + self.bundle.operation))
                paths = {str(self.work / name) for name in ('bootstrap-kernel', 'bootstrap-initramfs')}
                for row in self.host.inventory(deadline=min(deadline, time.monotonic() + 30)):
                    info = row['config']['c_info']; boot = row['config'].get('b_info', {})
                    if info['name'] == helper or info.get('uuid') == identity or boot.get('kernel') in paths or boot.get('ramdisk') in paths:
                        raise TransactionError('cold staging retirement found a live inspector or boot reference')
                if any(loops(path) for path in paths): raise TransactionError('cold staging retirement found a loop attachment')
                if records.read(self.intent_path) != intent:
                    raise TransactionError('cold staging retirement intent changed')
            fresh()
            # A receiver cannot write while this lock is held, and after release
            # every receiver must re-read this permanently closed phase.
            if state['phase'] in ('receiving', 'ready'):
                state['phase'] = 'retiring'; self.save(state)
            fixed = {'kind': 'klokast.router-cold-staging-cleanup-plan.v1', 'box': self.storage.box,
                'operation_id': self.bundle.operation, 'source_engine_commit': self.bundle.engine,
                'cleanup_engine_commit': engine, 'staging_sha256': intent['record_sha256'],
                'bootstrap_sha256': request['capsule']['record_sha256']}
            plan_path = self.directory / 'bootstrap-staging-cleanup-plan.json'
            progress_path = self.directory / 'bootstrap-staging-cleanup-progress.json'
            if plan_path.exists() or plan_path.is_symlink():
                plan = records.read(plan_path); generations.check_seal(plan)
                if set(plan) != set(fixed) | {'files', 'record_sha256'} or any(plan.get(k) != v for k, v in fixed.items()):
                    raise TransactionError('cold staging retirement plan changed')
            else:
                files = []
                for name in names:
                    item = state['files'][name.removeprefix('bootstrap-')]
                    path = self.work / name
                    if item['inode'] is None and not path.exists() and not path.is_symlink():
                        continue  # Durable creation intent, no inode or payload ever recorded.
                    parts = request['parts'][name.removeprefix('bootstrap-')]
                    minimum = sum(part['bytes'] for part in parts[:item['next_part']])
                    maximum = minimum + (parts[item['inflight']]['bytes'] if item['inflight'] is not None else 0)
                    actual = self.inspect(path, maximum)
                    if (actual['bytes'] < minimum or item['inode'] is not None and
                            (actual['device'], actual['inode']) != (item['device'], item['inode'])):
                        raise TransactionError('cold staging retirement file differs from its writer intent')
                    files.append({'name': name, **actual})
                plan = generations.seal({**fixed, 'files': files}); records.write(plan_path, plan)
            selected = [item.get('name') for item in plan['files'] if isinstance(item, dict)] if isinstance(plan.get('files'), list) else None
            if (selected is None or selected != [name for name in names if name in selected] or len(selected) != len(set(selected)) or
                    any(not isinstance(item, dict) or set(item) != {'name', 'device', 'inode', 'bytes', 'sha256'} or
                        type(item['device']) is not int or item['device'] < 0 or type(item['inode']) is not int or item['inode'] <= 0 or
                        type(item['bytes']) is not int or not 0 <= item['bytes'] <= request['capsule']['boot'][item['name'].removeprefix('bootstrap-')]['bytes'] or
                        not generations.matches('[0-9a-f]{64}', item['sha256']) for item in plan['files']) or
                    any(name not in selected and state['files'][name.removeprefix('bootstrap-')]['inode'] is not None for name in names)):
                raise TransactionError('cold staging retirement plan has invalid ownership')
            progress = records.read(progress_path) if progress_path.exists() or progress_path.is_symlink() else generations.seal({
                'kind': 'klokast.router-cold-staging-cleanup-progress.v1', 'plan_sha256': plan['record_sha256'], 'removed': [], 'inflight': None})
            generations.check_seal(progress)
            if (set(progress) != {'kind', 'plan_sha256', 'removed', 'inflight', 'record_sha256'} or
                    progress['kind'] != 'klokast.router-cold-staging-cleanup-progress.v1' or progress['plan_sha256'] != plan['record_sha256'] or
                    not isinstance(progress['removed'], list) or progress['removed'] != selected[:len(progress['removed'])] or
                    progress['inflight'] is not None and (len(progress['removed']) >= len(selected) or progress['inflight'] != selected[len(progress['removed'])])):
                raise TransactionError('cold staging retirement progress changed')
            def save():
                nonlocal progress
                progress = generations.seal({k: v for k, v in progress.items() if k != 'record_sha256'})
                records.write(progress_path, progress)
            save()
            for item in plan['files']:
                fresh(); path = self.work / item['name']; present = path.exists() or path.is_symlink()
                if item['name'] in progress['removed']:
                    if present: raise TransactionError('cold staged file reappeared after retirement')
                    continue
                if not present and progress['inflight'] != item['name']:
                    raise TransactionError('cold staged file disappeared without removal intent')
                if present:
                    if self.inspect(path, item['bytes']) != {k: v for k, v in item.items() if k != 'name'}:
                        raise TransactionError('cold staged file changed after retirement plan')
                    progress['inflight'] = item['name']; save(); fresh()
                    logging.info('Retiring cold staging operation=%s file=%s bytes=%s', self.bundle.operation, item['name'], item['bytes'])
                    path.unlink(); records.syncdir(self.work)
                progress['removed'].append(item['name']); progress['inflight'] = None; save()
            fresh()
            if any(path.exists() or path.is_symlink() for path in (self.work / name for name in names)):
                raise TransactionError('cold staging retirement found a late file')
            state['phase'] = 'retired'; self.save(state)
            complete = generations.seal({'kind': 'klokast.router-cold-staging-cleanup.v1',
                **{k: v for k, v in fixed.items() if k != 'kind'}, 'plan_sha256': plan['record_sha256'],
                'progress_sha256': progress['record_sha256'], 'bytes_reclaimed': sum(item['bytes'] for item in plan['files']),
                'status': 'unused-staging-retired'})
            target = self.directory / 'bootstrap-staging-cleanup-complete.json'
            if target.exists() or target.is_symlink():
                if records.read(target) != complete: raise TransactionError('cold staging retirement completion changed')
            else: records.write(target, complete)
            return complete


def loops(path):
    return sorted('/dev/' + item.parents[1].name for item in Path('/sys/block').glob(
        'loop*/loop/backing_file') if item.read_text().strip().removesuffix(' (deleted)').lstrip('/') == str(path).lstrip('/'))


class InspectorWrites:
    """Record file ownership before allocation and payload writes.

    The caller holds the router record lock. A failed write is not resumed;
    its inode and bounded creation intent stay available for exact retirement.
    """
    def __init__(self, bundle):
        self.bundle = bundle
        self.work = bundle.directory / 'filesystem'
        self.path = bundle.directory / 'filesystem-writes.json'

    def reserve(self, metadata, disk, capsule, job):
        if self.path.exists() or self.path.is_symlink():
            raise TransactionError('cold filesystem job already started; reconcile its file ownership')
        value = {'kind': 'klokast.router-cold-filesystem-writes.v1',
            'box': self.bundle.storage.box, 'operation_id': self.bundle.operation,
            'engine_commit': self.bundle.engine, 'metadata_sha256': metadata['record_sha256'],
            'disk_sha256': disk['record_sha256'], 'bootstrap_sha256': capsule['record_sha256'],
            'job_sha256': generations.digest(job), 'files': {}}
        self.save(value)

    def save(self, value):
        records.write(self.path, generations.seal({k: v for k, v in value.items() if k != 'record_sha256'}))

    def load(self):
        value = records.read(self.path); generations.check_seal(value)
        if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit', 'metadata_sha256',
                'disk_sha256', 'bootstrap_sha256', 'job_sha256', 'files', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-filesystem-writes.v1' or
                (value['box'], value['operation_id'], value['engine_commit']) !=
                    (self.bundle.storage.box, self.bundle.operation, self.bundle.engine) or
                any(not generations.matches('[0-9a-f]{64}', value[k]) for k in
                    ('metadata_sha256', 'disk_sha256', 'bootstrap_sha256', 'job_sha256')) or
                not isinstance(value['files'], dict) or set(value['files']) - {'job.slot', 'result.slot', 'guest.cfg'}):
            raise TransactionError('cold filesystem file ownership record changed')
        for name, item in value['files'].items():
            maximum = MIB if name.endswith('.slot') else 32768
            if (not isinstance(item, dict) or set(item) != {'device', 'inode', 'phase', 'bytes',
                    'payload_bytes', 'payload_sha256'} or
                    item['phase'] not in ('creating', 'writing', 'complete') or
                    type(item['bytes']) is not int or not 0 < item['bytes'] <= maximum or
                    name.endswith('.slot') and item['bytes'] != MIB or
                    type(item['payload_bytes']) is not int or not 0 <= item['payload_bytes'] <= min(32768, item['bytes']) or
                    name == 'result.slot' and item['payload_bytes'] != 0 or
                    name != 'result.slot' and item['payload_bytes'] == 0 or
                    name == 'guest.cfg' and item['payload_bytes'] != item['bytes'] or
                    not generations.matches('[0-9a-f]{64}', item['payload_sha256']) or
                    (item['device'] is None) != (item['inode'] is None) or
                    item['phase'] == 'creating' and item['inode'] is not None or
                    item['phase'] != 'creating' and (type(item['device']) is not int or item['device'] < 0 or
                        type(item['inode']) is not int or item['inode'] <= 0)):
                raise TransactionError('cold filesystem file ownership entry changed')
        return value

    def write(self, name, payload, *, slot=False):
        value = self.load()
        if (name not in ('job.slot', 'result.slot', 'guest.cfg') or slot != name.endswith('.slot') or
                not isinstance(payload, bytes) or len(payload) > 32768 or not slot and not payload):
            raise TransactionError('cold filesystem writer requires one bounded fixed file')
        if name == 'result.slot' and payload or name == 'job.slot' and not payload:
            raise TransactionError('cold filesystem slot payload differs from its fixed purpose')
        path = self.work / name
        if name in value['files'] or path.exists() or path.is_symlink():
            raise TransactionError('cold filesystem file already started; reconcile its exact inode')
        item = {'device': None, 'inode': None, 'phase': 'creating',
            'bytes': MIB if slot else len(payload), 'payload_bytes': len(payload),
            'payload_sha256': hashlib.sha256(payload).hexdigest()}
        value['files'][name] = item; self.save(value)
        # Creation intent is durable before O_EXCL; ownership is durable before
        # fallocate or write. Only an empty inode can precede ownership recording.
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor); os.fsync(descriptor); records.syncdir(self.work)
            item.update(device=info.st_dev, inode=info.st_ino, phase='writing'); self.save(value)
            with os.fdopen(descriptor, 'w+b', closefd=False) as stream:
                if slot: os.posix_fallocate(descriptor, 0, MIB)
                stream.write(payload); stream.flush(); os.fsync(descriptor)
            measured = BootstrapStaging(self.bundle).inspect(path, item['bytes'], hash_bytes=False)
            if (measured['device'], measured['inode'], measured['bytes']) != (item['device'], item['inode'], item['bytes']):
                raise TransactionError('cold filesystem written file differs from its ownership record')
            item['phase'] = 'complete'; self.save(value)
        finally:
            os.close(descriptor)


class Inspector:
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.backup = disks.DiskBackup(bundle)
        self.work = bundle.directory / 'filesystem'
        self.name = 'router-cold-fs-' + bundle.operation
        self.identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'klokast-router-cold-fs-' +
                                       bundle.operation))

    def capsule_record(self):
        if not (self.bundle.directory / 'filesystem-bootstrap.json').exists():
            raise TransactionError('cold filesystem boot capsule was not staged before the outage')
        value = records.read(self.bundle.directory / 'filesystem-bootstrap.json')
        return bootstrap_capsule(value, self.storage.box, self.bundle.operation, self.bundle.engine)

    def capsule(self):
        value=self.capsule_record()
        for name,expected in value['boot'].items():
            self.host.artifact({'path':str(self.work/('bootstrap-'+name)),**expected},deadline=time.monotonic()+90)
        return value

    def retire_used(self, engine, retirement):
        """Collect owned inspector files after exact used-backup retirement.

        The dispatcher first rechecks original health, test-disk retirement,
        supervisor completion and device cleanup through DiskBackup. No older
        writer can restart after the used-backup retirement intent.
        """
        import router_cold_cycle as cycle
        import router_cold_recovery as recovery
        if not generations.matches('[0-9a-f]{40}', engine):
            raise TransactionError('used inspector retirement requires the installed cleanup engine')
        with cycle.Cycle(self.bundle).exclusive(), self.storage.lock():
            deadline = time.monotonic() + 600
            measured = BootstrapStaging(self.bundle); measured.deadline = deadline
            metadata, generation = self.bundle.verify()
            disk = self.backup.validate(records.read(self.backup.record), metadata, generation)
            capsule = self.capsule_record()
            generations.check_seal(retirement)
            if (retirement.get('kind') != 'klokast.router-cold-used-backup-retirement.v1' or
                    retirement.get('status') != 'used-backup-retired' or
                    retirement.get('box') != self.storage.box or retirement.get('operation_id') != self.bundle.operation or
                    retirement.get('source_engine_commit') != self.bundle.engine or disk['stage'] != 'copied' or
                    records.read(self.bundle.directory / 'used-backup-retirement-complete.json') != retirement):
                raise TransactionError('used inspector retirement requires exact used-backup completion')
            staging_intent, request, staging = measured.load()
            if request['capsule'] != capsule or staging['phase'] != 'ready' or set(staging['files']) != {'kernel', 'initramfs'}:
                raise TransactionError('used inspector retirement lacks closed complete boot ownership')
            writer = InspectorWrites(self.bundle)
            ownership = writer.load() if writer.path.exists() or writer.path.is_symlink() else None
            if ownership is not None and any(ownership[key] != expected for key, expected in (
                    ('metadata_sha256', metadata['record_sha256']), ('disk_sha256', disk['record_sha256']),
                    ('bootstrap_sha256', capsule['record_sha256']),
                    ('job_sha256', generations.digest(self.job(capsule, metadata, disk))))):
                raise TransactionError('used inspector retirement file ownership selects another source')
            entries = {}
            for name in ('kernel', 'initramfs'):
                item = staging['files'][name]
                if item['inode'] is None or item['inflight'] is not None or item['next_part'] != len(request['parts'][name]):
                    raise TransactionError('used inspector retirement boot ownership is incomplete')
                entries['bootstrap-' + name] = {'device': item['device'], 'inode': item['inode'],
                    'minimum': capsule['boot'][name]['bytes'], 'maximum': capsule['boot'][name]['bytes'],
                    'sha256': capsule['boot'][name]['sha256']}
            for name in ('result.slot', 'job.slot', 'guest.cfg'):
                if ownership is not None and name in ownership['files']:
                    item = ownership['files'][name]
                    entries[name] = {'device': item['device'], 'inode': item['inode'],
                        'minimum': item['bytes'] if item['phase'] == 'complete' else 0,
                        'maximum': 0 if item['phase'] == 'creating' else item['bytes'],
                        'sha256': item['payload_sha256'] if name == 'guest.cfg' and item['phase'] == 'complete' else None}
            accepted = records.read(self.bundle.directory / 'accepted.json')
            completion = records.read(self.bundle.directory / 'completion.json')
            generations.check_seal(completion)
            baseline = recovery.Baseline(self.bundle)
            restored = baseline.restored(fenced=False)
            if (restored[:2] != (metadata, generation) or staging_intent['accepted'] != accepted or
                    retirement.get('completion_sha256') != completion['record_sha256']):
                raise TransactionError('used inspector retirement recovery or boot source changed')
            test_uuid = None
            archived = self.bundle.directory / 'test-state' / 'installation.json'
            if archived.exists() or archived.is_symlink(): test_uuid = records.read(archived)['disk']['uuid']
            initial = records.read(self.bundle.directory / 'supervised-request.json')['initial_operation']
            test_tag = 'routergen_' + initial
            def fresh():
                if time.monotonic() >= deadline: raise TransactionError('used inspector retirement time limit reached')
                self.host.guard(self.storage.box, deadline=min(deadline, time.monotonic() + 30))
                self.bundle.idle()
                if (self.storage.cold_test() is not None or self.storage.accepted() != accepted or
                        self.host.guest({'accepted': generation}, deadline=min(deadline, time.monotonic() + 30)) is None or
                        self.bundle.verify() != (metadata, generation) or records.read(self.backup.record) != disk or
                        records.read(self.bundle.directory / 'completion.json') != completion or
                        records.read(self.bundle.directory / 'used-backup-retirement-complete.json') != retirement or
                        baseline.restored(fenced=False) != restored or
                        self.capsule_record() != capsule or measured.load() != (staging_intent, request, staging) or
                        (writer.load() if writer.path.exists() or writer.path.is_symlink() else None) != ownership):
                    raise TransactionError('used inspector retirement source or recovered original changed')
                if self.backup.observe(disk['backup']['uuid']) is not None or any(
                        row['lv_path'] == '/dev/vg0/' + test_tag or test_tag in row['lv_tags'].split(',') or
                        test_uuid is not None and row['lv_uuid'] == test_uuid for row in disks.disks.inventory()):
                    raise TransactionError('used inspector retirement found a retired LV again')
                records.secure(self.work, directory=True)
                if {path.name for path in self.work.iterdir()} - set(entries):
                    raise TransactionError('used inspector retirement found unowned files')
                paths = {str(self.work / name) for name in entries}
                for row in self.host.inventory(deadline=min(deadline, time.monotonic() + 30)):
                    info = row['config']['c_info']; boot = row['config'].get('b_info', {})
                    if info['name'] == self.name or info.get('uuid') == self.identity or boot.get('kernel') in paths or boot.get('ramdisk') in paths:
                        raise TransactionError('used inspector retirement found a live inspector or boot reference')
                if any(loops(path) for path in paths): raise TransactionError('used inspector retirement found a loop attachment')
            fixed = {'kind': 'klokast.router-cold-used-inspector-cleanup-plan.v1', 'box': self.storage.box,
                'operation_id': self.bundle.operation, 'source_engine_commit': self.bundle.engine,
                'cleanup_engine_commit': engine, 'retirement_sha256': retirement['record_sha256'],
                'bootstrap_sha256': capsule['record_sha256'],
                'ownership_sha256': None if ownership is None else ownership['record_sha256']}
            plan_path = self.bundle.directory / 'used-inspector-cleanup-plan.json'
            progress_path = self.bundle.directory / 'used-inspector-cleanup-progress.json'
            fresh()
            if plan_path.exists() or plan_path.is_symlink():
                plan = records.read(plan_path); generations.check_seal(plan)
                if set(plan) != set(fixed) | {'files', 'record_sha256'} or any(plan.get(k) != v for k, v in fixed.items()):
                    raise TransactionError('used inspector retirement plan changed')
            else:
                files = []
                for name, item in entries.items():
                    path = self.work / name
                    if item['inode'] is None and not path.exists() and not path.is_symlink(): continue
                    actual = measured.inspect(path, item['maximum'])
                    if (actual['bytes'] < item['minimum'] or item['inode'] is not None and
                            (actual['device'], actual['inode']) != (item['device'], item['inode']) or
                            item['sha256'] is not None and actual['sha256'] != item['sha256']):
                        raise TransactionError('used inspector retirement file differs from its ownership')
                    files.append({'name': name, **actual})
                plan = generations.seal({**fixed, 'files': files}); records.write(plan_path, plan)
            selected = [item.get('name') for item in plan['files'] if isinstance(item, dict)] if isinstance(plan.get('files'), list) else None
            if (selected is None or selected != [name for name in entries if name in selected] or
                    len(selected) != len(set(selected)) or any(name not in selected and item['inode'] is not None for name, item in entries.items()) or
                    any(not isinstance(item, dict) or set(item) != {'name', 'device', 'inode', 'bytes', 'sha256'} or
                        type(item['device']) is not int or item['device'] < 0 or type(item['inode']) is not int or item['inode'] <= 0 or
                        type(item['bytes']) is not int or not entries[item['name']]['minimum'] <= item['bytes'] <= entries[item['name']]['maximum'] or
                        not generations.matches('[0-9a-f]{64}', item['sha256']) or
                        entries[item['name']]['inode'] is not None and (item['device'], item['inode']) !=
                            (entries[item['name']]['device'], entries[item['name']]['inode']) or
                        entries[item['name']]['sha256'] is not None and item['sha256'] != entries[item['name']]['sha256']
                        for item in plan['files'])):
                raise TransactionError('used inspector retirement plan has invalid ownership')
            progress = records.read(progress_path) if progress_path.exists() or progress_path.is_symlink() else generations.seal({
                'kind': 'klokast.router-cold-used-inspector-cleanup-progress.v1', 'plan_sha256': plan['record_sha256'],
                'removed': [], 'inflight': None})
            generations.check_seal(progress)
            if (set(progress) != {'kind', 'plan_sha256', 'removed', 'inflight', 'record_sha256'} or
                    progress['kind'] != 'klokast.router-cold-used-inspector-cleanup-progress.v1' or progress['plan_sha256'] != plan['record_sha256'] or
                    not isinstance(progress['removed'], list) or progress['removed'] != selected[:len(progress['removed'])] or
                    progress['inflight'] is not None and (len(progress['removed']) >= len(selected) or progress['inflight'] != selected[len(progress['removed'])])):
                raise TransactionError('used inspector retirement progress changed')
            def save():
                nonlocal progress
                progress = generations.seal({k: v for k, v in progress.items() if k != 'record_sha256'})
                records.write(progress_path, progress)
            save()
            for item in plan['files']:
                fresh(); path = self.work / item['name']; present = path.exists() or path.is_symlink()
                if item['name'] in progress['removed']:
                    if present: raise TransactionError('used inspector file reappeared after retirement')
                    continue
                if not present and progress['inflight'] != item['name']:
                    raise TransactionError('used inspector file disappeared without removal intent')
                if present:
                    if measured.inspect(path, item['bytes']) != {k: v for k, v in item.items() if k != 'name'}:
                        raise TransactionError('used inspector file changed after retirement plan')
                    progress['inflight'] = item['name']; save(); fresh()
                    logging.info('Retiring used cold inspector operation=%s file=%s bytes=%s', self.bundle.operation, item['name'], item['bytes'])
                    path.unlink(); records.syncdir(self.work)
                progress['removed'].append(item['name']); progress['inflight'] = None; save()
            fresh()
            if any(self.work.iterdir()): raise TransactionError('used inspector retirement found a late file')
            complete = generations.seal({'kind': 'klokast.router-cold-used-inspector-cleanup.v1',
                **{k: v for k, v in fixed.items() if k != 'kind'}, 'plan_sha256': plan['record_sha256'],
                'progress_sha256': progress['record_sha256'], 'bytes_reclaimed': sum(item['bytes'] for item in plan['files']),
                'status': 'used-inspector-retired'})
            target = self.bundle.directory / 'used-inspector-cleanup-complete.json'
            if target.exists() or target.is_symlink():
                if records.read(target) != complete: raise TransactionError('used inspector retirement completion changed')
            else: records.write(target, complete)
            return complete

    def retire_prepared_bootstrap(self, engine):
        """Retire two boot files only after an exact unused-backup abort."""
        if not generations.matches('[0-9a-f]{40}',engine):
            raise TransactionError('cold bootstrap retirement requires the installed cleanup engine')
        with self.storage.lock():
            deadline=time.monotonic()+600
            metadata,generation=self.bundle.verify()
            capsule=self.capsule_record()
            abort=records.read(self.bundle.directory/'prepared-abort-completion.json')
            intent=records.read(self.bundle.directory/'prepared-abort-intent.json')
            for value in (abort,intent):generations.check_seal(value)
            disk=self.backup.validate(records.read(self.backup.record),metadata,generation)
            if (abort.get('kind') != 'klokast.router-cold-prepared-abort.v1' or abort.get('status') != 'retired' or
                    intent.get('kind') != 'klokast.router-cold-prepared-abort-intent.v1' or
                    abort.get('intent_sha256') != intent['record_sha256'] or
                    any(value.get('box') != self.storage.box or value.get('operation_id') != self.bundle.operation or
                        value.get('source_engine_commit') != self.bundle.engine or
                        not generations.matches('[0-9a-f]{40}',value.get('cleanup_engine_commit')) for value in (abort,intent)) or
                    abort['cleanup_engine_commit'] != intent['cleanup_engine_commit'] or
                    intent.get('metadata_sha256') != metadata['record_sha256'] or
                    intent.get('backup_uuid') != disk['backup']['uuid'] or disk['stage'] != 'allocated'):
                raise TransactionError('cold bootstrap retirement lacks exact unused-backup completion')
            names=['bootstrap-kernel','bootstrap-initramfs']
            def fresh():
                if time.monotonic() >= deadline:raise TransactionError('cold bootstrap retirement time limit reached')
                self.host.guard(self.storage.box,deadline=min(deadline,time.monotonic()+30))
                self.bundle.idle()
                if (self.storage.cold_test() is not None or
                        self.storage.accepted() != records.read(self.bundle.directory/'accepted.json') or
                        self.host.guest({'accepted':generation},deadline=min(deadline,time.monotonic()+30)) is None or
                        any((self.bundle.directory/name).exists() or (self.bundle.directory/name).is_symlink()
                            for name in ('outage-authorization.json','supervisor-ready.json','supervisor-result.json',
                                         'filesystem.json','return-intent.json','completion.json'))):
                    raise TransactionError('cold bootstrap retirement requires the unchanged original and no outage')
                if any(row['lv_path'] == disk['backup']['path'] or row['lv_uuid'] == disk['backup']['uuid']
                       for row in disks.disks.inventory()):
                    raise TransactionError('cold bootstrap retirement found the retired backup LV again')
                records.secure(self.work,directory=True)
                if {path.name for path in self.work.iterdir()} - set(names):
                    raise TransactionError('cold bootstrap retirement found unknown inspector resources')
                for row in self.host.inventory(deadline=min(deadline,time.monotonic()+30)):
                    info=row['config']['c_info'];boot=row['config'].get('b_info',{})
                    if (info['name'] == self.name or info.get('uuid') == self.identity or
                            boot.get('kernel') in {str(self.work/name) for name in names} or
                            boot.get('ramdisk') in {str(self.work/name) for name in names}):
                        raise TransactionError('cold bootstrap retirement found a live inspector or boot reference')
                if any(loops(self.work/name) for name in names):
                    raise TransactionError('cold bootstrap retirement found a loop attachment')
                if (records.read(self.bundle.directory/'prepared-abort-completion.json') != abort or
                        records.read(self.bundle.directory/'prepared-abort-intent.json') != intent or
                        records.read(self.backup.record) != disk or
                        self.capsule_record() != capsule):
                    raise TransactionError('cold bootstrap retirement source changed')
            fixed={'kind':'klokast.router-cold-bootstrap-cleanup-plan.v1','box':self.storage.box,
                'operation_id':self.bundle.operation,'source_engine_commit':self.bundle.engine,
                'cleanup_engine_commit':engine,'abort_sha256':abort['record_sha256'],
                'bootstrap_sha256':capsule['record_sha256']}
            plan_path=self.bundle.directory/'bootstrap-cleanup-plan.json'
            progress_path=self.bundle.directory/'bootstrap-cleanup-progress.json'
            fresh()
            if plan_path.exists() or plan_path.is_symlink():
                plan=records.read(plan_path);generations.check_seal(plan)
                if any(plan.get(key) != value for key,value in fixed.items()) or set(plan) != set(fixed)|{'files','record_sha256'}:
                    raise TransactionError('cold bootstrap retirement plan changed')
            else:
                files=[]
                for name in names:
                    path=self.work/name;expected=capsule['boot'][name.removeprefix('bootstrap-')]
                    self.host.artifact({'path':str(path),**expected},deadline=min(deadline,time.monotonic()+90))
                    info=records.secure(path,maximum=expected['bytes']).stat()
                    files.append({'name':name,'device':info.st_dev,'inode':info.st_ino,**expected})
                plan=generations.seal({**fixed,'files':files});records.write(plan_path,plan)
            if (not isinstance(plan.get('files'),list) or [item.get('name') for item in plan['files'] if isinstance(item,dict)] != names or
                    any(not isinstance(item,dict) or set(item) != {'name','device','inode','bytes','sha256'} or
                        type(item['device']) is not int or item['device'] < 0 or type(item['inode']) is not int or item['inode'] <= 0 or
                        {key:item[key] for key in ('bytes','sha256')} != capsule['boot'][item['name'].removeprefix('bootstrap-')]
                        for item in plan['files'])):
                raise TransactionError('cold bootstrap retirement plan has invalid file identities')
            progress=records.read(progress_path) if progress_path.exists() or progress_path.is_symlink() else generations.seal({
                'kind':'klokast.router-cold-bootstrap-cleanup-progress.v1','plan_sha256':plan['record_sha256'],
                'removed':[],'inflight':None})
            generations.check_seal(progress)
            if (set(progress) != {'kind','plan_sha256','removed','inflight','record_sha256'} or
                    progress['kind'] != 'klokast.router-cold-bootstrap-cleanup-progress.v1' or
                    progress['plan_sha256'] != plan['record_sha256'] or not isinstance(progress['removed'],list) or
                    len(progress['removed']) > 2 or progress['removed'] != names[:len(progress['removed'])] or
                    progress['inflight'] is not None and (len(progress['removed']) == 2 or progress['inflight'] != names[len(progress['removed'])])):
                raise TransactionError('cold bootstrap retirement progress changed')
            def save():
                nonlocal progress
                progress=generations.seal({key:value for key,value in progress.items() if key != 'record_sha256'})
                records.write(progress_path,progress)
            save()
            for item in plan['files']:
                name=item['name'];path=self.work/name;present=path.exists() or path.is_symlink()
                if time.monotonic() >= deadline:raise TransactionError('cold bootstrap retirement time limit reached')
                fresh()
                if name in progress['removed']:
                    if present:raise TransactionError('cold bootstrap file reappeared after retirement')
                    continue
                if not present and progress['inflight'] != name:
                    raise TransactionError('cold bootstrap file disappeared without removal intent')
                if present:
                    info=records.secure(path,maximum=item['bytes']).stat()
                    if (info.st_dev,info.st_ino,info.st_size) != (item['device'],item['inode'],item['bytes']):
                        raise TransactionError('cold bootstrap file identity changed')
                    self.host.artifact({'path':str(path),**{key:item[key] for key in ('bytes','sha256')}},deadline=min(deadline,time.monotonic()+90))
                    progress['inflight']=name;save();fresh()
                    logging.info('Retiring unused cold bootstrap operation=%s file=%s bytes=%s',self.bundle.operation,name,item['bytes'])
                    path.unlink();records.syncdir(path.parent)
                progress['removed'].append(name);progress['inflight']=None;save()
            fresh()
            complete=generations.seal({'kind':'klokast.router-cold-bootstrap-cleanup.v1',**{key:fixed[key] for key in
                ('box','operation_id','source_engine_commit','cleanup_engine_commit','abort_sha256','bootstrap_sha256')},
                'plan_sha256':plan['record_sha256'],'progress_sha256':progress['record_sha256'],
                'bytes_reclaimed':sum(item['bytes'] for item in plan['files']),'status':'unused-bootstrap-retired'})
            target=self.bundle.directory/'bootstrap-cleanup-complete.json'
            if target.exists() or target.is_symlink():
                if records.read(target) != complete:raise TransactionError('cold bootstrap retirement completion changed')
            else:records.write(target,complete)
            return complete

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
        # libxl omits zero integer fields and empty arrays from xl list JSON.
        actual = [(item.get('vdev'), item.get('pdev_path'), item.get('readwrite', 0))
                  for item in config.get('disks', [])]
        if (info.get('name') != self.name or info.get('uuid') != self.identity or
                info.get('type') != 'pvh' or record.get('domid', 0) <= 0 or
                boot.get('kernel') != str(self.work / 'bootstrap-kernel') or
                boot.get('ramdisk') != str(self.work / 'bootstrap-initramfs') or
                boot.get('cmdline') != ('console=hvc0 panic=1 klokast_operation=' + self.bundle.operation +
                    ' klokast_inputs=' + capsule['inputs_sha256'] + ' klokast_job=' + generations.digest(job)) or
                boot.get('target_memkb') != 768 * 1024 or boot.get('max_vcpus') != 1 or
                config.get('nics', []) != [] or len(actual) != 3 or
                any(item.get('format') != 'raw' for item in config['disks']) or
                any(observed[0] != vdev or observed[2] != mode or
                    self.host.device(observed[1]) != self.host.device(path)
                    for observed, (vdev, path, mode) in zip(actual, expected))):
            raise TransactionError('paused cold filesystem guest differs from its networkless read-only capsule')

    def slot(self, path, *, content=None):
        if path not in (self.work / 'job.slot', self.work / 'result.slot'):
            raise TransactionError('cold filesystem slot path differs from its fixed operation')
        InspectorWrites(self.bundle).write(path.name, b'' if content is None else content + b'\0', slot=True)

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

    def abort(self):
        """Fence an interrupted inspector and detach only its recorded slots."""
        with self.storage.lock():
            self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
            records.secure(self.work, directory=True)
            metadata, generation = self.bundle.verify()
            disk = self.backup.validate(records.read(self.backup.record), metadata, generation)
            if disk['stage'] != 'copied':
                if (xen.domain(self.name) is not None or any(
                        (self.work / name).exists() or (self.work / name).is_symlink()
                        for name in ('result.slot', 'job.slot', 'guest.cfg'))):
                    raise TransactionError('cold filesystem job exists without a copied backup')
                return 'not-started'
            backup = self.backup.backup_disk(disk['backup']['uuid'])
            result_slot, job_slot = self.work / 'result.slot', self.work / 'job.slot'
            current = xen.domain(self.name)
            if current is not None:
                capsule = self.capsule()
                job = self.job(capsule, metadata, disk)
                for path in (result_slot, job_slot):
                    records.secure(path, maximum=MIB)
                    if path.stat().st_size != MIB:
                        raise TransactionError('cold filesystem guest has an incomplete recorded slot')
                result_loops, job_loops = loops(result_slot), loops(job_slot)
                if len(result_loops) != 1 or len(job_loops) != 1:
                    raise TransactionError('cold filesystem guest has ambiguous slot attachments')
                xen.require_identity(current, self.identity)
                self.paused(current, backup, result_loops[0], job_loops[0], capsule, job)
                xen.run(['xl', 'destroy', str(current['domid'])])
                if xen.domain(self.name) is not None:
                    raise TransactionError('cold filesystem guest remains after exact fencing')
            for path in (result_slot, job_slot):
                if path.exists() or path.is_symlink():
                    records.secure(path, maximum=MIB)
                    self.detach_loop(path)
            self.host.detached([backup['path']], deadline=time.monotonic() + 30)
            return 'destroyed' if current is not None else 'detached'

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
            writes = InspectorWrites(self.bundle)
            writes.reserve(metadata, disk, capsule, job)
            result_slot, job_slot = self.work / 'result.slot', self.work / 'job.slot'
            self.slot(result_slot)
            self.slot(job_slot, content=(json.dumps(job, sort_keys=True, separators=(',', ':')) + '\n').encode())
            result_loop, job_loop = None, None
            try:
                result_loop = self.loop(result_slot, False)
                job_loop = self.loop(job_slot, True)
                content = self.configuration(capsule, job, backup, result_loop, job_loop)
                cfg = self.work / 'guest.cfg'
                writes.write('guest.cfg', content.encode())
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
