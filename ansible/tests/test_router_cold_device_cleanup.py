"""An archived test ID can select only its offline API device."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_device_cleanup as cleanup
import router_generations as generations
from router_transaction import TransactionError


class DeviceCleanupTests(unittest.TestCase):
    def setUp(self):
        self.source = generations.seal({'kind': 'klokast.router-cold-device-source.v1',
            'box': 'boxa', 'operation_id': 'a' * 24, 'engine_commit': 'b' * 40,
            'status': 'revocation-required', 'machine_id': 'new-device',
            'original_machine_id': 'old-device', 'hostname': 'boxa-router'})
        self.status = {'BackendState': 'Running', 'Peer': {
            'old': {'ID': 'old-device', 'HostName': 'boxa-router', 'Online': True,
                    'TailscaleIPs': ['100.64.0.1']},
            'test': {'ID': 'new-device', 'HostName': 'boxa-router', 'Online': False,
                     'TailscaleIPs': ['100.64.0.2']}}}
        self.api = {'devices': [
            {'id': '111', 'nodeId': 'old-device', 'hostname': 'boxa-router',
             'addresses': ['100.64.0.1']},
            {'id': '222', 'nodeId': 'new-device', 'hostname': 'boxa-router',
             'addresses': ['100.64.0.2']}]}

    def test_same_hostname_selects_only_the_archived_offline_node(self):
        self.assertEqual(cleanup.test_device(self.source, self.status, self.api), '222')
        self.api['devices'].pop()
        self.assertIsNone(cleanup.test_device(self.source, self.status, self.api))
        self.assertTrue(cleanup.absent(self.source, self.api))

    def test_online_test_changed_address_or_missing_original_refuses(self):
        self.status['Peer']['test']['Online'] = True
        with self.assertRaisesRegex(TransactionError, 'offline'):
            cleanup.test_device(self.source, self.status, self.api)
        self.status['Peer']['test']['Online'] = False
        self.api['devices'][1]['addresses'] = ['100.64.0.9']
        with self.assertRaisesRegex(TransactionError, 'differs'):
            cleanup.test_device(self.source, self.status, self.api)
        self.api['devices'][1]['addresses'] = ['100.64.0.2']
        self.status['Peer']['old']['Online'] = False
        with self.assertRaisesRegex(TransactionError, 'original'):
            cleanup.test_device(self.source, self.status, self.api)

    def test_original_id_cannot_become_the_deletion_target(self):
        changed = {key: value for key, value in self.source.items() if key != 'record_sha256'}
        changed['machine_id'] = changed['original_machine_id']
        with self.assertRaisesRegex(TransactionError, 'retained test identity'):
            cleanup.test_device(generations.seal(changed), self.status, self.api)
        self.api['devices'] = []
        with self.assertRaisesRegex(TransactionError, 'retained original'):
            cleanup.absent(self.source, self.api)


if __name__ == '__main__':
    unittest.main()
