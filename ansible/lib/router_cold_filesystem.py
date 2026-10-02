"""Inspect a cold backup in a fixed networkless read-only Xen guest.

The active controller stages a versioned boot capsule before the outage.
This module runs only after the original router stops and its raw copy passes.
No guest filesystem is mounted on dom0. An uncertain result stays for exact
reconciliation; a second guest cannot silently reuse the operation.
"""
import hashlib
import json
import logging
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
        'loop*/loop/backing_file') if item.read_text().strip().removesuffix(' (deleted)').lstrip('/') == str(path).lstrip('/'))


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
        return value

    def capsule(self):
        value=self.capsule_record()
        for name,expected in value['boot'].items():
            self.host.artifact({'path':str(self.work/('bootstrap-'+name)),**expected},deadline=time.monotonic()+90)
        return value

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
