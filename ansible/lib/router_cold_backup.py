"""Fixed metadata bundle for a supervised legacy-router cold backup.

This is a root-only dom0 primitive, not an authorization or a disk backup.
The caller must separately verify the cold LV copy and manage its hold name.
No archive-supplied destination is used. Original immutable registry entries
stay in place; the existing Records lock serializes capture and restoration.
"""
import hashlib
import os
from pathlib import Path
import secrets
import stat
import time

import router_generation_device as devices
import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import TransactionError

LIMITS = {'accepted.json': 1024 * 1024, 'generation.json': 1024 * 1024,
          'device.json': 1024 * 1024, 'xen.cfg': 1024 * 1024,
          'kernel': 32 * 1024 * 1024, 'initramfs': 128 * 1024 * 1024}


def file_info(path, maximum):
    records.parents(path)
    records.secure(path, maximum=maximum)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    return descriptor


def measure(path, maximum):
    """Bound and hash a regular file without loading a boot image into RAM."""
    with os.fdopen(file_info(path, maximum), 'rb') as stream:
        before = os.fstat(stream.fileno())
        digest, size = hashlib.sha256(), 0
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(block)
            if size > maximum:
                raise TransactionError('cold-backup file grew beyond its bound: ' + path.name)
            digest.update(block)
        after = os.fstat(stream.fileno())
    stable = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
              'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if (any(getattr(before, key) != getattr(after, key) for key in stable) or
            size != before.st_size or not size or
            not stat.S_ISREG(before.st_mode) or before.st_uid != records.ROOT_UID or
            before.st_nlink != 1 or before.st_mode & 0o7022):
        raise TransactionError('cold-backup file changed or has unsafe metadata: ' + path.name)
    return {'bytes': size, 'sha256': digest.hexdigest(), 'mode': stat.S_IMODE(before.st_mode)}


def copy_exact(source, destination, expected, maximum):
    """Publish verified bytes atomically; a partial write never becomes final."""
    records.parents(destination)
    if destination.exists() or destination.is_symlink():
        if measure(destination, maximum) != expected:
            raise TransactionError('cold-backup destination has different content: ' + destination.name)
        return
    temporary = destination.with_name('.' + destination.name + '-' + secrets.token_hex(12))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            with os.fdopen(file_info(source, maximum), 'rb') as stream:
                size, digest = 0, hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    size += len(block)
                    if size > maximum:
                        raise TransactionError('cold-backup source exceeds its fixed bound')
                    output.write(block)
                    digest.update(block)
            if size != expected['bytes'] or digest.hexdigest() != expected['sha256']:
                raise TransactionError('cold-backup source changed during copying')
            output.flush()
            os.fchmod(output.fileno(), expected['mode'])
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        records.syncdir(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


class Bundle:
    def __init__(self, storage, operation, engine, *, root=Path('/'), host=None):
        if (not generations.matches('[0-9a-f]{24}', operation) or
                not generations.matches('[0-9a-f]{40}', engine)):
            raise TransactionError('cold backup requires an exact operation and engine')
        self.storage, self.operation, self.engine = storage, operation, engine
        self.root, self.host = Path(root), host or native.Native()
        self.directory = storage.base / 'cold-backups' / operation

    def local(self, absolute):
        return self.root / absolute.removeprefix('/')

    def paths(self, generation, device):
        result = {'accepted.json': self.storage.base / 'accepted.json',
                  'generation.json': self.storage.base / 'records' / (generation['record_sha256'] + '.json'),
                  'xen.cfg': self.local('/etc/xen/router.cfg'),
                  **{name: self.local(item['path']) for name, item in generation['boot'].items()}}
        if device:
            result['device.json'] = devices.path(self.storage, generation['record_sha256'])
        return result

    def link(self):
        path = self.local('/etc/xen/auto/router.cfg')
        records.parents(path)
        info = path.lstat()
        target = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
        if info.st_uid != records.ROOT_UID or target not in ('../router.cfg', '/etc/xen/router.cfg'):
            raise TransactionError('cold backup requires the exact managed router autostart link')
        return target

    def idle(self):
        if self.storage.pending() is not None or self.storage.installation() is not None:
            raise TransactionError('cold metadata operation refuses a pending update or installation')

    def validate(self, value):
        generations.check_seal(value)
        if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit', 'generation_sha256',
                'disk_included', 'autostart_target', 'files', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-metadata.v1' or value['box'] != self.storage.box or
                value['operation_id'] != self.operation or value['engine_commit'] != self.engine or
                not generations.matches('[0-9a-f]{64}', value['generation_sha256']) or
                value['disk_included'] is not False or
                value['autostart_target'] not in ('../router.cfg', '/etc/xen/router.cfg') or
                not isinstance(value['files'], dict) or
                set(value['files']) not in (set(LIMITS), set(LIMITS) - {'device.json'})):
            raise TransactionError('cold metadata manifest is incomplete or selects another operation')
        for name, item in value['files'].items():
            if (not isinstance(item, dict) or set(item) != {'bytes', 'sha256', 'mode'} or
                    type(item['bytes']) is not int or not 0 < item['bytes'] <= LIMITS[name] or
                    not generations.matches('[0-9a-f]{64}', item['sha256']) or
                    type(item['mode']) is not int or not 0 < item['mode'] <= 0o777 or item['mode'] & 0o022):
                raise TransactionError('cold metadata manifest has an unsafe file descriptor')
        return value

    def verify(self, value=None):
        records.parents(self.directory)
        for directory in (self.directory.parent, self.directory):
            records.secure(directory, directory=True)
            if stat.S_IMODE(directory.stat().st_mode) != 0o700:
                raise TransactionError('cold backup requires a private bundle directory')
        value = self.validate(value if value is not None else records.read(self.directory / 'manifest.json'))
        for name, item in value['files'].items():
            if measure(self.directory / name, LIMITS[name]) != item:
                raise TransactionError('cold metadata bundle checksum differs: ' + name)
        assignment = records.assignment(records.read(self.directory / 'accepted.json'), self.storage.box)
        generation = generations.generation(records.read(self.directory / 'generation.json'), self.storage.box)
        if (assignment['current_sha256'] != value['generation_sha256'] or
                generation['record_sha256'] != value['generation_sha256'] or generation['origin'] != 'legacy' or
                assignment['previous_sha256'] is not None or
                assignment['policy_sha256'] != records.BASELINE_AUTHORITY_SHA256):
            raise TransactionError('cold metadata bundle does not select one accepted legacy router')
        if 'device.json' in value['files']:
            device = devices.validate(records.read(self.directory / 'device.json'),
                                      self.storage.box, value['generation_sha256'])
            if device['hostname'] not in (self.storage.box + '-router',
                    generations.tailnet_hostname(self.storage.box, generation['generation_id'])):
                raise TransactionError('cold metadata device name selects a different generation')
        actual = native.literal_configuration((self.directory / 'xen.cfg').read_text())
        expected = native.literal_configuration(generations.configuration(generation))
        actual.setdefault('uuid', expected['uuid'])
        if actual != expected:
            raise TransactionError('cold metadata Xen definition differs from its accepted generation')
        for name, item in generation['boot'].items():
            if any(value['files'][name][key] != item[key] for key in ('sha256', 'bytes')):
                raise TransactionError('cold metadata boot file differs from its accepted generation')
        return value, generation

    def capture(self, expected_generation):
        """Copy metadata only; never stop the router or remove its assignment."""
        self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
        with self.storage.lock():
            self.idle()
            staging_path = self.directory / 'bootstrap-staging-intent.json'
            if staging_path.exists() or staging_path.is_symlink():
                # New native receivers must be closed and fully verified.
                # Legacy capsules have no staging ledger and keep their path.
                import router_cold_filesystem
                staging = router_cold_filesystem.BootstrapStaging(self)
                intent, request, state = staging.load()
                if state['phase'] != 'ready' or state['files'].keys() != {'kernel', 'initramfs'} or any(
                        item['inflight'] is not None or item['next_part'] != len(request['parts'][name])
                        for name, item in state['files'].items()) or records.read(
                            self.directory / 'filesystem-bootstrap.json') != request['capsule']:
                    raise TransactionError('cold metadata capture requires a completed bootstrap receiver')
            assignment = self.storage.accepted()
            if (staging_path.exists() or staging_path.is_symlink()) and intent['accepted'] != assignment:
                raise TransactionError('cold metadata capture source differs from bootstrap staging')
            if assignment['current_sha256'] != expected_generation:
                raise TransactionError('cold backup accepted generation changed before capture')
            generation = self.storage.generation(expected_generation)
            if (generation['origin'] != 'legacy' or assignment['previous_sha256'] is not None or
                    assignment['policy_sha256'] != records.BASELINE_AUTHORITY_SHA256):
                raise TransactionError('cold metadata capture requires one accepted legacy router')
            device = devices.read(self.storage, expected_generation)
            paths = self.paths(generation, device is not None)
            value = self.validate(generations.seal({
                'kind': 'klokast.router-cold-metadata.v1', 'box': self.storage.box,
                'operation_id': self.operation, 'engine_commit': self.engine,
                'generation_sha256': expected_generation, 'disk_included': False,
                'autostart_target': self.link(),
                'files': {name: measure(path, LIMITS[name]) for name, path in paths.items()}}))
            for directory in (self.directory.parent, self.directory):
                records.parents(directory)
                if not directory.exists() and not directory.is_symlink():
                    directory.mkdir(mode=0o700)
                    records.syncdir(directory.parent)
                records.secure(directory, directory=True)
                if stat.S_IMODE(directory.stat().st_mode) != 0o700:
                    raise TransactionError('cold backup requires a private bundle directory')
            intent = self.directory / 'intent.json'
            if intent.exists() or intent.is_symlink():
                if records.read(intent) != value:
                    raise TransactionError('cold backup retry found changed metadata')
            else:
                records.write(intent, value)
            for name, source in paths.items():
                copy_exact(source, self.directory / name, value['files'][name], LIMITS[name])
            self.verify(value)
            manifest = self.directory / 'manifest.json'
            if manifest.exists() or manifest.is_symlink():
                if records.read(manifest) != value:
                    raise TransactionError('cold backup completion differs from its recorded intent')
            else:
                records.write(manifest, value)
            self.verify()
            return value

    def restore(self):
        """Restore fixed metadata after native recovery restores the original LV.

        The caller must first archive and retire the exact test installation.
        The restored Xen definition pins the recorded UUID, even when the
        archived legacy definition left that field implicit. This does not
        boot a guest, commit LBU, or claim disk-copy verification.
        """
        deadline = time.monotonic() + 120
        self.host.guard(self.storage.box, deadline=deadline)
        with self.storage.lock():
            value, generation = self.verify()
            self.idle()
            self.host.disk(generation['disk'], deadline=deadline)
            if self.host.guest({'accepted': generation}, deadline=deadline) is not None:
                raise TransactionError('cold metadata restoration requires the router stopped')
            self.host.detached([generation['disk']['path']], deadline=deadline)
            paths = self.paths(generation, 'device.json' in value['files'])
            configuration = generations.configuration(generation).encode()
            restored_config = {'bytes': len(configuration),
                               'sha256': hashlib.sha256(configuration).hexdigest(), 'mode': 0o600}
            # Check every destination before changing any of them. Refuse a new
            # test assignment, config, device record, or unrelated boot file.
            for name, destination in paths.items():
                records.parents(destination)
                if destination.exists() or destination.is_symlink():
                    actual = measure(destination, LIMITS[name])
                    allowed = [value['files'][name]] + ([restored_config] if name == 'xen.cfg' else [])
                    if actual not in allowed:
                        raise TransactionError('cold metadata restore found a different live file: ' + name)
            link = self.local('/etc/xen/auto/router.cfg')
            records.parents(link)
            if (link.exists() or link.is_symlink()) and self.link() != value['autostart_target']:
                raise TransactionError('cold metadata restore found a different autostart link')
            # Publish the accepted pointer after all of its files are durable.
            for name in [key for key in paths if key not in ('accepted.json', 'xen.cfg')]:
                copy_exact(self.directory / name, paths[name], value['files'][name], LIMITS[name])
            records.atomic(paths['xen.cfg'], configuration)
            copy_exact(self.directory / 'accepted.json', paths['accepted.json'],
                       value['files']['accepted.json'], LIMITS['accepted.json'])
            if not link.exists() and not link.is_symlink():
                link.symlink_to(value['autostart_target'])
                records.syncdir(link.parent)
            return value
