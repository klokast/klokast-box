"""Archive one stopped first-install test before restoring the held router.

This step copies bounded protected records. It never removes a live selector,
guest, or disk. Later cleanup must use this exact archive as its intent.
"""
import os
from pathlib import Path
import stat
import time

import router_candidate_disk as candidate_disks
import router_cold_backup as metadata_files
import router_cold_window as cold_window
import router_generation_device as devices
import router_generations as generations
import router_initial_installation as initial_installation
import router_records as records
from router_transaction import TransactionError

MAX_FILES = 256
MAX_TOTAL = 32 * 1024 * 1024


class TestState:
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.window = cold_window.Window(bundle)
        self.archive = bundle.directory / 'test-state'

    def verify(self):
        records.secure(self.archive, directory=True)
        if stat.S_IMODE(self.archive.stat().st_mode) != 0o700:
            raise TransactionError('cold test archive directory is not private')
        value = records.read(self.archive / 'manifest.json')
        generations.check_seal(value)
        fixed = {'installation.json', 'accepted.json', 'generation.json', 'device.json'}
        if (set(value) != {'kind', 'box', 'operation_id', 'initial_operation',
                'engine_commit', 'installation_sha256', 'accepted_sha256',
                'generation_sha256', 'machine_id', 'files', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-test-archive.v1' or
                value['box'] != self.storage.box or value['operation_id'] != self.bundle.operation or
                value['engine_commit'] != self.bundle.engine or
                not generations.matches('[0-9a-f]{24}', value['initial_operation']) or
                not generations.matches('[0-9a-f]{64}', value['installation_sha256']) or
                (value['accepted_sha256'] is not None and not generations.matches(
                    '[0-9a-f]{64}', value['accepted_sha256'])) or
                (value['generation_sha256'] is not None and not generations.matches(
                    '[0-9a-f]{64}', value['generation_sha256'])) or
                (value['accepted_sha256'] is None) != (value['generation_sha256'] is None) or
                (value['machine_id'] is not None and not generations.matches(
                    '[A-Za-z0-9_-]{1,128}', value['machine_id'])) or
                not isinstance(value['files'], dict) or not value['files'] or
                len(value['files']) > MAX_FILES + 4 or 'installation.json' not in value['files'] or
                bool(value['accepted_sha256']) != ('accepted.json' in value['files']) or
                bool(value['generation_sha256']) != ('generation.json' in value['files'])):
            raise TransactionError('cold test archive manifest has an invalid test identity')
        for name, info in value['files'].items():
            parts = Path(name).parts
            if (name not in fixed and (len(parts) < 2 or parts[0] != 'operation' or
                    not all(part and part not in ('.', '..') for part in parts[1:]) or
                    not name.endswith('.json'))):
                raise TransactionError('cold test archive contains an unsafe record path')
            if (not isinstance(info, dict) or set(info) != {'bytes', 'sha256', 'mode'} or
                    type(info['bytes']) is not int or not 0 < info['bytes'] <= 1024 * 1024 or
                    not generations.matches('[0-9a-f]{64}', info['sha256']) or
                    type(info['mode']) is not int):
                raise TransactionError('cold test archive contains an invalid record descriptor')
            if metadata_files.measure(self.archive / name, 1024 * 1024) != info:
                raise TransactionError('cold test archive record changed: ' + name)
        test = initial_installation.validate(records.read(self.archive / 'installation.json'), self.storage.box)
        if (test['record_sha256'] != value['installation_sha256'] or
                test['operation_id'] != value['initial_operation'] or
                test['engine_commit'] != self.bundle.engine or test['machine_id'] != value['machine_id']):
            raise TransactionError('cold test archive installation differs from its manifest')
        if value['accepted_sha256'] is not None:
            accepted = records.assignment(records.read(self.archive / 'accepted.json'), self.storage.box)
            generation = generations.generation(records.read(self.archive / 'generation.json'), self.storage.box)
            if (accepted['record_sha256'] != value['accepted_sha256'] or
                    accepted['current_sha256'] != value['generation_sha256'] or
                    accepted['operation_id'] != value['initial_operation'] or
                    accepted['policy_sha256'] != records.INITIAL_AUTHORITY_SHA256 or
                    generation['record_sha256'] != value['generation_sha256'] or
                    generation['disk'] != test['disk'] or test['stage'] != 'verified'):
                raise TransactionError('cold test archive accepted router differs from its manifest')
            if 'device.json' in value['files']:
                device = devices.validate(records.read(self.archive / 'device.json'),
                                          self.storage.box, generation['record_sha256'])
                if device['machine_id'] != test['machine_id']:
                    raise TransactionError('cold test archive Tailnet device differs from its installation')
        return value

    def files(self, operation):
        """Select only regular JSON operation records, with bounded traversal."""
        result, total, entries = {}, 0, 0
        for root, directories, names in os.walk(operation, followlinks=False):
            current = Path(root)
            entries += len(directories) + len(names)
            if len(current.relative_to(operation).parts) > 8:
                raise TransactionError('cold test operation record tree is too deep')
            if entries > MAX_FILES:
                raise TransactionError('cold test operation record tree is too large')
            for name in directories:
                path = current / name
                records.secure(path, directory=True)
                if stat.S_IMODE(path.stat().st_mode) != 0o700:
                    raise TransactionError('cold test operation record directory is not private')
            for name in names:
                path = current / name
                if not name.endswith('.json'):
                    continue
                relative = path.relative_to(operation)
                info = metadata_files.measure(path, 1024 * 1024)
                result['operation/' + str(relative)] = (path, info)
                total += info['bytes']
                if len(result) > MAX_FILES or total > MAX_TOTAL:
                    raise TransactionError('cold test operation records exceed their archive bound')
        if not result:
            raise TransactionError('cold test has no operation records to archive')
        return result

    def source(self):
        marker, metadata, original = self.window.context()
        if marker['phase'] != 'open' or self.window.located(original)['path'] != self.window.hold_path:
            raise TransactionError('cold test archive requires the held original and open window')
        self.window.verified_backup(metadata, original)
        if self.storage.pending() is not None:
            raise TransactionError('cold test archive refuses a pending router transaction')
        test = self.storage.installation()
        if test is None or test['operation_id'] != marker['initial_operation'] or test['engine_commit'] != self.bundle.engine:
            raise TransactionError('cold test archive has no exact first installation')
        operation = self.storage.operation(marker['initial_operation'])
        assignment_path = self.storage.base / 'accepted.json'
        accepted = None
        if assignment_path.exists() or assignment_path.is_symlink():
            accepted = self.storage.accepted()
            if (accepted['policy_sha256'] != records.INITIAL_AUTHORITY_SHA256 or
                    accepted['operation_id'] != marker['initial_operation'] or
                    accepted['previous_sha256'] is not None or
                    accepted['engine_commit'] != self.bundle.engine):
                raise TransactionError('cold test archive refuses an unrelated accepted router')
        if test['stage'] == 'verified' and accepted is None:
            raise TransactionError('cold test archive lacks its verified test assignment')
        if accepted is not None and test['stage'] != 'verified':
            raise TransactionError('cold test archive has an incomplete accepted installation')
        disk_path = operation / 'candidate-disk.json'
        if test['stage'] == 'planned' and not disk_path.exists() and not disk_path.is_symlink():
            if candidate_disks.observed(marker['initial_operation']) is not None:
                raise TransactionError('cold test archive found an unrecorded test LV')
        else:
            disk_record = candidate_disks.record(operation, marker['initial_operation'])
            if test['stage'] == 'planned' and disk_record['stage'] == 'planned':
                if candidate_disks.observed(marker['initial_operation']) is not None:
                    raise TransactionError('cold test archive found an unrecorded test LV')
            elif (disk_record['stage'] not in ('allocated', 'cloned') or
                    test['disk'] != candidate_disks.verify(operation, marker['initial_operation'])):
                raise TransactionError('cold test archive disk differs from its installation')
        generation, device = None, None
        if accepted is not None:
            generation = self.storage.generation(accepted['current_sha256'])
            device = devices.read(self.storage, generation['record_sha256'])
            if generation['disk'] != test['disk'] or generation['generation_id'] != marker['initial_operation']:
                raise TransactionError('cold test archive accepted generation differs from its installation')
        return marker, test, operation, accepted, generation, device

    def capture(self):
        """Publish one sealed archive manifest only after all copies verify."""
        with self.storage.lock():
            self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
            marker, test, operation, accepted, generation, device = self.source()
            files = self.files(operation)
            targets = {'installation.json': self.storage.base / 'installation.json'}
            if accepted is not None:
                targets['accepted.json'] = self.storage.base / 'accepted.json'
                targets['generation.json'] = self.storage.base / 'records' / (generation['record_sha256'] + '.json')
                if device is not None:
                    targets['device.json'] = devices.path(self.storage, generation['record_sha256'])
            for name, path in targets.items():
                files[name] = (path, metadata_files.measure(path, 1024 * 1024))
            manifest = generations.seal({'kind': 'klokast.router-cold-test-archive.v1',
                'box': self.storage.box, 'operation_id': self.bundle.operation,
                'initial_operation': marker['initial_operation'], 'engine_commit': self.bundle.engine,
                'installation_sha256': test['record_sha256'],
                'accepted_sha256': accepted['record_sha256'] if accepted else None,
                'generation_sha256': generation['record_sha256'] if generation else None,
                'machine_id': test['machine_id'],
                'files': {name: info for name, (_, info) in files.items()}})
            if not self.archive.exists() and not self.archive.is_symlink():
                self.archive.mkdir(mode=0o700)
                records.syncdir(self.archive.parent)
            records.secure(self.archive, directory=True)
            if stat.S_IMODE(self.archive.stat().st_mode) != 0o700:
                raise TransactionError('cold test archive directory is not private')
            saved = self.archive / 'manifest.json'
            if saved.exists() or saved.is_symlink():
                if records.read(saved) != manifest:
                    raise TransactionError('cold test archive retry found changed source records')
            for name, (source, info) in files.items():
                target = self.archive / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                metadata_files.copy_exact(source, target, info, 1024 * 1024)
            for name, (source, info) in files.items():
                if metadata_files.measure(source, 1024 * 1024) != info or \
                        metadata_files.measure(self.archive / name, 1024 * 1024) != info:
                    raise TransactionError('cold test archive source or copied record changed')
            if not saved.exists() and not saved.is_symlink():
                records.write(saved, manifest)
            if self.verify() != manifest:
                raise TransactionError('cold test archive manifest changed after capture')
            return manifest
