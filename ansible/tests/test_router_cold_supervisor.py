"""A cold supervisor request binds every pre-stop K001 recovery input."""
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_supervisor as supervisor
import router_generations as generations
import router_records as records
import router_executor as executor
from router_transaction import TransactionError
import test_router_cold_window as fixtures


class RequestTests(unittest.TestCase):
    setUp = fixtures.WindowTests.setUp
    capture = fixtures.WindowTests.capture
    prepare_window = fixtures.WindowTests.prepare
    command = fixtures.WindowTests.command

    def prepare(self):
        self.prepare_window()
        self.window.marker.unlink()
        self.host.running = True
        self.window.backup.save(self.disk_record, stage='allocated', source_sha256=None)
        metadata, generation = self.bundle.verify()
        identity = records.read(self.bundle.directory / 'original-identity.json')
        baseline = records.read(self.bundle.directory / 'dependent-baseline.json')
        self.request = supervisor.Request(self.bundle)
        now = int(time.time())
        value = generations.seal({'kind': 'klokast.router-cold-supervised-request.v1',
            'box': 'boxa', 'operation_id': self.bundle.operation,
            'engine_commit': self.bundle.engine,
            'metadata_sha256': metadata['record_sha256'],
            'generation_sha256': generation['record_sha256'],
            'identity_sha256': identity['record_sha256'],
            'baseline_sha256': baseline['record_sha256'],
            'backup_uuid': 'backup-uuid',
            'bootstrap_sha256': self.capsule['record_sha256'],
            'original_xen_uuid': generation['xen']['uuid'],
            'initial_operation': self.initial,
            'initial_provision': generations.seal({
                'kind': 'klokast.router-initial-provision-pointer.v1', 'box': 'boxa',
                'engine_commit': self.bundle.engine, 'operation_id': self.initial,
                'source_operation': 'a' * 24, 'template_operation': 'b' * 24,
                'selection_sha256': 'c' * 64, 'release_sha256': 'd' * 64}),
            'issued_at': now,
            'expires_at': now + 3600})
        records.write(self.request.path, value)
        return value, now

    def test_request_binds_prepared_live_source_without_arming_fence(self):
        value, now = self.prepare()
        self.assertEqual(self.request.verify(now=now), value)
        self.assertFalse(self.window.marker.exists())

    def test_stale_or_changed_backup_refuses_request(self):
        value, now = self.prepare()
        with self.assertRaisesRegex(TransactionError, 'stale'):
            self.request.verify(now=now + 901)
        self.backup_row['lv_uuid'] = 'foreign'
        with self.assertRaisesRegex(TransactionError, 'identity'):
            self.request.verify(now=now)

    def test_changed_running_guest_set_refuses_request(self):
        _, now = self.prepare()
        self.host.inventory.return_value.pop()
        with self.assertRaisesRegex(TransactionError, 'unchanged running original'):
            self.request.verify(now=now)

    def test_request_stage_is_exact_and_does_not_arm_fence(self):
        value, now = self.prepare()
        self.request.path.unlink()
        self.assertEqual(self.request.stage(value, now=now), value)
        changed = generations.seal({**{key: item for key, item in value.items()
                                       if key != 'record_sha256'},
                                   'expires_at': now + 3000})
        with self.assertRaisesRegex(TransactionError, 'retry changed'):
            self.request.stage(changed, now=now)
        self.assertFalse(self.window.marker.exists())

    def grant(self, request, now):
        return generations.seal({'kind': 'klokast.router-cold-outage-authorization.v1',
            **{key: request[key] for key in ('box', 'operation_id', 'engine_commit')},
            'request_sha256': request['record_sha256'], 'outage_authorized': True,
            'granted_at': now, 'expires_at': now + 300})

    def test_outage_approval_is_exact_explicit_and_short_lived(self):
        request, now = self.prepare()
        grant = self.grant(request, now)
        self.assertEqual(supervisor.authorization(grant, request, now=now), grant)
        for field, value in (('request_sha256', 'f' * 64), ('outage_authorized', False),
                             ('expires_at', now + 301), ('granted_at', now + 1)):
            wrong = generations.seal({**{key: item for key, item in grant.items()
                                        if key != 'record_sha256'}, field: value})
            with self.subTest(field=field), self.assertRaises(TransactionError):
                supervisor.authorization(wrong, request, now=now)
        with self.assertRaises(TransactionError):
            supervisor.authorization(grant, request, now=now + 300)

    def test_changed_initial_template_cannot_reuse_the_outage_approval(self):
        request, now = self.prepare()
        grant = self.grant(request, now)
        pointer = {key: item for key, item in request['initial_provision'].items()
                   if key != 'record_sha256'}
        pointer['template_operation'] = 'f' * 24
        changed = {key: item for key, item in request.items() if key != 'record_sha256'}
        changed['initial_provision'] = generations.seal(pointer)
        with self.assertRaisesRegex(TransactionError, 'exact supervised request'):
            supervisor.authorization(grant, generations.seal(changed), now=now)
        pointer['operation_id'] = 'e' * 24
        changed['initial_provision'] = generations.seal(pointer)
        with self.assertRaisesRegex(TransactionError, 'prepared original'):
            self.request.validate(generations.seal(changed), now=now)

    def test_parent_recovers_only_after_worker_process_group_is_fenced(self):
        request, now = self.prepare()
        records.write(self.bundle.directory / 'outage-authorization.json', self.grant(request, now))
        storage = SimpleNamespace(box='k001', cold_test=Mock(return_value={'phase': 'open'}))
        cycle = Mock()
        cycle.request.verify.return_value = request
        events = []
        cycle.run.side_effect = lambda: events.append('recover') or 'original-running-fenced'
        with patch.object(executor.cold_backup, 'Bundle', return_value=self.bundle), \
             patch.object(executor.cold_cycle, 'Cycle', return_value=cycle), \
             patch.object(executor.subprocess, 'Popen') as launch, \
             patch.object(executor, 'wait_worker', side_effect=lambda *args: events.append('fenced') or -9):
            self.assertEqual(executor.supervise_cold(storage, self.bundle.operation, self.bundle.engine),
                             'original-running-fenced')
            self.assertEqual(events, ['fenced', 'recover'])
            self.assertTrue(launch.call_args.kwargs['start_new_session'])
            with self.assertRaises(FileExistsError):
                executor.supervise_cold(storage, self.bundle.operation, self.bundle.engine)
            self.assertEqual(launch.call_count, 1)

    def test_parent_never_reopens_after_worker_exits_before_arming(self):
        request, now = self.prepare()
        records.write(self.bundle.directory / 'outage-authorization.json', self.grant(request, now))
        storage = SimpleNamespace(box='k001', cold_test=Mock(return_value=None))
        cycle = Mock()
        cycle.request.verify.return_value = request
        cycle.result = self.bundle.directory / 'supervisor-result.json'
        with patch.object(executor.cold_backup, 'Bundle', return_value=self.bundle), \
             patch.object(executor.cold_cycle, 'Cycle', return_value=cycle), \
             patch.object(executor.subprocess, 'Popen'), \
             patch.object(executor, 'wait_worker', return_value=1):
            with self.assertRaisesRegex(TransactionError, 'before arming'):
                executor.supervise_cold(storage, self.bundle.operation, self.bundle.engine)
        cycle.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
