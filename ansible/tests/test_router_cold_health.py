"""A cold recovery receipt needs complete, fresh controller observations."""
import copy
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_health as health
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_cold_recovery as fixtures


class HealthTests(unittest.TestCase):
    setUp = fixtures.RecoveryTests.setUp
    capture_bundle = fixtures.RecoveryTests.capture_bundle
    prepare_recovery = fixtures.RecoveryTests.prepare

    def observation(self):
        self.prepare_recovery()
        baseline = self.baseline.capture()
        original = records.read(self.bundle.directory / 'original-identity.json')
        now = int(time.time())
        guest = {'BackendState': 'Running', 'Self': {
            'ID': 'test-device', 'HostName': 'boxa-router', 'Online': True}}
        controller = {'BackendState': 'Running', 'Peer': {'peer': {
            'ID': 'test-device', 'HostName': 'boxa-router', 'Online': True}}}
        routes = {('boxa-' + name): {'uuid': uuid, 'gateway': '192.168.1.1',
            'route': '1.1.1.1 via 192.168.1.1 dev eth0 src 192.168.1.10',
            'gateway_ping': '1 packets transmitted, 1 packets received, 0% packet loss'}
            for name, uuid in baseline['domains'].items()}
        return {'kind': 'klokast.router-cold-recovery-observation.v1',
            'box': 'boxa', 'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
            'observed_at': now, 'baseline': baseline, 'identity': original,
            'accepted_manifest': {'kind': 'klokast.router-accepted-manifest.v1',
                'box': 'boxa', 'generation_sha256': self.generation['record_sha256'],
                'kernel_release': self.generation['kernel_release'],
                'origin': 'legacy', 'tailscale': None,
                'packages': self.generation['packages'],
                'configuration_files': self.generation['configuration_files']},
            'guest_status': guest, 'controller_status': controller,
            'direct_ping': 'pong from boxa-router (100.1.2.3) via [2001:db8::1]:41641 in 10ms',
            'routes': routes}

    def test_complete_fresh_original_produces_bound_receipt(self):
        observation = self.observation()
        receipt = health.create(observation, observation['observed_at'])
        self.assertEqual(receipt['identity_sha256'], observation['identity']['record_sha256'])
        self.assertEqual(receipt['baseline_sha256'], observation['baseline']['record_sha256'])
        self.assertEqual(receipt['generation_sha256'], self.generation['record_sha256'])

    def test_missing_route_relay_only_or_changed_identity_refuses(self):
        observation = self.observation()
        original = copy.deepcopy(observation)
        observation['routes'].pop('boxa-bak')
        with self.assertRaisesRegex(TransactionError, 'missing or unexpected'):
            health.create(observation, observation['observed_at'])
        observation = copy.deepcopy(original)
        observation['direct_ping'] = 'pong from boxa-router (100.1.2.3) via DERP(nyc) in 10ms'
        with self.assertRaisesRegex(TransactionError, 'direct controller'):
            health.create(observation, observation['observed_at'])
        observation = copy.deepcopy(original)
        observation['guest_status']['Self']['ID'] = 'new-device'
        observation['controller_status']['Peer']['peer']['ID'] = 'new-device'
        with self.assertRaisesRegex(TransactionError, 'identity changed'):
            health.create(observation, observation['observed_at'])

    def test_stale_or_wrong_accepted_generation_refuses(self):
        observation = self.observation()
        with self.assertRaisesRegex(TransactionError, 'stale'):
            health.create(observation, observation['observed_at'] + 121)
        observation['accepted_manifest']['generation_sha256'] = 'a'*64
        with self.assertRaisesRegex(TransactionError, 'accepted manifest'):
            health.create(observation, observation['observed_at'])

    def test_changed_guest_uuid_or_false_gateway_ping_refuses(self):
        observation = self.observation()
        original = copy.deepcopy(observation)
        observation['routes']['boxa-bak']['uuid'] = '11111111-1111-1111-1111-111111111111'
        with self.assertRaisesRegex(TransactionError, 'dependent route'):
            health.create(observation, observation['observed_at'])
        observation = copy.deepcopy(original)
        observation['routes']['boxa-bak']['gateway_ping'] = '1 packets transmitted, 0 packets received'
        with self.assertRaisesRegex(TransactionError, 'dependent route'):
            health.create(observation, observation['observed_at'])

    def test_dom0_stage_requires_restored_fence_and_exact_boot_assignment(self):
        observation = self.observation()
        receipt = health.create(observation, observation['observed_at'])
        staged = health.Health(self.bundle)
        records.write(staged.input, receipt)
        marker = {'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
                  'metadata_sha256': observation['identity']['metadata_sha256'],
                  'generation_sha256': self.generation['record_sha256'], 'phase': 'restoring'}
        with patch.object(self.storage, 'cold_test', return_value=marker):
            self.assertEqual(staged.stage(lambda: 'accepted-assignment-verified',
                                          now=observation['observed_at']), receipt)
            self.assertEqual(records.read(staged.path), receipt)
            with self.assertRaisesRegex(TransactionError, 'boot assignment'):
                staged.stage(lambda: 'different', now=observation['observed_at'])
        with self.assertRaisesRegex(TransactionError, 'restored accepted router'):
            staged.stage(lambda: 'accepted-assignment-verified', now=observation['observed_at'])

    def test_dom0_stage_refuses_stale_receipt_without_replacing_previous(self):
        observation = self.observation()
        receipt = health.create(observation, observation['observed_at'])
        staged = health.Health(self.bundle)
        records.write(staged.input, receipt)
        marker = {'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
                  'metadata_sha256': observation['identity']['metadata_sha256'],
                  'generation_sha256': self.generation['record_sha256'], 'phase': 'restoring'}
        with patch.object(self.storage, 'cold_test', return_value=marker):
            with self.assertRaisesRegex(TransactionError, 'stale'):
                staged.stage(lambda: 'accepted-assignment-verified',
                             now=observation['observed_at'] + 121)
        self.assertFalse(staged.path.exists())

    def test_dom0_stage_refuses_a_sealed_receipt_for_other_manifest(self):
        observation = self.observation()
        receipt = health.create(observation, observation['observed_at'])
        receipt = generations.seal({**{key: value for key, value in receipt.items()
                                         if key != 'record_sha256'},
                                    'accepted_manifest_sha256': 'f'*64})
        staged = health.Health(self.bundle)
        records.write(staged.input, receipt)
        with patch.object(self.storage, 'cold_test', return_value={
                'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
                'metadata_sha256': observation['identity']['metadata_sha256'],
                'generation_sha256': self.generation['record_sha256'],
                'phase': 'restoring'}):
            with self.assertRaisesRegex(TransactionError, 'differs from the restored'):
                staged.stage(lambda: 'accepted-assignment-verified',
                             now=observation['observed_at'])
        self.assertFalse(staged.path.exists())


if __name__ == '__main__':
    unittest.main()
