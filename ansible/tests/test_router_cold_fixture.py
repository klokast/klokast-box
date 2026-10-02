"""The native cold proof selects only its exact synthetic read-only disk."""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_fixture as fixture
import router_generations as generations
import router_records as records
from router_transaction import TransactionError


class LostWorker(BaseException):
    pass


class ColdFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.operation = 'a' * 24
        self.root = self.base / self.operation
        self.root.mkdir(mode=0o700)
        self.source = self.root / 'original.slot'
        self.source.write_bytes(bytes(4096))
        self.source.chmod(0o600)
        info = self.source.stat()
        self.value = generations.seal({'kind': 'klokast.router-cold-fixture.v1',
            'box': 'k001', 'operation_id': self.operation, 'engine_commit': 'b' * 40,
            'source_sha256': fixture.cold.xen.checksum(self.source),
            'source_inode': info.st_ino, 'source_device': info.st_dev})
        for target, name, value in ((fixture, 'BASE', self.base), (fixture, 'BYTES', 4096),
                (records, 'ROOT_UID', os.geteuid()), (records, 'parents', lambda path: None)):
            context = patch.object(target, name, value)
            context.start(); self.addCleanup(context.stop)
        records.write(self.root / 'cold-fixture.json', self.value)
        self.inspector = fixture.FixtureInspector(self.root, 'interrupted')
        self.inspector.host = Mock()
        self.inspector.work.mkdir(parents=True, mode=0o700)

    def test_exact_source_requires_one_readonly_loop_and_unchanged_bytes(self):
        with patch.object(fixture.cold, 'loops', return_value=['/dev/loop7']), \
             patch.object(Path, 'read_text', return_value='1\n'):
            _, generation = self.inspector.verify()
            self.assertEqual(generation['disk']['path'], '/dev/loop7')
            self.assertEqual(generation['disk']['bytes'], 4096)
        self.source.write_bytes(b'x' * 4096)
        with self.assertRaisesRegex(TransactionError, 'identity or bytes changed'):
            self.inspector.verify()

    def test_writable_or_ambiguous_source_is_never_adopted(self):
        with patch.object(fixture.cold, 'loops', return_value=['/dev/loop7']), \
             patch.object(Path, 'read_text', return_value='0\n'):
            with self.assertRaisesRegex(TransactionError, 'writable'):
                self.inspector.verify()
        with patch.object(fixture.cold, 'loops', return_value=['/dev/loop7', '/dev/loop8']):
            with self.assertRaisesRegex(TransactionError, 'ambiguous'):
                self.inspector.verify()

    def test_foreign_disk_path_and_operation_refuse(self):
        generation = {'disk': {'path': '/dev/loop7', 'uuid': self.operation, 'bytes': 4096}}
        disk = generations.seal({'kind': 'klokast.router-cold-fixture-disk.v1',
            'stage': 'copied', 'source_sha256': self.value['source_sha256'],
            'backup': {**generation['disk'], 'path': '/dev/vg0/lv_router'},
            'metadata_sha256': self.value['record_sha256']})
        with self.assertRaisesRegex(TransactionError, 'exact synthetic source'):
            self.inspector.validate_disk(disk, self.value, generation)
        changed = {key: value for key, value in self.value.items() if key != 'record_sha256'}
        records.write(self.root / 'cold-fixture.json', generations.seal({**changed, 'operation_id': 'c' * 24}))
        with self.assertRaisesRegex(TransactionError, 'exact private synthetic operation'):
            fixture.FixtureInspector(self.root, 'complete')

    def test_worker_exits_only_after_exact_paused_guest_is_verified(self):
        capsule = generations.seal({'inputs_sha256': 'c' * 64})
        backup = {'path': '/dev/loop7', 'uuid': self.operation, 'bytes': 4096}
        disk = {'record_sha256': 'd' * 64, 'backup': backup}
        self.inspector.source = Mock(return_value=(self.value, {}, disk, backup))
        self.inspector.capsule = Mock(return_value=capsule)
        self.inspector.loop = Mock(side_effect=['/dev/loop8', '/dev/loop9'])
        job = self.inspector.job(capsule, self.value, disk)
        config = ast.parse(self.inspector.configuration(capsule, job, backup, '/dev/loop8', '/dev/loop9'))
        expected = {item.targets[0].id: ast.literal_eval(item.value) for item in config.body}
        current = {'domid': 17, 'config': {'c_info': {'uuid': self.inspector.identity}}}
        with patch.object(fixture.cold.xen, 'domain', side_effect=[None, current]), \
             patch.object(fixture.cold.xen, 'run') as run, \
             patch.object(self.inspector, 'paused') as paused, \
             patch.object(fixture.os, '_exit', side_effect=LostWorker) as exit_worker:
            with self.assertRaises(LostWorker):
                self.inspector.interrupt()
        self.assertEqual(expected['vif'], [])
        self.assertEqual(expected['disk'][0], 'phy:/dev/loop7,xvda,r')
        paused.assert_called_once()
        self.assertEqual(run.call_args.args[0][:3], ['xl', 'create', '-p'])
        exit_worker.assert_called_once_with(73)
        self.assertEqual(records.read(self.inspector.bundle.directory / 'interrupted.json')['domid'], 17)

    def test_wrong_capsule_refuses_before_any_disk_attachment(self):
        records.write(self.root / 'cold-bootstrap.json', {'kind': 'wrong'})
        with patch.object(fixture.cold.xen, 'attach_loop') as attach:
            with self.assertRaisesRegex(TransactionError, 'capsule differs'):
                fixture.qualify(self.root, {'cold_bootstrap_sha256': 'e' * 64})
        attach.assert_not_called()


if __name__ == '__main__':
    unittest.main()
