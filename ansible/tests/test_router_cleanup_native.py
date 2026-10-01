"""Native retirement retries reconcile exact intent without touching retained disks."""
import copy
import hashlib
from pathlib import Path
import time
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import unittest
from unittest import mock

import router_executor as executor
import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import TransactionError
import test_router_cleanup_plan as plan_tests


class Host:
    monotonic = staticmethod(time.monotonic)
    artifact = native.Native.artifact

    def __init__(self, row):
        self.row, self.attached, self.live = row, False, '0'

    def guest(self, kept, **kwargs):
        return (self.live, {}) if self.live else None

    def disk(self, disk, **kwargs):
        if self.row is None or self.row['lv_uuid'] != disk['uuid']:
            raise TransactionError('LV UUID changed')

    def detached(self, paths, **kwargs):
        if self.attached:
            raise TransactionError('live Xen backend')


class NativeCleanupTests(unittest.TestCase):
    def setUp(self):
        plan_tests.CleanupPlanTests.setUp(self)
        self.complete('rolled-back')
        self.copy.retire = mock.Mock(return_value={'record_sha256': 'e' * 64})
        patch = mock.patch.object(executor, 'cache_copy_proof', return_value={'record_sha256': 'd' * 64})
        patch.start(); self.addCleanup(patch.stop)
        self.selected = executor.cleanup_plan(self.records, self.request['operation_id'], self.request['engine_commit'])
        # Plan selection/schema have separate real-record tests. Substitute
        # temporary artifact paths only in this trusted native-plan fixture.
        target = copy.deepcopy(self.selected['retire'][0]['generation'])
        for name, item in target['boot'].items():
            path = self.base / name
            path.write_bytes((name + '-verified').encode())
            path.chmod(0o600)
            item.update(path=str(path), bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        target.pop('record_sha256')
        self.target = generations.seal(target)
        self.selected['retire'][0]['generation'] = self.target
        self.selected.pop('record_sha256')
        self.selected = generations.seal(self.selected)
        self.host = Host({'lv_path': self.target['disk']['path'], 'lv_uuid': self.target['disk']['uuid'],
            'lv_size': str(self.target['disk']['bytes']), 'lv_attr': '-wi-a-----', 'origin': '',
            'lv_tags': 'routergen_' + self.target['generation_id']})
        for name, value in (('cleanup_plan', lambda *args: self.selected),
                            ('boot_assignment', mock.Mock(return_value='accepted-assignment-verified'))):
            patch = mock.patch.object(executor, name, value)
            patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(executor.candidate_disk, 'inventory',
            side_effect=lambda: [self.host.row] if self.host.row else [])
        patch.start(); self.addCleanup(patch.stop)
        self.commands = []
        self.interrupt_remove = False
        patch = mock.patch.object(executor.native, 'command', side_effect=self.command)
        patch.start(); self.addCleanup(patch.stop)
        now = int(time.time())
        self.token = 'a' * 12
        self.grant = {'kind': 'klokast.router-cleanup-authorization.v1',
            'plan_sha256': self.selected['record_sha256'], 'assignment_sha256': self.selected['assignment_sha256'],
            'service_sha256': '1' * 64, 'granted_at': now, 'expires_at': now + 600,
            'device': {'generation_sha256': self.target['record_sha256'],
                'machine_id': 'nNewRouter', 'provider_id': 'provider-new', 'status': 'absent'}}
        self.grant_path = self.work / ('cleanup-grant-' + self.token + '.json')
        records.write(self.grant_path, self.grant)

    def command(self, argv, *args, **kwargs):
        self.assertEqual(argv, ['/sbin/lvremove', '--yes', self.target['disk']['path']])
        self.assertEqual(records.read(self.work / 'cleanup-progress.json')['phase'], 'disk-removing')
        self.commands.append(argv)
        self.host.row = None
        if self.interrupt_remove:
            self.interrupt_remove = False
            raise KeyboardInterrupt
        return ''

    def retire(self):
        return executor.retire_completed(self.records, self.request['operation_id'],
            self.request['engine_commit'], self.token, host=self.host)

    def test_supervisor_fences_worker_before_collecting_and_rechecking_completion(self):
        original = executor.retire_completed
        events = []
        process = mock.Mock()
        def wait(child, seconds):
            self.assertIs(child, process)
            self.assertEqual(seconds, 360)
            original(self.records, self.request['operation_id'], self.request['engine_commit'],
                self.token, host=self.host)
            events.append('fenced')
            return 0
        def collect(*args):
            self.assertEqual(events, ['fenced'])
            events.append('collected')
            return original(*args, host=self.host)
        with mock.patch.object(executor.subprocess, 'Popen', return_value=process) as spawn, \
                mock.patch.object(executor, 'wait_worker', side_effect=wait), \
                mock.patch.object(executor, 'retire_completed', side_effect=collect):
            result = executor.supervise_cleanup(self.records, self.request['operation_id'],
                self.request['engine_commit'], self.token)
        self.assertEqual(result['status'], 'exact-resources-retired')
        self.assertEqual(events, ['fenced', 'collected'])
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertIn('cleanup-worker', spawn.call_args.args[0])
        self.assertEqual(len(self.commands), 1)

    def test_worker_exit_without_native_completion_never_looks_retired(self):
        with mock.patch.object(executor.subprocess, 'Popen'), \
                mock.patch.object(executor, 'wait_worker', return_value=9):
            with self.assertRaisesRegex(TransactionError, 'no protected completion'):
                executor.supervise_cleanup(self.records, self.request['operation_id'],
                    self.request['engine_commit'], self.token)
        self.assertEqual(self.commands, [])

    def test_exact_native_retirement_preserves_metadata_and_is_idempotent(self):
        result = self.retire()
        self.assertEqual(result['status'], 'exact-resources-retired')
        self.assertEqual(result['disk'], self.target['disk'])
        self.assertEqual(len(self.commands), 1)
        self.assertTrue((self.work / 'complete.json').exists())
        self.assertTrue((self.work / 'completion-assignment.json').exists())
        self.assertEqual(self.records.accepted(), self.original)
        self.assertEqual(self.retire(), result)
        self.assertEqual(len(self.commands), 1)

    def test_controller_loss_after_lvremove_reconciles_intent_without_second_delete(self):
        self.interrupt_remove = True
        with self.assertRaises(KeyboardInterrupt):
            self.retire()
        self.assertEqual(records.read(self.work / 'cleanup-progress.json')['phase'], 'disk-removing')
        self.assertEqual(self.retire()['status'], 'exact-resources-retired')
        self.assertEqual(len(self.commands), 1)

    def test_loss_after_first_boot_file_removal_finishes_only_remaining_file(self):
        unlink = Path.unlink
        selected = Path(self.target['boot']['kernel']['path'])
        def interrupted(path, *args, **kwargs):
            result = unlink(path, *args, **kwargs)
            if path == selected:
                raise KeyboardInterrupt
            return result
        with mock.patch.object(Path, 'unlink', interrupted), self.assertRaises(KeyboardInterrupt):
            self.retire()
        self.assertEqual(records.read(self.work / 'cleanup-progress.json')['phase'], 'boot-removing')
        self.assertEqual(self.retire()['status'], 'exact-resources-retired')
        self.assertEqual(len(self.commands), 1)

    def test_absent_disk_without_intent_is_not_adopted_as_cleanup(self):
        self.host.row = None
        with self.assertRaisesRegex(TransactionError, 'without a protected retirement intent'):
            self.retire()
        self.assertFalse((self.work / 'cleanup-complete.json').exists())

    def test_reused_uuid_attached_disk_and_wrong_running_generation_refuse(self):
        self.host.row['lv_uuid'] = 'replacement-uuid'
        with self.assertRaisesRegex(TransactionError, 'UUID'):
            self.retire()
        self.host.row['lv_uuid'] = self.target['disk']['uuid']
        self.host.attached = True
        with self.assertRaisesRegex(TransactionError, 'Xen backend'):
            self.retire()
        self.host.attached = False
        self.host.live = '1'
        with self.assertRaisesRegex(TransactionError, 'exact running current'):
            self.retire()
        self.assertEqual(self.commands, [])

    def test_changed_allocation_tag_blocks_disk_removal(self):
        self.host.row['lv_tags'] = 'another-owner'
        with self.assertRaisesRegex(TransactionError, 'ownership tag'):
            self.retire()
        self.assertEqual(self.commands, [])

    def test_renamed_uuid_cannot_be_reported_as_removed(self):
        self.host.row['lv_path'] = '/dev/vg0/renamed-obsolete'
        with self.assertRaisesRegex(TransactionError, 'UUID moved'):
            self.retire()
        self.assertEqual(self.commands, [])

    def test_stale_grant_wrong_device_and_missing_enrollment_identity_refuse(self):
        for changes in ({'expires_at': 1}, {'plan_sha256': '0' * 64},
                        {'device': {**self.grant['device'], 'machine_id': 'nOldRouter'}}):
            records.write(self.grant_path, {**self.grant, **changes})
            with self.assertRaises(TransactionError):
                self.retire()
        self.assertEqual(self.commands, [])
        self.selected['retire'][0]['device'] = None
        self.selected.pop('record_sha256')
        self.selected = generations.seal(self.selected)
        self.grant.update(plan_sha256=self.selected['record_sha256'], device={
            'generation_sha256': self.target['record_sha256'], 'machine_id': None,
            'provider_id': None, 'status': 'no-device'})
        records.write(self.grant_path, self.grant)
        with self.assertRaisesRegex(TransactionError, 'cannot infer remote absence'):
            self.retire()

    def test_never_started_candidate_needs_native_absence_of_enrollment_attempt(self):
        self.selected['retire'][0]['device'] = None
        self.selected.pop('record_sha256')
        self.selected = generations.seal(self.selected)
        complete = records.read(self.work / 'complete.json')
        complete['candidate_started'] = False
        records.write(self.work / 'complete.json', complete)
        self.grant.update(plan_sha256=self.selected['record_sha256'], device={
            'generation_sha256': self.target['record_sha256'], 'machine_id': None,
            'provider_id': None, 'status': 'no-device'})
        records.write(self.grant_path, self.grant)
        records.write(self.work / 'enrollment-attempt.json', {'started': True})
        with self.assertRaisesRegex(TransactionError, 'cannot infer remote absence'):
            self.retire()
        self.assertEqual(self.commands, [])
        (self.work / 'enrollment-attempt.json').unlink()
        self.assertEqual(self.retire()['device']['status'], 'no-device')

    def test_empty_plan_does_not_remove_any_lv_or_boot_file(self):
        self.selected['retire'] = []
        self.selected.pop('record_sha256')
        self.selected = generations.seal(self.selected)
        self.grant.update(plan_sha256=self.selected['record_sha256'], device={
            'generation_sha256': None, 'machine_id': None, 'provider_id': None, 'status': 'no-target'})
        records.write(self.grant_path, self.grant)
        result = self.retire()
        self.assertIsNone(result['disk'])
        self.assertEqual(self.commands, [])
        self.assertTrue(Path(self.target['boot']['kernel']['path']).exists())

    def test_changed_artifact_refuses_before_lv_removal_and_reappeared_disk_refuses(self):
        path = Path(self.target['boot']['kernel']['path'])
        saved = path.read_bytes()
        path.write_bytes(b'changed')
        with self.assertRaises(TransactionError):
            self.retire()
        self.assertEqual(self.commands, [])
        path.write_bytes(saved)
        self.retire()
        self.host.row = {'lv_path': self.target['disk']['path'], 'lv_uuid': 'reappeared'}
        with self.assertRaisesRegex(TransactionError, 'reappeared'):
            self.retire()

    def test_shared_boot_artifact_and_changed_completed_result_refuse(self):
        self.selected['keep'][0]['generation']['boot']['kernel']['path'] = self.target['boot']['kernel']['path']
        with self.assertRaisesRegex(TransactionError, 'shared with a retained'):
            self.retire()
        self.selected['keep'][0]['generation']['boot']['kernel']['path'] = '/unrelated-kept-kernel'
        self.retire()
        records.write(self.work / 'cleanup-complete.json', {'changed': True})
        with self.assertRaisesRegex(TransactionError, 'completed result changed'):
            self.retire()


if __name__ == '__main__':
    unittest.main()
