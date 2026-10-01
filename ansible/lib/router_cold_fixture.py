"""Native Inspector component proof on the state-copy test's synthetic disk.

Only fixture source selection is replaced. Guest configuration, paused checks,
slots, loop cleanup, abort, and inspection use the production Inspector. This
module is never installed as a router transaction action.
"""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from types import SimpleNamespace

import router_cold_filesystem as cold
import router_generations as generations
import router_records as records
from router_transaction import TransactionError

BASE = Path('/mnt/dom0_data/klokast-router-copy-tests')
BYTES = 64 * 1024 * 1024
PHASES = ('interrupted', 'complete')


class FixtureInspector(cold.Inspector):
    def __init__(self, root, phase):
        if phase not in PHASES:
            raise TransactionError('cold fixture phase is unsupported')
        self.root = Path(root)
        self.fixture = records.read(self.root / 'cold-fixture.json')
        generations.check_seal(self.fixture)
        value = self.fixture
        if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit',
                'source_sha256', 'source_inode', 'source_device', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-fixture.v1' or
                value['box'] != 'k001' or
                not generations.matches('[0-9a-f]{24}', value['operation_id']) or
                self.root != BASE / value['operation_id'] or
                not generations.matches('[0-9a-f]{40}', value['engine_commit']) or
                not generations.matches('[0-9a-f]{64}', value['source_sha256'])):
            raise TransactionError('cold fixture is not one exact private synthetic operation')
        storage = SimpleNamespace(box=value['box'], lock=nullcontext)
        bundle = SimpleNamespace(storage=storage, host=cold.native.Native(),
            directory=self.root / ('cold-' + phase), operation=value['operation_id'],
            engine=value['engine_commit'], verify=self.verify)
        super().__init__(bundle)
        self.backup = SimpleNamespace(record=bundle.directory / 'disk.json',
            validate=self.validate_disk, backup_disk=self.backup_disk)

    def verify(self):
        self.host.guard(self.storage.box, deadline=time.monotonic() + 30)
        source = self.root / 'original.slot'
        records.secure(source, maximum=BYTES)
        info = source.stat()
        if ((info.st_dev, info.st_ino, info.st_size) != (
                self.fixture['source_device'], self.fixture['source_inode'], BYTES) or
                cold.xen.checksum(source) != self.fixture['source_sha256']):
            raise TransactionError('cold fixture source identity or bytes changed')
        attached = cold.loops(source)
        if len(attached) != 1 or not re.fullmatch('/dev/loop[0-9]+', attached[0]):
            raise TransactionError('cold fixture source loop is ambiguous')
        self.host.device(attached[0])
        if Path('/sys/class/block/' + Path(attached[0]).name + '/ro').read_text().strip() != '1':
            raise TransactionError('cold fixture source loop is writable')
        return self.fixture, {'record_sha256': self.fixture['record_sha256'],
            'disk': {'path': attached[0], 'uuid': self.bundle.operation, 'bytes': BYTES}}

    def validate_disk(self, value, metadata, generation):
        expected = generations.seal({'kind': 'klokast.router-cold-fixture-disk.v1',
            'stage': 'copied', 'source_sha256': metadata['source_sha256'],
            'backup': generation['disk'], 'metadata_sha256': metadata['record_sha256']})
        if value != expected:
            raise TransactionError('cold fixture disk does not match its exact synthetic source')
        return value

    def backup_disk(self, identity):
        _, generation = self.verify()
        if identity != generation['disk']['uuid']:
            raise TransactionError('cold fixture source selects another identity')
        return generation['disk']

    def source(self):
        metadata, generation = self.verify()
        disk = self.validate_disk(records.read(self.backup.record), metadata, generation)
        backup = self.backup_disk(disk['backup']['uuid'])
        self.host.detached([backup['path']], deadline=time.monotonic() + 30)
        return metadata, generation, disk, backup

    def interrupt(self):
        metadata, _, disk, backup = self.source()
        capsule = self.capsule()
        job = self.job(capsule, metadata, disk)
        self.slot(self.work / 'result.slot')
        self.slot(self.work / 'job.slot', content=json.dumps(job).encode())
        result_loop = self.loop(self.work / 'result.slot', False)
        job_loop = self.loop(self.work / 'job.slot', True)
        cfg = self.work / 'guest.cfg'
        records.atomic(cfg, self.configuration(capsule, job, backup, result_loop, job_loop).encode())
        if cold.xen.domain(self.name) is not None:
            raise TransactionError('cold fixture guest already exists')
        cold.xen.run(['xl', 'create', '-p', str(cfg)])
        current = cold.xen.domain(self.name)
        cold.xen.require_identity(current, self.identity)
        self.paused(current, backup, result_loop, job_loop, capsule, job)
        records.write(self.bundle.directory / 'interrupted.json', {
            'operation_id': self.bundle.operation, 'uuid': self.identity, 'domid': current['domid']})
        # Simulate process loss without executing the guest or running finally.
        os._exit(73)


def qualify(work, request):
    work = Path(work)
    capsule = records.read(work / 'cold-bootstrap.json')
    if request.get('cold_bootstrap_sha256') != generations.digest(capsule):
        raise TransactionError('cold fixture boot capsule differs from its request')
    source = work / 'original.slot'
    records.secure(source, maximum=BYTES)
    info = source.stat()
    if info.st_size != BYTES or cold.loops(source):
        raise TransactionError('cold fixture requires one detached fixed-size synthetic source')
    records.write(work / 'cold-fixture.json', generations.seal({
        'kind': 'klokast.router-cold-fixture.v1', 'box': request['box'],
        'operation_id': request['operation_id'], 'engine_commit': request['engine_commit'],
        'source_sha256': cold.xen.checksum(source), 'source_inode': info.st_ino,
        'source_device': info.st_dev}))
    loop = cold.xen.attach_loop(source, readonly=True)
    inspectors = []
    try:
        for phase in PHASES:
            directory = work / ('cold-' + phase)
            directory.mkdir(mode=0o700)
            (directory / 'filesystem').mkdir(mode=0o700)
            records.write(directory / 'filesystem-bootstrap.json', capsule)
            for name in ('kernel', 'initramfs'):
                shutil.copyfile(work / ('cold-' + name), directory / 'filesystem' / ('bootstrap-' + name))
                (directory / 'filesystem' / ('bootstrap-' + name)).chmod(0o600)
            inspector = FixtureInspector(work, phase)
            inspectors.append(inspector)
            metadata, generation = inspector.verify()
            records.write(inspector.backup.record, generations.seal({
                'kind': 'klokast.router-cold-fixture-disk.v1', 'stage': 'copied',
                'source_sha256': metadata['source_sha256'], 'backup': generation['disk'],
                'metadata_sha256': metadata['record_sha256']}))
            if phase == 'interrupted':
                child = subprocess.run(['/usr/bin/python3', str(Path(__file__).resolve()),
                    '--box', request['box'], '--operation-id', request['operation_id']],
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
                if child.returncode != 73 or not (directory / 'interrupted.json').is_file():
                    raise TransactionError('cold fixture worker did not reach its exact interruption point')
                if inspector.abort() != 'destroyed' or inspector.abort() != 'detached':
                    raise TransactionError('cold fixture interrupted guest cleanup is incomplete')
            else:
                proof = inspector.run()
                if proof.get('readonly') is not True or proof.get('root_verified') is not True:
                    raise TransactionError('cold fixture did not prove the read-only filesystem')
        result = {'kind': 'klokast.router-cold-fixture-result.v1',
            'operation_id': request['operation_id'], 'interrupted_worker': True,
            'exact_guest_fenced': True, 'loop_cleanup_retried': True,
            'readonly': True, 'root_verified': True, 'synthetic_only': True}
        records.write(work / 'cold-result.json', result)
        return result
    finally:
        for inspector in inspectors:
            inspector.abort()
            for name in ('result.slot', 'job.slot'):
                path = inspector.work / name
                if path.exists() or path.is_symlink():
                    records.secure(path, maximum=cold.MIB)
                    if path.stat().st_size != cold.MIB or cold.loops(path):
                        raise TransactionError('cold fixture slot remains attached or differs')
                    path.unlink()
        cold.xen.detach_loop(source, loop)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--box', required=True)
    parser.add_argument('--operation-id', required=True)
    args = parser.parse_args()
    if args.box != 'k001' or not generations.matches('[0-9a-f]{24}', args.operation_id):
        raise TransactionError('cold fixture child requires one exact K001 operation')
    FixtureInspector(BASE / args.operation_id, 'interrupted').interrupt()


if __name__ == '__main__':
    main()
