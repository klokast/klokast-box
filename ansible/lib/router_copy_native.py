"""Opaque dom0 transport for the fixed offline router state-copy guest.

Dom0 never mounts either router disk. Scratch files are preallocated before
cutover and retained on uncertain completion. Private receipts stay on box.
"""
import json
import os
from pathlib import Path
import re
import time

import router_copy_contract as contract
import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import TransactionError

MIB = 1024 * 1024
SLOTS = {'scratch.slot': 2048 * MIB,
         **{phase + '.' + kind + '.slot': MIB for phase in ('forward', 'reverse') for kind in ('private', 'result')}}


def phase_name(source, target):
    if (source, target) not in (('old', 'candidate'), ('candidate', 'old')):
        raise TransactionError('router state copy requires a fixed direction between its recorded pair')
    return 'forward' if source == 'old' else 'reverse'


def loops(path):
    return sorted('/dev/' + item.parents[1].name for item in Path('/sys/block').glob('loop*/loop/backing_file')
                  if item.read_text().strip().lstrip('/') == str(path).lstrip('/'))


class Copy:
    def inputs(self, adapter):
        value = contract.capsule(records.read(adapter.work / 'capsule.json'), adapter.request,
                                 adapter.pair['old'], adapter.pair['candidate'])
        return value, contract.job(adapter.request, adapter.pair['old'], adapter.pair['candidate'], value['inputs_sha256'])

    def prepare(self, adapter, *, deadline):
        """Allocate only new opaque regular files before an operation can arm."""
        directory = records.secure(adapter.work / 'copy', directory=True)
        capsule, _ = self.inputs(adapter)
        if (directory / 'allocation.json').exists() or any((directory / name).exists() for name in SLOTS):
            raise TransactionError('router copy allocation already exists; inspect its exact files before retrying')
        rows = {}
        for name, size in SLOTS.items():
            if adapter.monotonic() >= deadline:
                raise TransactionError('router copy allocation deadline expired')
            path = directory / name
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'w+b') as stream:
                os.posix_fallocate(stream.fileno(), 0, size)
                os.fsync(stream.fileno())
                info = os.fstat(stream.fileno())
            rows[name] = {'device': info.st_dev, 'inode': info.st_ino, 'bytes': size}
        records.write(directory / 'allocation.json', {'kind': 'klokast.router-copy-allocation.v1',
            'transaction_sha256': generations.digest(adapter.request), 'files': rows})
        self.verify(adapter, deadline=deadline)

    def slots(self, adapter):
        directory = records.secure(adapter.work / 'copy', directory=True)
        value = records.read(directory / 'allocation.json')
        if (not isinstance(value, dict) or set(value) != {'kind', 'transaction_sha256', 'files'} or
                value['kind'] != 'klokast.router-copy-allocation.v1' or
                value['transaction_sha256'] != generations.digest(adapter.request) or
                not isinstance(value['files'], dict) or set(value['files']) != set(SLOTS)):
            raise TransactionError('router copy allocation differs from its exact operation')
        for name, size in SLOTS.items():
            path = records.secure(directory / name, maximum=size)
            info = path.stat()
            if (value['files'][name] != {'device': info.st_dev, 'inode': info.st_ino, 'bytes': size} or
                    info.st_size != size or info.st_mode & 0o077):
                raise TransactionError('router copy scratch file identity, size, or private mode changed')
        return directory

    def verify(self, adapter, *, deadline):
        capsule, _ = self.inputs(adapter)
        directory = self.slots(adapter)
        for name, value in capsule['bootstrap'].items():
            adapter.host.artifact({'path': str(directory / name), **value}, deadline=deadline)

    def mappings(self, adapter, phase):
        directory = self.slots(adapter)
        source, target = ('old', 'candidate') if phase == 'forward' else ('candidate', 'old')
        result = [(adapter.pair[source]['disk']['path'], 'xvda', 0),
                  (adapter.pair[target]['disk']['path'], 'xvdb', 1)]
        for name, vdev in (('scratch.slot', 'xvdc'), (phase + '.private.slot', 'xvdd'), (phase + '.result.slot', 'xvde')):
            devices = loops(directory / name)
            if len(devices) != 1 or not re.fullmatch('/dev/loop[0-9]+', devices[0]):
                raise TransactionError('router copy scratch loop attachment is absent or ambiguous')
            result.append((devices[0], vdev, 1))
        return result

    def helper(self, adapter, phase, *, deadline):
        capsule, _ = self.inputs(adapter)
        name = 'router-copy-' + adapter.request['operation_id'] + '-' + phase
        identity = capsule['domains'][phase]
        found = [v for v in adapter.host.inventory(deadline=deadline) if v['domid'] > 0 and
                 (v['config']['c_info']['name'] == name or v['config']['c_info']['uuid'] == identity)]
        if not found:
            return None
        if len(found) != 1:
            raise TransactionError('router copy guest identity is ambiguous')
        value = found[0]
        config = value['config']
        info, boot = config['c_info'], config['b_info']
        actual = [(adapter.host.device(d['pdev_path']), d['vdev'], d['readwrite']) for d in config['disks']]
        expected = [(adapter.host.device(path), vdev, mode) for path, vdev, mode in self.mappings(adapter, phase)]
        if (value['domid'] <= 0 or info['name'] != name or info['uuid'] != identity or info['type'] != 'pvh' or
                config['nics'] or actual != expected or any(d['format'] != 'raw' for d in config['disks']) or
                boot['kernel'] != str(adapter.work / 'copy/kernel') or
                boot['ramdisk'] != str(adapter.work / 'copy/initramfs') or boot['cmdline'] != self.extra(adapter, phase)):
            raise TransactionError('router copy guest differs from its fixed networkless capsule and disk assignments')
        return value

    def fence(self, adapter, *, deadline):
        directory = self.slots(adapter)
        capsule, _ = self.inputs(adapter)
        for phase in ('forward', 'reverse'):
            current = self.helper(adapter, phase, deadline=deadline)
            if current is not None:
                native.command(['/usr/sbin/xl', 'destroy', capsule['domains'][phase]], deadline)
                if self.helper(adapter, phase, deadline=deadline) is not None:
                    raise TransactionError('router copy guest remains; retain all disks and loop attachments')
        for name in SLOTS:
            path = directory / name
            devices = loops(path)
            if len(devices) > 1:
                raise TransactionError('router copy scratch file has ambiguous loop attachments')
            if devices:
                adapter.host.wait_detached(devices, deadline=deadline)
                native.command(['/sbin/losetup', '-d', devices[0]], deadline)
                if loops(path):
                    raise TransactionError('router copy scratch file remains attached; retain it')

    def extra(self, adapter, phase):
        capsule, _ = self.inputs(adapter)
        return ('console=hvc0 klokast_operation=' + adapter.request['operation_id'] +
                ' klokast_inputs=' + capsule['inputs_sha256'] + ' klokast_job=' + capsule['job_sha256'] +
                ' klokast_phase=' + phase)

    def read_slot(self, path):
        with path.open('rb') as stream:
            return json.loads(stream.read(MIB).split(b'\0', 1)[0], object_pairs_hook=records.unique)

    def result(self, adapter, phase):
        capsule, _ = self.inputs(adapter)
        value = self.read_slot(adapter.work / 'copy' / (phase + '.result.slot'))
        if (not isinstance(value, dict) or value.get('kind') != 'klokast.router-copy-phase.v1' or
                value.get('phase') != phase or value.get('operation_id') != adapter.request['operation_id'] or
                value.get('inputs_sha256') != capsule['inputs_sha256'] or value.get('job_sha256') != capsule['job_sha256'] or
                type(value.get('success')) is not bool):
            raise TransactionError('router copy result differs from this capsule, operation, or direction')
        return value

    def copy(self, adapter, source, target, *, deadline):
        phase = phase_name(source, target)
        started = adapter.monotonic()
        self.verify(adapter, deadline=deadline)
        self.fence(adapter, deadline=deadline)
        if adapter.host.guest(adapter.pair, deadline=deadline) is not None:
            raise TransactionError('both router generations must be stopped before state copying')
        adapter.host.detached([v['disk']['path'] for v in adapter.pair.values()], deadline=deadline)
        capsule, _ = self.inputs(adapter)
        directory = self.slots(adapter)
        # Leave a stop reserve within the caller's budget. A copy timeout never
        # borrows the separate full recovery budget given by the transaction.
        copy_deadline = deadline - 10
        if copy_deadline <= adapter.monotonic():
            raise TransactionError('router copy has no time left for its bounded guest and stop reserve')
        for kind in ('private', 'result'):
            with (directory / (phase + '.' + kind + '.slot')).open('r+b', buffering=0) as stream:
                stream.write(b'\0' * MIB)
                os.fsync(stream.fileno())
        try:
            for name in ('scratch.slot', phase + '.private.slot', phase + '.result.slot'):
                native.command(['/sbin/losetup', '-f', directory / name], copy_deadline)
                if len(loops(directory / name)) != 1:
                    raise TransactionError('router copy scratch loop allocation failed')
            name = 'router-copy-' + adapter.request['operation_id'] + '-' + phase
            config = {'name': name, 'uuid': capsule['domains'][phase], 'type': 'pvh', 'memory': 768, 'vcpus': 1,
                'kernel': str(directory / 'kernel'), 'ramdisk': str(directory / 'initramfs'),
                'extra': self.extra(adapter, phase), 'vif': [], 'on_poweroff': 'destroy', 'on_crash': 'destroy',
                'on_reboot': 'destroy', 'disk': ['phy:' + path + ',' + vdev + ',' + ('w' if mode else 'r')
                                                for path, vdev, mode in self.mappings(adapter, phase)]}
            path = directory / (phase + '.cfg')
            records.atomic(path, ('\n'.join(k + ' = ' + repr(v) for k, v in config.items()) + '\n').encode())
            native.command(['/usr/sbin/xl', 'create', '-p', path], copy_deadline)
            if self.helper(adapter, phase, deadline=copy_deadline) is None:
                raise TransactionError('router copy guest did not start paused with its exact identity')
            native.command(['/usr/sbin/xl', 'unpause', capsule['domains'][phase]], copy_deadline)
            while adapter.monotonic() < copy_deadline:
                try:
                    self.result(adapter, phase)
                    break
                except (ValueError, UnicodeError, TransactionError):
                    if self.helper(adapter, phase, deadline=copy_deadline) is None:
                        raise TransactionError('router copy guest stopped without a complete result') from None
                time.sleep(min(0.5, max(0, copy_deadline - adapter.monotonic())))
            else:
                raise TransactionError('router state-copy guest exceeded its fixed deadline')
        finally:
            self.fence(adapter, deadline=deadline)
        self.verify_copy(adapter, source, target, deadline=deadline)
        records.write(directory / (phase + '.timing.json'), {'kind': 'klokast.router-copy-timing.v1',
            'transaction_sha256': generations.digest(adapter.request), 'phase': phase,
            'host_seconds': round(adapter.monotonic() - started, 3), 'complete': True})

    def verify_copy(self, adapter, source, target, *, deadline):
        phase = phase_name(source, target)
        adapter.host.detached([v['disk']['path'] for v in adapter.pair.values()], deadline=deadline)
        value = self.result(adapter, phase)
        if value['success'] is not True:
            raise TransactionError('router state copy failed; destination remains fenced')
        _, job = self.inputs(adapter)
        receipt = self.read_slot(adapter.work / 'copy' / (phase + '.private.slot'))
        expected = job[phase]
        if (not isinstance(receipt, dict) or receipt.get('kind') != 'klokast.router-state-copy.v1' or
                receipt.get('complete') is not True or receipt.get('operation') != adapter.request['operation_id'] or
                receipt.get('request_sha256') != expected['request_sha256'] or
                receipt.get('source') != expected['source_id'] or receipt.get('destination') != expected['destination_id'] or
                receipt.get('receipt_sha256') != value.get('copy_receipt_sha256') or
                receipt.get('receipt_sha256') != generations.digest({k:v for k,v in receipt.items() if k != 'receipt_sha256'})):
            raise TransactionError('router state copy lacks its exact complete private receipt')
