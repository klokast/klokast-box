"""Controller cleanup orders service proof, exact broker deletion and native result."""
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import unittest
from unittest import mock

import test_router_cleanup_plan as plan_tests
from test_router_completion_controller import cli
import router_executor as executor
from platform_updates import UpdateError


class CleanupDriverTests(unittest.TestCase):
    def setUp(self):
        plan_tests.CleanupPlanTests.setUp(self)
        plan_tests.CleanupPlanTests.prior(self)
        self.complete()
        self.plan_value = executor.cleanup_plan(self.records, self.request['operation_id'], self.request['engine_commit'])
        self.module = cli()
        self.state = self.base / 'controller'
        self.state.mkdir(mode=0o700)
        self.directory = self.state / self.request['operation_id']
        self.directory.mkdir(mode=0o700)
        self.source = executor.accepted_source(self.records)
        self.events = []
        self.api = {'devices': []}
        self.status = {'BackendState': 'Running', 'Self': {'ID': 'nController'}, 'Peer': {}}
        for index, item in enumerate(self.plan_value['keep'] + self.plan_value['retire']):
            device = item['device']
            addresses = ['100.64.0.' + str(index + 1)]
            self.api['devices'].append({'id': 'provider-' + str(index), 'nodeId': device['machine_id'],
                'hostname': device['hostname'], 'addresses': addresses, 'tags': ['tag:vm']})
            self.status['Peer'][str(index)] = {'ID': device['machine_id'], 'HostName': device['hostname'],
                'Online': index == 0, 'TailscaleIPs': addresses}
        self.fail_service = self.fail_delete = self.fail_lbu = False
        self.foreign_native = False
        patches = [(self.module, 'STATE', self.state),
            (self.module.transport, 'require_controller', mock.Mock()),
            (self.module.transport, 'installation_lock', mock.Mock(side_effect=nullcontext)),
            (self.module.transport, 'approved_engine', mock.Mock(return_value=self.request['engine_commit'])),
            (self.module, 'read_cleanup_plan', mock.Mock(return_value={'plan': self.plan_value})),
            (self.module, 'accepted_source_at', mock.Mock(return_value=self.source)),
            (self.module.transport, 'command', mock.Mock(side_effect=self.command))]
        for owner, name, value in patches:
            patch = mock.patch.object(owner, name, value)
            patch.start(); self.addCleanup(patch.stop)

    def command(self, argv, **kwargs):
        text = [str(value) for value in argv]
        if text == ['/usr/bin/tailscale', 'status', '--json']:
            return json.dumps(self.status)
        if 'ts-devices-list' in text[-1]:
            return json.dumps(self.api)
        if any('ts-device-delete-stale' in value for value in text):
            self.events.append('delete')
            self.assertEqual(text[text.index('--id') + 1], 'provider-2')
            self.assertEqual(text[text.index('--machine-id') + 1], 'nPriorRouter')
            self.assertTrue((self.directory / 'cleanup-device-intent.json').exists())
            self.api['devices'].pop()
            if self.fail_delete:
                self.fail_delete = False
                raise UpdateError('broker reply lost after deletion')
            return ''
        if any('74-router-accepted-verification.yml' in value for value in text):
            self.events.append('service')
            if self.fail_service:
                raise UpdateError('service failed')
        elif any(value.endswith('/platform-check') for value in text):
            self.events.append('health')
        elif any('74-router-cleanup-retire.yml' in value for value in text):
            self.events.append('retire')
            self.assertEqual(len(self.api['devices']), 2)
            arguments = self.module.transport.load(Path(text[-1][1:]))
            grant = arguments['router_cleanup_grant']
            self.assertEqual(grant['device']['provider_id'], 'provider-2')
            self.assertEqual(grant['device']['status'], 'absent')
            target = self.plan_value['retire'][0]['generation']
            result = executor.generations.seal({'kind': 'klokast.router-cleanup-complete.v2',
                'box': self.records.box, 'operation_id': self.request['operation_id'],
                'engine_commit': self.request['engine_commit'], 'plan_sha256': self.plan_value['record_sha256'],
                'assignment_sha256': self.plan_value['assignment_sha256'],
                'generation_sha256': target['record_sha256'], 'disk': target['disk'],
                'device': grant['device'], 'copy_retirement_sha256': 'd' * 64, 'status': 'exact-resources-retired'})
            outer = {'kind': 'klokast.router-command-result.v1', 'box': self.records.box,
                'engine_commit': self.request['engine_commit'], 'action': 'retire-completed', 'result': result}
            if self.foreign_native:
                outer['action'] = 'completion-status'
            self.module.transport.write(self.directory / ('cleanup-native-' + arguments['router_cleanup_token'] + '.json'), outer)
        elif any('74-router-diagnostic-lbu-commit.yml' in value for value in text):
            self.events.append('persist')
            if self.fail_lbu:
                raise UpdateError('unrelated LBU change')
        else:
            raise AssertionError('unexpected cleanup command: ' + repr(text))
        return ''

    def cleanup(self):
        return self.module.cleanup_replacement(self.records.box, self.request['operation_id'])

    def test_checks_services_before_exact_deletion_and_native_retirement_before_persistence(self):
        result = self.cleanup()
        self.assertEqual(self.events, ['service', 'health', 'health', 'delete', 'retire', 'persist'])
        self.assertEqual(result['status'], 'exact-resources-retired')
        self.assertTrue((self.directory / 'cleanup-result.json').exists())
        self.assertEqual(self.cleanup(), result)
        self.assertEqual(self.events.count('delete'), 1)

    def test_service_failure_prevents_device_or_disk_mutations(self):
        self.fail_service = True
        with self.assertRaisesRegex(UpdateError, 'service failed'):
            self.cleanup()
        self.assertEqual(self.events, ['service'])
        self.assertEqual(len(self.api['devices']), 3)

    def test_lost_broker_reply_retains_identity_and_retry_does_not_delete_again(self):
        self.fail_delete = True
        with self.assertRaisesRegex(UpdateError, 'reply lost'):
            self.cleanup()
        self.assertNotIn('retire', self.events)
        self.cleanup()
        self.assertEqual(self.events.count('delete'), 1)
        self.assertEqual(self.events.count('retire'), 1)

    def test_failed_lbu_persistence_cannot_be_reported_as_complete(self):
        self.fail_lbu = True
        with self.assertRaisesRegex(UpdateError, 'LBU change'):
            self.cleanup()
        self.assertFalse((self.directory / 'cleanup-result.json').exists())
        self.fail_lbu = False
        self.cleanup()
        self.assertEqual(self.events.count('delete'), 1)
        self.assertTrue((self.directory / 'cleanup-result.json').exists())

    def test_foreign_native_result_and_changed_service_assignment_refuse(self):
        self.foreign_native = True
        with self.assertRaisesRegex(UpdateError, 'another native source'):
            self.cleanup()
        self.assertFalse((self.directory / 'cleanup-result.json').exists())
        self.foreign_native = False
        changed = copy.deepcopy(self.source)
        changed['assignment']['record_sha256'] = '0' * 64
        self.module.accepted_source_at.side_effect = [self.source, changed]
        with self.assertRaisesRegex(UpdateError, 'changed during service'):
            self.cleanup()


if __name__ == '__main__':
    unittest.main()
