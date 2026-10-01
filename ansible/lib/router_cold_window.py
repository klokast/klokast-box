"""Exact legacy-LV hold and restore steps for a supervised first-install test.

The active test marker is a boot fence, never an outage grant. A future
supervisor must own stop, filesystem qualification, test cleanup, service
verification, and marker removal. These primitives do not remove that fence.
"""
import re
import time
from pathlib import Path

import router_cold_backup as metadata_files
import router_cold_disk as cold_disk
import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import TransactionError
from xen_build_runtime import checksum


class Window:
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.backup = cold_disk.DiskBackup(bundle)
        self.marker = self.storage.base / 'cold-test.json'
        self.hold_name = 'routercold_' + bundle.operation + '_hold'
        self.hold_path = '/dev/vg0/' + self.hold_name

    def context(self):
        self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
        metadata, generation = self.bundle.verify()
        value = self.storage.cold_test()
        if (value is None or value['operation_id'] != self.bundle.operation or
                value['engine_commit'] != self.bundle.engine or
                value['metadata_sha256'] != metadata['record_sha256'] or
                value['generation_sha256'] != generation['record_sha256']):
            raise TransactionError('cold router window differs from its protected metadata bundle')
        return value, metadata, generation

    def phase(self, value, phase):
        updated = generations.seal({**{key: item for key, item in value.items() if key != 'record_sha256'},
                                    'phase': phase})
        records.write(self.marker, updated)
        return self.storage.cold_test()

    def arm(self, initial_operation, expires_at):
        """Persist the boot fence before the separately authorized router stop."""
        now = int(time.time())
        if (not generations.matches('[0-9a-f]{24}', initial_operation) or
                initial_operation == self.bundle.operation or type(expires_at) is not int or
                not now < expires_at <= now + 7200):
            raise TransactionError('cold test needs a separate initial operation and a window of at most two hours')
        with self.storage.lock():
            self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
            self.bundle.idle()
            metadata, generation = self.bundle.verify()
            current = self.storage.cold_test()
            if current is not None:
                self.context()
                if current['initial_operation'] != initial_operation or current['expires_at'] != expires_at:
                    raise TransactionError('cold test retry cannot change its initial operation or deadline')
                return current
            saved_assignment = records.read(self.bundle.directory / 'accepted.json')
            if self.storage.accepted() != saved_assignment:
                raise TransactionError('cold test accepted assignment changed since its backup')
            if native.command(['/usr/sbin/lbu', 'status'], time.monotonic() + 30).strip():
                raise TransactionError('cold test requires clean dom0 persistence before arming')
            self.backup.validate(records.read(self.backup.record), metadata, generation)
            # Capture alone is not permission to stop the router. The caller
            # must enter through its supervised authorization before this step.
            value = generations.seal({'kind': 'klokast.router-cold-test.v1', 'box': self.storage.box,
                'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
                'metadata_sha256': metadata['record_sha256'], 'generation_sha256': generation['record_sha256'],
                'initial_operation': initial_operation, 'armed_at': now, 'expires_at': expires_at, 'phase': 'armed'})
            records.write(self.marker, value)
            return self.storage.cold_test()

    def located(self, generation):
        """Only the recorded old UUID may occupy either of the two fixed names."""
        original = generation['disk']
        rows = cold_disk.disks.inventory()
        matches = [row for row in rows if row['lv_path'] in (original['path'], self.hold_path) or
                   row['lv_uuid'] == original['uuid']]
        if len(matches) != 1:
            raise TransactionError('cold router original and hold names are missing, occupied, or ambiguous')
        row = matches[0]
        if (row['lv_path'] not in (original['path'], self.hold_path) or row['lv_uuid'] != original['uuid'] or
                row['lv_size'] != str(original['bytes']) or row['origin'] or row['lv_tags'] or
                not row['lv_attr'].startswith('-wi-a')):
            raise TransactionError('cold router held LV identity or independent allocation changed')
        disk = {**original, 'path': row['lv_path']}
        deadline = time.monotonic() + 30
        self.host.disk(disk, deadline=deadline)
        expected = native.literal_configuration(generations.configuration(generation))
        if self.host.initial_guest(disk, expected, deadline=deadline) is not None:
            raise TransactionError('cold router hold or restoration requires all router guests stopped')
        self.host.detached([disk['path']], deadline=deadline)
        return disk

    def verified_backup(self, metadata, generation):
        value = self.backup.validate(records.read(self.backup.record), metadata, generation)
        if value['stage'] != 'copied':
            raise TransactionError('cold router hold requires a completed independent disk backup')
        disk = self.backup.backup_disk(value['backup']['uuid'])
        deadline = time.monotonic() + 30
        self.host.disk(disk, deadline=deadline)
        self.host.detached([disk['path']], deadline=deadline)
        if checksum(Path(disk['path']), disk['bytes']) != value['source_sha256']:
            raise TransactionError('cold router backup checksum changed; keep the original LV')
        return value

    def hold(self):
        """Rename only the cold original, with durable intent before lvrename."""
        with self.storage.lock():
            value, metadata, generation = self.context()
            if value['phase'] not in ('armed', 'holding', 'held') or time.time() >= value['expires_at']:
                raise TransactionError('cold router hold is outside its active test window')
            self.bundle.idle()
            if self.storage.accepted() != records.read(self.bundle.directory / 'accepted.json'):
                raise TransactionError('cold router assignment changed before holding its LV')
            backup = self.verified_backup(metadata, generation)
            proof = records.read(self.bundle.directory / 'filesystem.json')
            if proof != {'kind': 'klokast.router-cold-filesystem.v1', 'operation_id': self.bundle.operation,
                    'metadata_sha256': metadata['record_sha256'], 'disk_sha256': backup['record_sha256'],
                    'backup_uuid': backup['backup']['uuid'], 'readonly': True, 'root_verified': True}:
                raise TransactionError('cold router backup lacks its exact read-only filesystem proof')
            disk = self.located(generation)
            if checksum(Path(disk['path']), disk['bytes']) != backup['source_sha256']:
                raise TransactionError('cold router original changed after backup; do not hold it')
            if value['phase'] == 'armed' and disk['path'] == self.hold_path:
                raise TransactionError('cold router LV was renamed without a recorded hold intent')
            if value['phase'] == 'held' and disk['path'] != self.hold_path:
                raise TransactionError('cold router held LV reappeared under its original name')
            if value['phase'] != 'held':
                value = self.phase(value, 'holding')
                if disk['path'] != self.hold_path:
                    native.command(['/sbin/lvrename', 'vg0', 'lv_router', self.hold_name], time.monotonic() + 30)
                if self.located(generation)['path'] != self.hold_path:
                    raise TransactionError('cold router LV rename did not reach its exact hold name')
                value = self.phase(value, 'held')
            return value

    def commit_xen(self):
        """Persist only the two fixed guest boot paths, never unrelated drift."""
        deadline = time.monotonic() + 120
        changed = native.command(['/usr/sbin/lbu', 'status'], deadline)
        if any(not re.fullmatch(r'[AUD] etc/xen/(?:auto/)?router\.cfg', line)
               for line in changed.splitlines()):
            raise TransactionError('cold router persistence found changes outside its two Xen boot paths')
        native.command(['/usr/sbin/lbu', 'commit', '-d'], deadline, maximum_seconds=120)
        if native.command(['/usr/sbin/lbu', 'status'], deadline).strip():
            raise TransactionError('cold router persistence is not clean after its commit')

    def open_target(self):
        """Remove only backed-up live selectors; retain all immutable records."""
        with self.storage.lock():
            value, metadata, generation = self.context()
            if value['phase'] not in ('held', 'opening') or time.time() >= value['expires_at']:
                raise TransactionError('cold fresh-install target is outside its held test window')
            self.bundle.idle()
            if self.located(generation)['path'] != self.hold_path:
                raise TransactionError('cold fresh-install target requires the original LV held')
            self.verified_backup(metadata, generation)
            paths = self.bundle.paths(generation, 'device.json' in metadata['files'])
            for name in ('xen.cfg', 'accepted.json'):
                path = paths[name]
                if path.exists() or path.is_symlink():
                    if metadata_files.measure(path, metadata_files.LIMITS[name]) != metadata['files'][name]:
                        raise TransactionError('cold target refuses a changed live selector: ' + name)
                elif value['phase'] != 'opening':
                    raise TransactionError('cold target selector disappeared before its durable removal intent')
            link = self.bundle.local('/etc/xen/auto/router.cfg')
            if link.exists() or link.is_symlink():
                if self.bundle.link() != metadata['autostart_target']:
                    raise TransactionError('cold target autostart changed before its removal')
            elif value['phase'] != 'opening':
                raise TransactionError('cold target autostart disappeared before its removal intent')
            value = self.phase(value, 'opening')
            for path in (link, paths['xen.cfg'], paths['accepted.json']):
                if path.exists() or path.is_symlink():
                    path.unlink()
                    records.syncdir(path.parent)
            self.commit_xen()
            return self.phase(value, 'open')

    def restore_disk(self):
        """Restore the held original, repairing only its exact LV from the backup.

        The test installation must already be stopped and archived. This step
        leaves the boot fence set and never boots the router.
        """
        with self.storage.lock():
            value, metadata, generation = self.context()
            if value['phase'] == 'restored':
                raise TransactionError('cold router already restored; do not recopy its newer state')
            self.bundle.idle()
            accepted = self.storage.base / 'accepted.json'
            if (accepted.exists() or accepted.is_symlink()) and self.storage.accepted() != records.read(
                    self.bundle.directory / 'accepted.json'):
                raise TransactionError('cold restoration must first archive the exact test assignment')
            disk = self.located(generation)
            value = self.phase(value, 'restoring')
            if disk['path'] == self.hold_path:
                backup = self.verified_backup(metadata, generation)
                if checksum(Path(disk['path']), disk['bytes']) != backup['source_sha256']:
                    # The original has been cold since hold. No generation's
                    # newer live state may be overwritten through this path.
                    native.command(['/bin/dd', 'if=' + backup['backup']['path'], 'of=' + self.hold_path,
                                    'bs=4M', 'count=512', 'conv=notrunc,fsync'],
                                   time.monotonic() + 180, maximum_seconds=180)
                    self.located(generation)
                    self.verified_backup(metadata, generation)
                    if checksum(Path(disk['path']), disk['bytes']) != backup['source_sha256']:
                        raise TransactionError('cold backup could not restore the held original; keep it fenced')
                native.command(['/sbin/lvrename', 'vg0', self.hold_name, 'lv_router'], time.monotonic() + 30)
            if self.located(generation) != generation['disk']:
                raise TransactionError('cold restoration did not recover the exact original LV name and UUID')
            return value
