"""Only an exact stopped, detached guest may publish cold recovery proof."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_filesystem as cold
import xen_build_runtime as xen
import router_cold_window
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_cold_backup as fixtures
import test_router_cold_window as window_fixtures


class InspectorTests(unittest.TestCase):
    setUp = fixtures.ColdBundleTests.setUp
    capture = fixtures.ColdBundleTests.capture

    def prepare(self):
        self.capture()
        self.host.device = lambda path: path
        self.window = router_cold_window.Window(self.bundle)
        window_fixtures.WindowTests.stage_proofs(self)
        window_fixtures.WindowTests.stage_capsule(self)
        self.disk = self.window.backup.save({'kind': 'klokast.router-cold-disk.v1', 'box': 'boxa',
            'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
            'metadata_sha256': self.bundle.verify()[0]['record_sha256'], 'source': self.generation['disk'],
            'backup': {'path': self.window.backup.path, 'uuid': 'backup-uuid', 'bytes': 2147483648},
            'stage': 'allocated', 'source_sha256': None})
        change = mock.patch.object(cold.disks.disks, 'inventory', return_value=[
            {'lv_path': self.window.backup.path, 'lv_uuid': 'backup-uuid',
             'lv_size': '2147483648', 'lv_attr': '-wi-a-----', 'origin': '',
             'lv_tags': self.window.backup.tag}])
        change.start(); self.addCleanup(change.stop)
        with mock.patch.object(cold.native, 'command', return_value=''):
            self.window.arm('b'*24, int(cold.time.time()) + 3600)
        self.disk = self.window.backup.save(self.disk, stage='copied', source_sha256='d'*64)
        self.host.guest = mock.Mock(return_value=None)
        change = mock.patch.object(cold.xen, 'checksum', return_value='d'*64)
        self.source_checksum = change.start(); self.addCleanup(change.stop)
        change = mock.patch.object(cold.xen, 'domain', return_value=None)
        change.start(); self.addCleanup(change.stop)
        self.loop_map = {}
        change = mock.patch.object(cold, 'loops', side_effect=lambda path: self.loop_map.get(str(path), []))
        change.start(); self.addCleanup(change.stop)
        def command(argv, *args, **kwargs):
            if argv[:2] == ['/sbin/losetup', '-d']:
                for path, devices in self.loop_map.items():
                    if argv[2] in devices:
                        self.loop_map[path] = []
            elif argv[0] == '/sbin/losetup':
                self.loop_map[str(argv[-1])] = ['/dev/loop' + str(len(self.loop_map) + 10)]
        change = mock.patch.object(cold.native, 'command', side_effect=command)
        self.commands = change.start(); self.addCleanup(change.stop)

    def paused_record(self, job):
        result, request = self.loop_map[str(self.inspector.work / 'result.slot')][0], \
                          self.loop_map[str(self.inspector.work / 'job.slot')][0]
        config = {'c_info': {'name': self.inspector.name, 'uuid': self.inspector.identity,
                             'type': 'pvh'},
                  'b_info': {'kernel': str(self.inspector.work / 'bootstrap-kernel'),
                             'ramdisk': str(self.inspector.work / 'bootstrap-initramfs'),
                             'cmdline': ('console=hvc0 panic=1 klokast_operation=' + self.bundle.operation +
                                 ' klokast_inputs=' + self.capsule['inputs_sha256'] +
                                 ' klokast_job=' + generations.digest(job)),
                             'target_memkb': 768 * 1024, 'max_vcpus': 1},
                  'disks': [{'vdev': vdev, 'pdev_path': path, 'readwrite': mode, 'format': 'raw'}
                            for vdev, path, mode in (
                                ('xvda', self.window.backup.path, 0),
                                ('xvdb', result, 1), ('xvdc', request, 0))],
                  'nics': []}
        return {'domid': 5, 'config': config}

    def boot(self, work, name, identity, config, job, **kwargs):
        self.assertEqual(name, self.inspector.name)
        self.assertEqual(identity, self.inspector.identity)
        self.assertEqual(kwargs['timeout'], 420)
        self.assertEqual(kwargs['kind'], 'klokast.router-cold-filesystem-result.v1')
        kwargs['validate_paused'](self.paused_record(job))
        result = {'kind': 'klokast.router-cold-filesystem-result.v1',
                  'operation_id': job['operation_id'], 'inputs_sha256': job['inputs_sha256'],
                  'job_sha256': generations.digest(job), 'success': True,
                  'readonly': True, 'root_verified': True}
        (work / 'result.slot').write_bytes((json.dumps(result) + '\0').encode())

    def test_success_requires_stopped_guest_and_exact_readonly_attachment(self):
        self.prepare()
        with mock.patch.object(cold.xen, 'boot_guest', side_effect=self.boot) as boot:
            result = self.inspector.run()
        self.assertEqual(result['disk_sha256'], self.disk['record_sha256'])
        self.assertEqual(records.read(self.bundle.directory / 'filesystem.json'), result)
        self.assertEqual(boot.call_count, 1)
        self.assertEqual(self.loop_map[str(self.inspector.work / 'job.slot')], [])
        self.assertEqual(self.loop_map[str(self.inspector.work / 'result.slot')], [])
        with mock.patch.object(cold.xen, 'boot_guest') as second:
            with self.assertRaisesRegex(TransactionError, 'already started'):
                self.inspector.run()
            second.assert_not_called()

    def test_paused_guest_rejects_nic_and_writable_source(self):
        self.prepare()
        job = self.inspector.job(self.capsule, self.bundle.verify()[0], self.disk)
        self.loop_map[str(self.inspector.work / 'result.slot')] = ['/dev/loop10']
        self.loop_map[str(self.inspector.work / 'job.slot')] = ['/dev/loop11']
        record = self.paused_record(job)
        backup = self.disk['backup']
        for change in ('nic', 'writable', 'wrong-source'):
            modified = json.loads(json.dumps(record))
            if change == 'nic':
                modified['config']['nics'] = [{'bridge': 'br-wan'}]
            elif change == 'writable':
                modified['config']['disks'][0]['readwrite'] = 1
            else:
                modified['config']['disks'][0]['pdev_path'] = '/dev/vg0/lv_router'
            with self.subTest(change=change), self.assertRaisesRegex(TransactionError, 'networkless read-only'):
                self.inspector.paused(modified, backup, '/dev/loop10', '/dev/loop11', self.capsule, job)

    def test_native_xl_omits_zero_modes_and_empty_nics(self):
        record = self.interrupted_guest()
        del record['config']['nics']
        for disk in record['config']['disks']:
            if disk['readwrite'] == 0:
                del disk['readwrite']
        job = self.inspector.job(self.capsule, self.bundle.verify()[0], self.disk)
        self.inspector.paused(record, self.disk['backup'], '/dev/loop10',
                              '/dev/loop11', self.capsule, job)
        with mock.patch.object(cold.xen, 'domain', side_effect=[record, None]), \
             mock.patch.object(cold.xen, 'run') as run:
            self.assertEqual(self.inspector.abort(), 'destroyed')
        run.assert_called_once_with(['xl', 'destroy', '5'])

    def test_running_source_or_changed_raw_hash_refuses_before_guest_creation(self):
        self.prepare()
        self.host.guest.return_value = ('accepted', {})
        with mock.patch.object(cold.xen, 'boot_guest') as boot:
            with self.assertRaisesRegex(TransactionError, 'router stopped'):
                self.inspector.run()
            boot.assert_not_called()
        self.host.guest.return_value = None
        self.source_checksum.return_value = 'f'*64
        with mock.patch.object(cold.xen, 'boot_guest') as boot:
            with self.assertRaisesRegex(TransactionError, 'changed after raw copying'):
                self.inspector.run()
            boot.assert_not_called()
        self.assertFalse((self.inspector.work / 'result.slot').exists())

    def test_failed_guest_keeps_slots_and_publishes_no_proof(self):
        self.prepare()
        with mock.patch.object(cold.xen, 'boot_guest', side_effect=RuntimeError('guest failed')):
            with self.assertRaisesRegex(RuntimeError, 'guest failed'):
                self.inspector.run()
        self.assertFalse((self.bundle.directory / 'filesystem.json').exists())
        self.assertTrue((self.inspector.work / 'result.slot').exists())
        with mock.patch.object(cold.xen, 'boot_guest') as boot:
            with self.assertRaisesRegex(TransactionError, 'already started'):
                self.inspector.run()
            boot.assert_not_called()

    def interrupted_guest(self):
        self.prepare()
        job = self.inspector.job(self.capsule, self.bundle.verify()[0], self.disk)
        result_slot = self.inspector.work / 'result.slot'
        job_slot = self.inspector.work / 'job.slot'
        self.inspector.slot(result_slot)
        self.inspector.slot(job_slot, content=(json.dumps(job) + '\n').encode())
        self.loop_map[str(result_slot)] = ['/dev/loop10']
        self.loop_map[str(job_slot)] = ['/dev/loop11']
        return self.paused_record(job)

    def test_interrupted_inspector_is_fenced_by_exact_domain_and_slots(self):
        record = self.interrupted_guest()
        with mock.patch.object(cold.xen, 'domain', side_effect=[record, None]), \
             mock.patch.object(cold.xen, 'run') as run:
            self.assertEqual(self.inspector.abort(), 'destroyed')
        run.assert_called_once_with(['xl', 'destroy', '5'])
        self.assertEqual(self.loop_map[str(self.inspector.work / 'result.slot')], [])
        self.assertEqual(self.loop_map[str(self.inspector.work / 'job.slot')], [])

    def test_interrupted_inspector_with_network_is_not_destroyed_as_ours(self):
        record = self.interrupted_guest()
        record['config']['nics'] = [{'bridge': 'br-wan'}]
        with mock.patch.object(cold.xen, 'domain', return_value=record), \
             mock.patch.object(cold.xen, 'run') as run:
            with self.assertRaisesRegex(TransactionError, 'networkless read-only'):
                self.inspector.abort()
        run.assert_not_called()


class TransportTests(unittest.TestCase):
    def test_invalid_paused_guest_is_destroyed_before_unpause(self):
        record = {'domid': 17, 'config': {'c_info': {'uuid': 'a'*8 + '-1111-4111-8111-111111111111'}}}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            with mock.patch.object(xen, 'run') as run, \
                 mock.patch.object(xen, 'domain', side_effect=[record, record, None]) as domain:
                with self.assertRaisesRegex(RuntimeError, 'unexpected writable source'):
                    xen.boot_guest(work, 'cold-test', record['config']['c_info']['uuid'],
                                   work / 'guest.cfg', {'operation_id': 'a'*24, 'inputs_sha256': 'b'*64},
                                   validate_paused=lambda value: (_ for _ in ()).throw(
                                       RuntimeError('unexpected writable source')))
            self.assertEqual([call.args[0][:2] for call in run.call_args_list],
                             [['xl', 'create'], ['xl', 'destroy']])
            self.assertEqual(domain.call_count, 3)


if __name__ == '__main__':
    unittest.main()
