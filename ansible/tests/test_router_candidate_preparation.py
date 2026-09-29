"""Preparation recovery must never repeat writes or select another disk."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_candidate_preparation as preparation
import test_router_candidate as candidate_fixture


class PreparationTests(unittest.TestCase):
    def setUp(self):
        fixture = candidate_fixture.CandidateTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.job = copy.deepcopy(fixture.job)
        self.result = {
            'kind':'klokast.router-candidate-preparation-result.v1',
            'operation_id':self.job['operation_id'], 'inputs_sha256':self.job['inputs_sha256'],
            'job_sha256':preparation.router_personalize.digest(self.job),
            'success':True, 'prepared':fixture.prepare()}
        self.value = {key:self.job[key] for key in (
            'box', 'mode', 'operation_id', 'inputs_sha256', 'engine_commit')}
        self.value['job_sha256'] = self.result['job_sha256']
        self.disk = {'path':'/dev/vg0/routergen_' + self.job['operation_id'],
                     'uuid':'recorded-uuid', 'bytes':2147483648}
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.attached = []
        self.host = Mock()
        self.boot = Mock(side_effect=self.guest)
        self.verify = Mock(return_value=self.disk)
        self.retire = Mock(side_effect=AssertionError('runner must not retire its disk'))
        for target, name, replacement in (
                (preparation, 'safe_directory', lambda path:None),
                (preparation, 'safe_file', lambda path, maximum:None),
                (preparation, 'domain', Mock(return_value=None)),
                (preparation, 'boot_guest', self.boot),
                (preparation, 'attach_loop', self.attach),
                (preparation, 'detach_loop', self.detach),
                (preparation, 'loop_devices', lambda path:list(self.attached)),
                (preparation.router_records, 'parents', lambda path:None),
                (preparation.router_records, 'ROOT_UID', os.geteuid()),
                (preparation.router_native, 'Native', lambda:self.host),
                (preparation.router_candidate_disk, 'record', lambda *args:{'stage':'cloned'}),
                (preparation.router_candidate_disk, 'verify', self.verify),
                (preparation.router_candidate_disk, 'retire', self.retire)):
            context = patch.object(target, name, replacement)
            context.start()
            self.addCleanup(context.stop)

    def attach(self, path):
        self.assertEqual(self.attached, [])
        self.attached.append('/dev/loop7')
        return '/dev/loop7'

    def detach(self, path, device):
        self.assertEqual(self.attached, [device])
        self.attached.clear()

    def guest(self, work, name, identity, config, value, **kwargs):
        ledger = json.loads((work / 'preparation.json').read_text())
        self.assertEqual(ledger['stage'], 'booting')
        self.assertEqual(ledger['disk'], self.disk)
        self.assertEqual(ledger['uuid'], identity)
        self.assertEqual(ledger['request_sha256'], preparation.router_personalize.digest(value))
        with (work / 'result.slot').open('r+b') as stream:
            stream.write(json.dumps(self.result).encode() + b'\0')
            stream.flush()
            os.fsync(stream.fileno())

    def run_prepare(self):
        return preparation.prepare(self.work, self.value, self.job, self.disk)

    def ledger(self):
        return json.loads((self.work / 'preparation.json').read_text())

    def test_success_retains_exact_disk_and_retry_does_not_boot_or_write(self):
        self.assertEqual(self.run_prepare(), self.result)
        before = (self.work / 'preparation.json').read_bytes()
        self.assertEqual(self.ledger()['stage'], 'prepared')
        self.assertEqual(self.run_prepare(), self.result)
        self.assertEqual((self.work / 'preparation.json').read_bytes(), before)
        self.assertEqual(self.boot.call_count, 1)
        self.assertEqual(self.attached, [])
        self.retire.assert_not_called()

    def test_complete_guest_result_survives_interruption_before_result_publication(self):
        write = preparation.router_records.write
        def interrupted(path, value):
            if path.name == 'preparation-result.json':
                raise RuntimeError('controller interrupted after guest completion')
            return write(path, value)
        with patch.object(preparation.router_records, 'write', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'controller interrupted'):
                self.run_prepare()
        self.assertEqual(self.ledger()['stage'], 'booting')
        self.assertFalse((self.work / 'preparation-result.json').exists())
        self.assertEqual(self.run_prepare(), self.result)
        self.assertEqual(self.ledger()['stage'], 'prepared')
        self.assertEqual(self.boot.call_count, 1)

    def test_result_publication_survives_interruption_before_completion_record(self):
        write = preparation.router_records.write
        def interrupted(path, value):
            if path.name == 'preparation.json' and value['stage'] == 'prepared':
                raise RuntimeError('completion record interrupted')
            return write(path, value)
        with patch.object(preparation.router_records, 'write', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'completion record interrupted'):
                self.run_prepare()
        self.assertTrue((self.work / 'preparation-result.json').exists())
        self.assertEqual(self.run_prepare(), self.result)
        self.assertEqual(self.boot.call_count, 1)

    def test_incomplete_result_never_reboots_or_recreates_a_clone(self):
        self.boot.side_effect = RuntimeError('interrupted before guest result')
        with self.assertRaisesRegex(RuntimeError, 'interrupted before'):
            self.run_prepare()
        with self.assertRaisesRegex(RuntimeError, 'without a complete result'):
            self.run_prepare()
        self.assertEqual(self.boot.call_count, 1)
        self.retire.assert_not_called()

    def test_completed_preparation_rejects_changed_job_disk_or_boot_configuration(self):
        self.run_prepare()
        original = copy.deepcopy(self.value)
        self.value['engine_commit'] = 'f' * 40
        with self.assertRaisesRegex(RuntimeError, 'differs from its complete job'):
            self.run_prepare()
        self.value = original
        with patch.object(preparation.router_candidate_disk, 'verify', return_value={**self.disk, 'uuid':'other'}):
            with self.assertRaisesRegex(RuntimeError, 'recorded clone'):
                self.run_prepare()
        (self.work / 'prepare.cfg').write_text('changed boot definition')
        with self.assertRaisesRegex(RuntimeError, 'configuration or result slot changed'):
            self.run_prepare()
        self.assertEqual(self.boot.call_count, 1)

    def test_recorded_input_binding_rejects_another_valid_request(self):
        self.run_prepare()
        self.value['template_sha256'] = 'f' * 64
        with self.assertRaisesRegex(RuntimeError, 'recorded run'):
            self.run_prepare()
        self.assertEqual(self.boot.call_count, 1)

    def test_result_tampering_cannot_replace_completed_evidence(self):
        self.run_prepare()
        self.result['prepared']['accounts']['dnsmasq_uid'] += 1
        with (self.work / 'result.slot').open('r+b') as stream:
            stream.write(json.dumps(self.result).encode() + b'\0')
        with self.assertRaisesRegex(RuntimeError, 'result changed after completion'):
            self.run_prepare()
        self.assertEqual(self.boot.call_count, 1)

    def test_live_guest_or_attached_disk_prevents_preparation(self):
        with patch.object(preparation, 'domain', return_value={'domid':123}):
            with self.assertRaisesRegex(RuntimeError, 'guest remains'):
                self.run_prepare()
        self.verify.side_effect = RuntimeError('disk still attached')
        with self.assertRaisesRegex(RuntimeError, 'disk still attached'):
            self.run_prepare()
        self.boot.assert_not_called()
        self.assertEqual(list(self.work.iterdir()), [])

    def test_failed_loop_cleanup_is_reconciled_without_another_boot(self):
        with patch.object(preparation, 'detach_loop', side_effect=RuntimeError('detach interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'detach interrupted'):
                self.run_prepare()
        self.assertEqual(self.attached, ['/dev/loop7'])
        self.assertEqual(self.run_prepare(), self.result)
        self.assertEqual(self.attached, [])
        self.assertEqual(self.boot.call_count, 1)
        self.assertTrue(self.host.wait_detached.called)

    def test_changed_loop_identity_is_not_detached(self):
        self.run_prepare()
        self.attached = ['/dev/loop8']
        with patch.object(preparation, 'detach_loop') as detach:
            with self.assertRaisesRegex(RuntimeError, 'result loop changed'):
                self.run_prepare()
            detach.assert_not_called()

    def test_guest_evidence_must_match_the_complete_expected_configuration(self):
        for mutate in (
                lambda r:r['prepared'].update(box='boxb'),
                lambda r:r['prepared'].update(replacement_authorized=True),
                lambda r:r['prepared']['configuration_files'].update({'etc/nftables.nft':'f'*64}),
                lambda r:r['prepared']['accounts'].update(dnsmasq_uid=True),
                lambda r:r.update(prepared=None)):
            result = copy.deepcopy(self.result)
            mutate(result)
            with self.assertRaisesRegex(RuntimeError, 'evidence|complete job'):
                preparation.validate_result(result, self.value, self.job)

    def test_initial_mode_recovery_keeps_the_same_first_contact_identity(self):
        self.job['mode'] = self.value['mode'] = 'initial-install'
        self.job['first_contact'] = {'key':'ssh-ed25519 YQ==', 'backend_address':'192.0.2.2',
                                     'backend_source_address':'192.0.2.1', 'backend_prefix':24}
        self.job['personalization']['files']['etc/nftables.nft'] = (
            'table inet filter {\n    chain input {\n'
            '        type filter hook input priority 0; policy drop;\n    }\n}\n')
        self.result['prepared']['mode'] = 'initial-install'
        self.result['prepared']['packages'] = self.job['personalization']['packages']
        self.result['prepared']['configuration_files'] = {
            name:hashlib.sha256(content.encode()).hexdigest()
            for name, content in self.job['personalization']['files'].items()}
        self.value['job_sha256'] = self.result['job_sha256'] = preparation.router_personalize.digest(self.job)
        contact = preparation.router_initial_contact
        self.result['first_contact'] = {
            'kind':'klokast.router-first-contact.v1',
            'authorized_key_sha256':contact.digest_bytes(b'ssh-ed25519 YQ==\n'),
            'interfaces_sha256':contact.digest_bytes(contact.first_contact_interfaces('192.0.2.2', 24).encode()),
            'firewall_sha256':contact.digest_bytes(contact.first_contact_firewall(
                self.job['personalization']['files']['etc/nftables.nft'],
                '192.0.2.1', '192.0.2.2', 24).encode()),
            'sshd_config_sha256':contact.digest_bytes(contact.first_contact_sshd_config('192.0.2.2').encode()),
            'host_key_public_sha256':{'ed25519':'c'*64}}
        self.assertEqual(self.run_prepare(), self.result)
        self.assertEqual(self.run_prepare(), self.result)
        self.assertEqual(self.boot.call_count, 1)
        self.result['first_contact']['authorized_key_sha256'] = 'd'*64
        with self.assertRaisesRegex(RuntimeError, 'incomplete or different evidence'):
            preparation.validate_result(self.result, self.value, self.job)

    def test_unrecorded_partial_staging_never_starts_a_guest(self):
        (self.work / 'result.slot').write_bytes(b'partial prior staging')
        with self.assertRaisesRegex(RuntimeError, 'unrecorded files'):
            self.run_prepare()
        self.boot.assert_not_called()
        self.retire.assert_not_called()

    def test_uncertain_live_guest_keeps_its_result_loop_and_disk(self):
        with patch.object(preparation, 'domain', side_effect=[None, {'domid':123}, {'domid':123}]):
            with self.assertRaisesRegex(RuntimeError, 'guest remains'):
                self.run_prepare()
        self.assertEqual(self.attached, ['/dev/loop7'])
        self.assertEqual(self.ledger()['stage'], 'booting')
        self.retire.assert_not_called()


if __name__ == '__main__':
    unittest.main()
