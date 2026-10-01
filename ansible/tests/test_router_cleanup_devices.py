"""Cleanup excludes retained identities and selects only an exact offline provider."""
import copy
import unittest

import test_router_cleanup_plan as plan_tests
import router_executor as executor
import router_generation_device as devices
from router_transaction import TransactionError


class CleanupDevicesTests(unittest.TestCase):
    def setUp(self):
        plan_tests.CleanupPlanTests.setUp(self)
        self.complete('rolled-back')
        self.selected = executor.cleanup_plan(self.records, self.request['operation_id'], self.request['engine_commit'])
        self.api = {'devices': []}
        self.status = {'BackendState': 'Running', 'Self': {'ID': 'nController'}, 'Peer': {}}
        for number, resource in enumerate(self.selected['keep'] + self.selected['retire']):
            device = resource['device']
            address = ['100.64.0.' + str(number + 1)]
            self.api['devices'].append({'nodeId': device['machine_id'], 'id': 'provider-' + str(number),
                'hostname': device['hostname'], 'tags': ['tag:vm'], 'addresses': address})
            self.status['Peer'][str(number)] = {'ID': device['machine_id'], 'HostName': device['hostname'],
                'Online': number == 0, 'TailscaleIPs': address}

    def test_selects_exact_offline_id_and_proves_absence_with_retained_router_preserved(self):
        self.assertEqual(devices.cleanup_target(self.selected, self.status, self.api), 'provider-1')
        self.api['devices'].pop()
        self.assertIsNone(devices.cleanup_target(self.selected, self.status, self.api))
        self.assertTrue(devices.cleanup_absent(self.selected, self.api))

    def test_online_target_changed_hostname_tag_or_address_refuses(self):
        baseline = copy.deepcopy(self.api)
        for changes in ({'hostname': 'another-router'}, {'tags': ['tag:ops']}, {'addresses': ['100.64.9.9']}):
            self.api = copy.deepcopy(baseline)
            self.api['devices'][-1].update(changes)
            with self.assertRaises(TransactionError):
                devices.cleanup_target(self.selected, self.status, self.api)
        self.api = baseline
        self.status['Peer']['1']['Online'] = True
        with self.assertRaises(TransactionError):
            devices.cleanup_target(self.selected, self.status, self.api)

    def test_duplicate_provider_or_missing_retained_registration_refuses(self):
        self.api['devices'].append(copy.deepcopy(self.api['devices'][-1]))
        with self.assertRaisesRegex(TransactionError, 'duplicate API'):
            devices.cleanup_target(self.selected, self.status, self.api)
        self.api['devices'].pop()
        self.api['devices'].pop(0)
        with self.assertRaisesRegex(TransactionError, 'retained device'):
            devices.cleanup_target(self.selected, self.status, self.api)

    def test_provider_id_shared_with_retained_registration_refuses(self):
        self.api['devices'][-1]['id'] = self.api['devices'][0]['id']
        with self.assertRaises(TransactionError):
            devices.cleanup_target(self.selected, self.status, self.api)

    def test_target_still_present_and_current_offline_refuse(self):
        with self.assertRaisesRegex(TransactionError, 'remains'):
            devices.cleanup_absent(self.selected, self.api)
        self.status['Peer']['0']['Online'] = False
        with self.assertRaisesRegex(TransactionError, 'current router online'):
            devices.cleanup_target(self.selected, self.status, self.api)

    def test_empty_plan_never_selects_a_device_and_missing_identity_is_not_ownership(self):
        self.selected['retire'] = []
        self.selected = executor.generations.seal({key: value for key, value in self.selected.items()
            if key != 'record_sha256'})
        self.assertIsNone(devices.cleanup_target(self.selected, self.status, self.api))
        self.assertTrue(devices.cleanup_absent(self.selected, self.api))


if __name__ == '__main__':
    unittest.main()
