"""Archive and retire one stopped first-install test before old-router restore.

The archive fixes all selectors before removal. The stopped test guest and
held original remain fenced while its exact disk is retired.
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
from xen_build_runtime import domain

MAX_FILES = 256
MAX_TOTAL = 32 * 1024 * 1024


class TestState:
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.window = cold_window.Window(bundle)
        self.archive = bundle.directory / 'test-state'

    def record_unstarted(self):
        """Preserve staged inputs when the first install never allocated a disk."""
        with self.storage.lock():
            self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
            marker, metadata, original = self.window.context()
            if marker['phase'] != 'open' or self.window.located(original)['path'] != self.window.hold_path:
                raise TransactionError('unstarted cold test requires its held original and open window')
            self.window.verified_backup(metadata, original)
            if (self.storage.pending() is not None or
                    any(path.exists() or path.is_symlink() for path in (
                        self.storage.base / 'installation.json', self.storage.base / 'accepted.json',
                        self.bundle.local('/etc/xen/router.cfg'),
                        self.bundle.local('/etc/xen/auto/router.cfg'), self.archive)) or
                    domain('router') is not None):
                raise TransactionError('unstarted cold test found an installation, selector, or running router')
            if any(row['lv_path'].startswith('/dev/vg0/routergen_') or
                    any(tag.startswith('routergen_') for tag in row['lv_tags'].split(','))
                    for row in candidate_disks.inventory()):
                raise TransactionError('unstarted cold test found a router generation LV without its installation')
            operation = self.storage.base / 'operations' / marker['initial_operation']
            if operation.exists() or operation.is_symlink():
                records.secure(operation, directory=True)
                if stat.S_IMODE(operation.stat().st_mode) != 0o700:
                    raise TransactionError('unstarted cold test operation directory is not private')
                files = self.files(operation, empty_ok=True)
            else:
                files = {}
            if 'operation/candidate-disk.json' in files:
                raise TransactionError('unstarted cold test found a disk record without its installation')
            value = generations.seal({'kind': 'klokast.router-cold-unstarted-test.v1',
                'box': self.storage.box, 'operation_id': self.bundle.operation,
                'initial_operation': marker['initial_operation'],
                'engine_commit': self.bundle.engine, 'marker_sha256': marker['record_sha256'],
                'files': {name: info for name, (_, info) in files.items()},
                'status': 'never-allocated'})
            destination = self.bundle.directory / 'no-installation'
            if not destination.exists() and not destination.is_symlink():
                destination.mkdir(mode=0o700)
                records.syncdir(destination.parent)
            records.secure(destination, directory=True)
            if stat.S_IMODE(destination.stat().st_mode) != 0o700:
                raise TransactionError('unstarted cold test archive directory is not private')
            manifest = destination / 'manifest.json'
            if manifest.exists() or manifest.is_symlink():
                if records.read(manifest) != value:
                    raise TransactionError('unstarted cold test retry found changed staged inputs')
            for name, (source, info) in files.items():
                target = destination / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                metadata_files.copy_exact(source, target, info, 1024 * 1024)
                if metadata_files.measure(source, 1024 * 1024) != info:
                    raise TransactionError('unstarted cold test staged input changed during archive: ' + name)
            if not manifest.exists() and not manifest.is_symlink():
                records.write(manifest, value)
            return value

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

    def files(self, operation, *, empty_ok=False):
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
        if not result and not empty_ok:
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

    def remove_selectors(self):
        """Remove only archived test selectors after recording a retry intent.

        Immutable generation, device, and operation records stay in place.
        This step does not retire the test LV or restore the original router.
        """
        with self.storage.lock():
            self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
            marker, metadata, original = self.window.context()
            if marker['phase'] != 'open' or self.window.located(original)['path'] != self.window.hold_path:
                raise TransactionError('cold test selector removal requires the held original and stopped test')
            self.window.verified_backup(metadata, original)
            if self.storage.pending() is not None:
                raise TransactionError('cold test selector removal refuses a pending router transaction')
            archive = self.verify()
            if archive['initial_operation'] != marker['initial_operation']:
                raise TransactionError('cold test selector archive belongs to another operation')
            for name, info in archive['files'].items():
                if name.startswith('operation/'):
                    source = self.storage.operation(marker['initial_operation']) / name.removeprefix('operation/')
                elif name in ('generation.json', 'device.json'):
                    source = (self.storage.base / 'records' /
                              (archive['generation_sha256'] + '.json' if name == 'generation.json' else
                               'device-' + archive['generation_sha256'] + '.json'))
                else:
                    continue
                if metadata_files.measure(source, 1024 * 1024) != info:
                    raise TransactionError('cold test retained record changed after archive: ' + name)
            intent_path = self.archive / 'selectors-removal.json'
            intent = generations.seal({'kind': 'klokast.router-cold-test-selector-removal.v1',
                'box': self.storage.box, 'operation_id': self.bundle.operation,
                'initial_operation': marker['initial_operation'],
                'archive_sha256': archive['record_sha256']})
            started = intent_path.exists() or intent_path.is_symlink()
            if started and records.read(intent_path) != intent:
                raise TransactionError('cold test selector removal intent changed')
            installation = self.storage.base / 'installation.json'
            accepted = self.storage.base / 'accepted.json'
            config = self.bundle.local('/etc/xen/router.cfg')
            link = self.bundle.local('/etc/xen/auto/router.cfg')
            for name, path in (('installation.json', installation), ('accepted.json', accepted)):
                expected = archive['files'].get(name)
                exists = path.exists() or path.is_symlink()
                if expected is None:
                    if exists:
                        raise TransactionError('cold test selector appeared outside its archive: ' + name)
                elif exists:
                    if metadata_files.measure(path, 1024 * 1024) != expected:
                        raise TransactionError('cold test selector changed after archive: ' + name)
                elif not started:
                    raise TransactionError('cold test selector disappeared before removal intent: ' + name)
            if archive['accepted_sha256'] is None:
                if any(path.exists() or path.is_symlink() for path in (config, link)):
                    raise TransactionError('unaccepted cold test has an unexpected router autostart selector')
            else:
                generation = generations.generation(records.read(self.archive / 'generation.json'),
                                                    self.storage.box)
                if config.exists() or config.is_symlink():
                    if records.secure(config).read_text() != generations.configuration(generation):
                        raise TransactionError('cold test Xen definition changed after acceptance')
                if link.exists() or link.is_symlink():
                    info = link.lstat()
                    if not stat.S_ISLNK(info.st_mode) or info.st_uid != records.ROOT_UID or \
                            os.readlink(link) != '../router.cfg':
                        raise TransactionError('cold test autostart link changed after acceptance')
            if not started:
                records.write(intent_path, intent)
            for path in (link, config, accepted, installation):
                if path.exists() or path.is_symlink():
                    path.unlink()
                    records.syncdir(path.parent)
            self.window.commit_xen()
            if any(path.exists() or path.is_symlink() for path in (link, config, accepted, installation)):
                raise TransactionError('cold test selectors remain after recorded removal')
            return intent

    def retire_disk(self):
        """Retire only the archived test LV after its selectors are removed."""
        with self.storage.lock():
            self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
            marker, metadata, original = self.window.context()
            if marker['phase'] != 'open' or self.window.located(original)['path'] != self.window.hold_path:
                raise TransactionError('cold test LV retirement requires the held original and stopped test')
            self.window.verified_backup(metadata, original)
            archive = self.verify()
            if archive['initial_operation'] != marker['initial_operation'] or self.storage.pending() is not None:
                raise TransactionError('cold test LV retirement differs from its open operation')
            intent_path = self.archive / 'selectors-removal.json'
            if not intent_path.exists() and not intent_path.is_symlink():
                raise TransactionError('cold test LV retirement requires completed exact selector removal')
            intent = records.read(intent_path)
            expected = generations.seal({'kind': 'klokast.router-cold-test-selector-removal.v1',
                'box': self.storage.box, 'operation_id': self.bundle.operation,
                'initial_operation': marker['initial_operation'],
                'archive_sha256': archive['record_sha256']})
            if intent != expected or any(path.exists() or path.is_symlink() for path in (
                    self.storage.base / 'installation.json', self.storage.base / 'accepted.json',
                    self.bundle.local('/etc/xen/router.cfg'), self.bundle.local('/etc/xen/auto/router.cfg'))):
                raise TransactionError('cold test LV retirement requires completed exact selector removal')
            operation = self.storage.operation(marker['initial_operation'])
            test = initial_installation.validate(records.read(self.archive / 'installation.json'),
                                                 self.storage.box)
            disk_path = operation / 'candidate-disk.json'
            if not disk_path.exists() and not disk_path.is_symlink():
                if test['stage'] != 'planned' or candidate_disks.observed(marker['initial_operation']) is not None:
                    raise TransactionError('cold test LV has no exact allocation record')
                status = 'never-allocated'
            else:
                disk = candidate_disks.record(operation, marker['initial_operation'])
                if (disk['path'] != test['disk']['path'] or
                        disk['uuid'] != test['disk']['uuid'] or
                        disk['stage'] not in ('planned', 'aborted', 'allocated', 'cloned', 'retiring', 'retired')):
                    raise TransactionError('cold test LV identity differs from archived installation')
                candidate_disks._retire_locked(operation, marker['initial_operation'],
                                               box=self.storage.box, inspected_uuid=None)
                final = candidate_disks.record(operation, marker['initial_operation'])
                if final['stage'] not in ('aborted', 'retired') or \
                        candidate_disks.observed(marker['initial_operation']) is not None:
                    raise TransactionError('cold test LV remains after exact retirement')
                status = final['stage']
            result = generations.seal({'kind': 'klokast.router-cold-test-disk-retirement.v1',
                'box': self.storage.box, 'operation_id': self.bundle.operation,
                'initial_operation': marker['initial_operation'],
                'archive_sha256': archive['record_sha256'], 'status': status})
            path = self.archive / 'disk-retirement.json'
            if path.exists() or path.is_symlink():
                if records.read(path) != result:
                    raise TransactionError('cold test disk retirement result changed')
            else:
                records.write(path, result)
            return result
