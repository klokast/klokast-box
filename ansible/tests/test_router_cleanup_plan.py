"""Cleanup selects exact unretained generations from protected native history."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_executor as executor
import router_generation_device as devices
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_completion as completion_tests


class CleanupPlanTests(unittest.TestCase):
    def setUp(self):
        completion_tests.CompletionTests.setUp(self)
        self.original = self.records.accepted()
        records.write(self.work / 'original-assignment.json', self.original)
        for generation, identity in ((self.old, 'nOldRouter'), (self.new, 'nNewRouter')):
            devices.remember(self.records, generation['record_sha256'], identity,
                'boxa-router' if generation['origin'] == 'legacy' else
                generations.tailnet_hostname('boxa', generation['generation_id']), '5' * 64)
        self.read = lambda: completion_tests.CompletionTests.read(self)
        self.complete = lambda *args, **kwargs: completion_tests.CompletionTests.complete(self, *args, **kwargs)

    def plan(self):
        return executor.cleanup_plan(self.records, self.request['operation_id'], self.request['engine_commit'])

    def prior(self, disk_uuid=None):
        value = copy.deepcopy(self.new)
        value.pop('record_sha256')
        value['generation_id'] = 'e' * 24
        value['disk'].update(path='/dev/vg0/routergen_' + 'e' * 24, uuid=disk_uuid or 'older-uuid')
        value['xen']['uuid'] = '33333333-1111-4111-8111-111111111111'
        for name in value['boot']:
            value['boot'][name]['path'] = '/mnt/dom0_data/klokast-router-updates/generations/' + 'e' * 24 + '/' + name
        prior = generations.seal(value)
        records.write(self.base / 'records' / (prior['record_sha256'] + '.json'), prior)
        devices.remember(self.records, prior['record_sha256'], 'nPriorRouter',
            generations.tailnet_hostname('boxa', prior['generation_id']), '6' * 64)
        original = {key: value for key, value in self.original.items() if key != 'record_sha256'}
        original['previous_sha256'] = prior['record_sha256']
        self.original = generations.seal(original)
        records.write(self.base / 'accepted.json', self.original)
        records.write(self.work / 'original-assignment.json', self.original)
        self.request['accepted_sha256'] = self.original['record_sha256']
        records.write(self.work / 'transaction-request.json', self.request)
        self.adapter.ready['request_sha256'] = generations.digest(self.request)
        records.write(self.work / 'readiness.json', self.adapter.ready)
        return prior

    def test_first_update_keeps_both_os_generations_and_retires_nothing(self):
        self.complete()
        with mock.patch.object(executor.native, 'command', side_effect=AssertionError('planning cannot delete')):
            plan = self.plan()
        self.assertEqual([row['generation']['record_sha256'] for row in plan['keep']],
            [self.new['record_sha256'], self.old['record_sha256']])
        self.assertEqual(plan['retire'], [])
        self.assertFalse(plan['retirement_authorized'])
        self.assertEqual(plan['status'], 'planned-no-retirement')
        self.assertEqual(self.plan(), plan)
        self.assertEqual(records.read(self.work / 'cleanup-plan.json'), plan)

    def test_later_acceptance_selects_only_the_older_previous_generation(self):
        prior = self.prior()
        self.complete()
        plan = self.plan()
        self.assertEqual([row['generation']['record_sha256'] for row in plan['retire']], [prior['record_sha256']])
        self.assertEqual(plan['retire'][0]['generation']['disk'], prior['disk'])
        self.assertEqual(plan['retire'][0]['device']['machine_id'], 'nPriorRouter')
        self.assertEqual(len(plan['keep']), 2)

    def test_rollback_selects_rejected_candidate_and_keeps_original_previous(self):
        prior = self.prior()
        self.complete('rolled-back')
        plan = self.plan()
        self.assertEqual([row['generation']['record_sha256'] for row in plan['keep']],
            [self.old['record_sha256'], prior['record_sha256']])
        self.assertEqual(plan['retire'][0]['generation']['record_sha256'], self.new['record_sha256'])
        self.assertEqual(plan['retire'][0]['device']['machine_id'], 'nNewRouter')

    def test_obsolete_disk_sharing_a_retained_uuid_is_not_selected(self):
        self.prior(disk_uuid=self.old['disk']['uuid'])
        self.complete()
        with self.assertRaisesRegex(TransactionError, 'generation disks overlap'):
            self.plan()
        self.assertFalse((self.work / 'cleanup-plan.json').exists())

    def test_missing_original_snapshot_never_guesses_old_resources(self):
        self.complete()
        (self.work / 'original-assignment.json').unlink()
        with self.assertRaisesRegex(TransactionError, 'do not infer'):
            self.plan()
        self.assertFalse((self.work / 'cleanup-plan.json').exists())

    def test_changed_original_assignment_or_current_pointer_refuses(self):
        self.complete()
        changed = {key: value for key, value in self.original.items() if key != 'record_sha256'}
        changed['evidence_sha256'] = '7' * 64
        records.write(self.work / 'original-assignment.json', generations.seal(changed))
        with self.assertRaisesRegex(TransactionError, 'original assignment differs'):
            self.plan()
        records.write(self.work / 'original-assignment.json', self.original)
        current = {key: value for key, value in self.records.accepted().items() if key != 'record_sha256'}
        current['evidence_sha256'] = '7' * 64
        records.write(self.base / 'accepted.json', generations.seal(current))
        with self.assertRaisesRegex(TransactionError, 'later accepted work'):
            self.plan()

    def test_missing_started_candidate_identity_or_reused_device_refuses(self):
        self.complete('rolled-back')
        device_path = devices.path(self.records, self.new['record_sha256'])
        value = records.read(device_path)
        device_path.unlink()
        with self.assertRaisesRegex(TransactionError, 'no protected device'):
            self.plan()
        value = {key: item for key, item in value.items() if key != 'record_sha256'}
        value['machine_id'] = 'nOldRouter'
        records.write(device_path, generations.seal(value))
        with self.assertRaisesRegex(TransactionError, 'device is shared'):
            self.plan()

    def test_changed_saved_plan_is_not_replaced_by_retry(self):
        self.complete()
        self.plan()
        records.write(self.work / 'cleanup-plan.json', {'changed': True})
        with self.assertRaisesRegex(TransactionError, 'plan changed'):
            self.plan()
        self.assertEqual(records.read(self.work / 'cleanup-plan.json'), {'changed': True})

    def test_staging_keeps_the_original_assignment_immutable(self):
        records.write(self.work / 'proposed-generation.json', self.new)
        executor.stage_cutover(self.records, self.request['operation_id'], self.request['engine_commit'])
        self.assertEqual(records.read(self.work / 'original-assignment.json'), self.original)
        records.write(self.work / 'original-assignment.json', self.records.accepted() | {'record_sha256': '0' * 64})
        with self.assertRaises(RuntimeError):
            executor.stage_cutover(self.records, self.request['operation_id'], self.request['engine_commit'])


if __name__ == '__main__':
    unittest.main()
