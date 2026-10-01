"""First allocation is fenced before native writes and cannot repeat enrollment."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_initial_preparation as initial
import router_records as records
from router_transaction import TransactionError
from test_router_updates import ENGINE, PROFILE, release
import test_router_candidate as candidate_fixture


class InitialPreparationTests(unittest.TestCase):
    def setUp(self):
        fixture = candidate_fixture.CandidateTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.job = copy.deepcopy(fixture.job)
        self.operation = self.job['operation_id']
        self.job['mode'] = 'initial-install'
        self.job['first_contact'] = {'key':'ssh-ed25519 YQ==', 'backend_address':'192.0.2.2',
                                     'backend_source_address':'192.0.2.1', 'backend_prefix':24}
        self.release = release()
        self.selection = initial.router_updates.seal({
            'kind':'klokast.router-bootstrap-input-selection.v1', 'engine_commit':ENGINE,
            'inputs_sha256':self.job['inputs_sha256'], 'replacement_authorized':False})
        self.request = {'kind':'klokast.router-initial-preparation.v1', 'box':'boxa',
            'mode':'initial-install', 'operation_id':self.operation, 'engine_commit':ENGINE,
            'inputs_sha256':self.job['inputs_sha256'], 'template_operation':'c'*24,
            'source_operation':'e'*24,
            'template_sha256':'d'*64, 'job_sha256':initial.generations.digest(self.job),
            'bootstrap':{name:{'bytes':1, 'sha256':'e'*64} for name in ('kernel', 'initramfs')},
            'selection_sha256':self.selection['receipt_sha256'],
            'release_sha256':self.release['receipt_sha256'], 'registry_sha256':'f'*64}
        self.grant = {'kind':'klokast.router-initial-preparation-grant.v1', 'engine_commit':ENGINE,
            'request_sha256':initial.generations.digest(self.request), 'granted_at':1000, 'expires_at':1900}
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.xen = self.base / 'xen'
        self.xen.mkdir()
        for name in ('records', 'generations', 'operations'):
            (self.base / name).mkdir(mode=0o700)
        self.work = self.base / 'operations' / self.operation
        self.work.mkdir(mode=0o700)
        for name, value in (('ROOT_UID', os.geteuid()), ('parents', lambda path:None)):
            context = patch.object(records, name, value)
            context.start(); self.addCleanup(context.stop)
        self.storage = records.Records('boxa', self.base)
        for name, value in (('request', self.request), ('candidate-job', self.job),
                ('release', self.release), ('profile', PROFILE), ('selection', self.selection),
                ('authorization', self.grant)):
            records.write(self.work / (name + '.json'), value)
        self.disk = {'path':initial.disks.selection(self.operation)[0], 'uuid':'exact-uuid', 'bytes':initial.disks.BYTES}
        component = {key:self.release['inputs']['tailscale'][key] for key in (
            'version', 'sha256', 'tailscale_sha256', 'tailscaled_sha256', 'openrc_sha256')}
        self.result = {'prepared':{'tailscale':component}, 'synthetic_native_result':True}
        self.clone = Mock(side_effect=self.allocate)
        self.prepare = Mock(return_value=self.result)
        for target, name, value in (
                (initial, 'template', Mock(return_value=(self.work / 'template', {'bytes':initial.disks.BYTES, 'sha256':'e'*64}))),
                (initial, 'bootstrap', Mock()),
                (initial, 'domain', Mock(return_value=None)),
                (initial.time, 'time', Mock(return_value=1001)),
                (initial.router_native, 'Native', Mock()),
                (initial.disks, 'inventory', Mock(return_value=[])),
                (initial.disks, 'initial_clone', self.clone),
                (initial.disks, 'verify', Mock(return_value=self.disk)),
                (initial.preparation, 'prepare', self.prepare)):
            context = patch.object(target, name, value)
            context.start(); self.addCleanup(context.stop)

    def allocate(self, work, operation, source, expected, storage):
        planned = storage.installation()
        self.assertEqual(planned['stage'], 'planned')
        self.assertIsNone(planned['disk']['uuid'])
        storage.record_installation(initial.generations.seal({
            **{key:value for key,value in planned.items() if key != 'record_sha256'},
            'stage':'allocated', 'disk':self.disk}))
        return self.disk

    def execute(self):
        return initial.execute(self.storage, self.operation, ENGINE, xen=self.xen)

    def test_fence_precedes_allocation_and_exact_prepared_retry_keeps_the_disk(self):
        result = self.execute()
        self.assertEqual(result['status'], 'initial-prepared')
        self.assertFalse(result['router_started'])
        self.assertEqual(result['installation']['disk'], self.disk)
        self.assertEqual(result['installation']['stage'], 'prepared')
        self.assertEqual(self.execute(), result)
        self.assertEqual(self.clone.call_count, 1)
        self.assertFalse((self.base / 'accepted.json').exists())

    def test_cold_window_refuses_another_reserved_template_before_allocation(self):
        cold_operation = 'f' * 24
        directory = self.base / 'cold-backups' / cold_operation
        directory.mkdir(parents=True, mode=0o700)
        pointer = initial.generations.seal({
            'kind': 'klokast.router-initial-provision-pointer.v1', 'box': 'boxa',
            **{key: self.request[key] for key in ('engine_commit', 'operation_id',
                'source_operation', 'selection_sha256', 'release_sha256')},
            'template_operation': 'f' * 24})
        records.write(directory / 'supervised-request.json', initial.generations.seal({
            'operation_id': cold_operation, 'initial_operation': self.operation,
            'initial_provision': pointer}))
        with patch.object(self.storage, 'cold_test', return_value={'operation_id': cold_operation}), \
             patch.object(self.storage, 'initial_window'):
            with self.assertRaisesRegex(TransactionError, 'approved template reservation'):
                self.execute()
        self.clone.assert_not_called()
        self.prepare.assert_not_called()
        self.assertIsNone(self.storage.installation())

    def test_failed_allocation_keeps_a_durable_legacy_provisioning_fence(self):
        self.clone.side_effect = RuntimeError('allocation interrupted')
        with self.assertRaisesRegex(RuntimeError, 'allocation interrupted'):
            self.execute()
        self.assertEqual(self.storage.installation()['stage'], 'planned')
        self.prepare.assert_not_called()
        self.clone.side_effect = self.allocate
        self.assertEqual(self.execute()['installation']['stage'], 'prepared')

    def test_failed_preparation_keeps_allocated_disk_for_exact_recovery(self):
        self.prepare.side_effect = RuntimeError('preparation interrupted')
        with self.assertRaisesRegex(RuntimeError, 'preparation interrupted'):
            self.execute()
        current = self.storage.installation()
        self.assertEqual(current['stage'], 'allocated')
        self.assertEqual(current['disk'], self.disk)
        self.prepare.side_effect = None
        self.clone.side_effect = lambda *args:self.disk
        self.assertEqual(self.execute()['installation']['stage'], 'prepared')

    def test_existing_legacy_or_unknown_generation_disks_are_not_fresh_targets(self):
        for path in ('/dev/vg0/lv_router', '/dev/vg0/routergen_'+'f'*24):
            with patch.object(initial.disks, 'inventory', return_value=[{'lv_path':path, 'lv_tags':''}]):
                with self.assertRaisesRegex(TransactionError, 'existing unassigned'):
                    self.execute()
        self.assertIsNone(self.storage.installation())
        self.clone.assert_not_called()

    def test_existing_config_or_router_domain_is_not_a_fresh_target(self):
        (self.xen / 'router.cfg').write_text('existing router')
        with self.assertRaisesRegex(TransactionError, 'configured router'):
            self.execute()
        (self.xen / 'router.cfg').unlink()
        with patch.object(initial, 'domain', return_value={'domid':4}):
            with self.assertRaisesRegex(TransactionError, 'running router'):
                self.execute()
        self.clone.assert_not_called()

    def test_expired_or_changed_grant_refuses_before_record_or_allocation(self):
        for changes in ({'expires_at':1001}, {'request_sha256':'0'*64}, {'engine_commit':'b'*40}):
            records.write(self.work / 'authorization.json', {**self.grant, **changes})
            with self.assertRaisesRegex(TransactionError, 'grant is stale'):
                self.execute()
            self.assertIsNone(self.storage.installation())
        self.clone.assert_not_called()

    def test_expiry_during_template_hashing_cannot_start_allocation(self):
        with patch.object(initial.time, 'time', side_effect=[1001, 1900]):
            with self.assertRaisesRegex(TransactionError, 'grant is stale'):
                self.execute()
        self.assertIsNone(self.storage.installation())
        self.clone.assert_not_called()

    def test_enrolled_disk_cannot_return_to_preparation(self):
        self.execute()
        current = self.storage.installation()
        self.storage.record_installation(initial.generations.seal({
            **{key:value for key,value in current.items() if key != 'record_sha256'},
            'stage':'enrolled', 'enrollment_sha256':'1'*64, 'machine_id':'machine_1'}))
        self.clone.reset_mock(); self.prepare.reset_mock()
        with self.assertRaisesRegex(TransactionError, 'repeat an enrolled'):
            self.execute()
        self.clone.assert_not_called(); self.prepare.assert_not_called()

    def test_changed_release_cannot_resume_the_same_installation(self):
        self.execute()
        changed = copy.deepcopy(self.release)
        changed['kernel_release'] = 'other-kernel'
        changed.pop('receipt_sha256')
        records.write(self.work / 'release.json', initial.router_updates.seal(changed))
        self.clone.reset_mock(); self.prepare.reset_mock()
        with self.assertRaisesRegex(TransactionError, 'selected release or job'):
            self.execute()
        self.clone.assert_not_called(); self.prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
