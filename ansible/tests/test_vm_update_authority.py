"""Signed policy authority, root-owned evidence and safe pause controls."""
import copy
import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_instance_verification as verification_fixture
import platform_updates as release_contract


class VMUpdateAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.fixture = verification_fixture.InstanceVerificationTest()
        self.fixture.setUp()
        self.m = self.fixture.m
        lock_home = tempfile.TemporaryDirectory()
        self.addCleanup(lock_home.cleanup)
        lock_path = Path(lock_home.name) / 'operation.lock'
        lock_path.touch()
        lock_path.chmod(0o660)
        self.lock_path = lock_path
        lock_location = patch.object(self.m, 'VM_UPDATE_LOCK', lock_path)
        lock_location.start()
        self.addCleanup(lock_location.stop)
        lock_group = patch.object(self.m, 'smith_gid', return_value=0)
        lock_group.start()
        self.addCleanup(lock_group.stop)
        # Test real files and flock as an unprivileged runner. Mock only the
        # root identity checks; do not weaken the production executor.
        self.directory_check = patch.object(self.m, 'ensure_protected_dir', side_effect=lambda p, mode, group: self.m.ensure_dir(p, mode))
        self.directory_check.start()
        self.addCleanup(self.directory_check.stop)
        original_fstat = self.m.os.fstat
        def root_fstat(descriptor):
            value = list(original_fstat(descriptor))
            value[4] = value[5] = 0
            return self.m.os.stat_result(value)
        self.lock_identity = patch.object(self.m.os, 'fstat', side_effect=root_fstat)
        self.lock_identity.start()
        self.addCleanup(self.lock_identity.stop)
        self.collected = self.fixture.collected()
        self.collected["declared_boxes"] = ["boxa", "boxb", "boxc"]
        self.policy = {"enabled": True, "targets": {"boxa": ["bak", "dmz"], "boxc": ["iot"]}, "exclusions": [],
                       "branch-policy": "tested-stable", "maintenance-window": {"start":"02:00", "end":"04:00", "last-start":"03:00"},
                       "canary-hours": 24, "replacement-minutes": 30, "recovery-minutes": 30}

    def intent(self):
        return self.m.vm_update_intent(self.collected, self.policy, "vm-update-test-nonce", self.m.now_utc())

    def test_closed_intent_and_policy_do_not_expand_apply(self):
        m = self.m
        intent = self.intent()
        m.validate_vm_update_intent(intent)
        with self.assertRaises(m.ApplyError): m.validate_verification_intent(intent)
        for change in (
            lambda v: v.update(command="xl destroy bak"),
            lambda v: v.update(executor="shell"),
            lambda v: v["policy"].update(enabled=False),
            lambda v: v["policy"].update(**{"canary-hours":23}),
            lambda v: v["policy"].update(**{"replacement-minutes":True}),
            lambda v: v["policy"]["targets"].update(boxa=["ops"]),
            lambda v: v["policy"]["targets"].update(boxa=["router"]),
            lambda v: v["policy"]["targets"].update(unknown=["bak"]),
            lambda v: v.update(policy_sha256="0"*64),
            lambda v: v.update(declared_boxes=["boxa"]),
            lambda v: v["policy"]["exclusions"].append({"box":"boxb", "role":"bak", "reason":"repair"}),
        ):
            bad=copy.deepcopy(intent); change(bad)
            with self.subTest(change=change), self.assertRaises(m.ApplyError): m.validate_vm_update_intent(bad)

    def test_expiry_is_checked_for_execution_and_not_extended(self):
        m=self.m
        intent=m.vm_update_intent(self.collected,self.policy,"vm-update-test-nonce",m.now_utc()-dt.timedelta(hours=2))
        with self.assertRaises(m.ApplyError): m.validate_vm_update_intent(intent)
        m.validate_vm_update_intent(intent,check_time=False)

    def prepare_files(self,root,intent):
        directory=root/'pre'/intent['nonce']; directory.mkdir(parents=True)
        for name,value in [('intent',intent),('binding',self.collected['binding'])]:
            (directory/(name+'.json')).write_text(self.m.canonical(value)+'\n')
        signature=root/'signature.sig'; signature.write_text('test-signature')
        return SimpleNamespace(approval_signature=signature, signer_id=self.m.SIGNER_ID)

    def test_activation_consumes_nonce_publishes_only_receipt_and_refuses_replay(self):
        m=self.m; intent=self.intent()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); args=self.prepare_files(root,intent)
            def collect(*args):
                self.assertTrue((root/'nonces'/intent['nonce']).exists())
                return self.collected,self.policy
            with patch.object(m,'VM_UPDATE_ROOT',root/'executor'), patch.object(m,'PREFLIGHT_ROOT',root/'pre'), patch.object(m,'NONCE_ROOT',root/'nonces'), patch.object(m,'verify_signature'), patch.object(m,'vm_update_collect',side_effect=collect), patch.object(m,'append_audit'), patch.object(m,'publish_authority') as general, redirect_stdout(io.StringIO()):
                m.vm_update_policy_execute(args,intent)
                pointer=(root/'executor/active-policy').read_text().strip()
                receipt=json.loads((root/'executor/activations'/(pointer+'.json')).read_text())
                self.assertEqual(receipt['intent']['policy'],self.policy)
                self.assertEqual(receipt['binding'],self.collected['binding'])
                self.assertEqual((root/'executor/active-policy').stat().st_mode & 0o777,0o600)
                general.assert_not_called()
                with self.assertRaisesRegex(m.ApplyError,'already used'):m.vm_update_policy_execute(args,intent)

    def test_changed_evidence_leaves_no_active_authority(self):
        m=self.m; intent=self.intent()
        changed=copy.deepcopy(self.collected); changed['engine_commit']='d'*40
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); args=self.prepare_files(root,intent)
            with patch.object(m,'VM_UPDATE_ROOT',root/'executor'), patch.object(m,'PREFLIGHT_ROOT',root/'pre'), patch.object(m,'NONCE_ROOT',root/'nonces'), patch.object(m,'verify_signature'), patch.object(m,'vm_update_collect',return_value=(changed,self.policy)):
                with self.assertRaisesRegex(m.ApplyError,'changed'):m.vm_update_policy_execute(args,intent)
                self.assertFalse((root/'executor/active-policy').exists())
                self.assertTrue((root/'nonces'/intent['nonce']).exists())

    def test_pause_can_restrict_revoked_policy_but_resume_cannot(self):
        m=self.m
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch.object(m,'VM_UPDATE_ROOT',root/'executor'), patch.object(m,'require_root_active'), patch.object(m,'require_self_match'), patch.object(m,'append_audit'), patch.object(m,'vm_update_policy_current',side_effect=m.ApplyError('revoked')), redirect_stdout(io.StringIO()):
                m.vm_update_policy_control('pause')
                with self.assertRaisesRegex(m.ApplyError,'revoked'):m.vm_update_policy_control('resume')
                self.assertTrue(json.loads((root/'executor/pause.json').read_text())['paused'])

    def test_source_reader_requires_current_signed_policy_and_bound_resume(self):
        m = self.m
        intent = self.intent()
        receipt = {'intent': intent, 'receipt_sha256': 'f' * 64}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'executor'
            root.mkdir()
            with patch.object(m, 'VM_UPDATE_ROOT', root), patch.object(m, 'require_root_active'), \
                    patch.object(m, 'require_self_match'), \
                    patch.object(m, 'vm_update_policy_current', return_value=receipt):
                status = m.vm_update_policy_source_status()
                self.assertEqual(status['kind'], 'klokast.vm-update-policy-source.v1')
                self.assertEqual(status['policy'], self.policy)
                self.assertEqual(status['activation_sha256'], 'f' * 64)
                self.assertFalse(status['paused'])
                for activation, paused in (('0' * 64, True), ('f' * 64, False)):
                    value = {'paused': False, 'changed_at': m.format_utc(m.now_utc()),
                             'activation_sha256': activation}
                    (root / 'pause.json').write_text(m.canonical(value) + '\n')
                    self.assertEqual(m.vm_update_policy_source_status()['paused'], paused)
                value['paused'] = 'false'
                (root / 'pause.json').write_text(m.canonical(value) + '\n')
                with self.assertRaisesRegex(m.ApplyError, 'pause record'):
                    m.vm_update_policy_source_status()

    def test_source_reader_cli_accepts_no_caller_evidence(self):
        m = self.m
        with patch.object(m, 'vm_update_policy_source_status', return_value={'paused': True}) as source, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(['vm-update-policy', 'source-status']), 0)
            self.assertEqual(m.main(['vm-update-policy', 'source-status', '--plan', 'fake']), 1)
        source.assert_called_once()

    def test_current_policy_revalidates_engine_toolchain_signer_and_revocation(self):
        m=self.m; intent=self.intent()
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root=Path(temporary); args=self.prepare_files(root,intent)
            for name,value in [('VM_UPDATE_ROOT',root/'executor'),('PREFLIGHT_ROOT',root/'pre'),('NONCE_ROOT',root/'nonces'),('INSTANCE',root/'instance')]:
                stack.enter_context(patch.object(m,name,value))
            signature_check=stack.enter_context(patch.object(m,'verify_signature'))
            stack.enter_context(patch.object(m,'vm_update_collect',return_value=(self.collected,self.policy)))
            stack.enter_context(patch.object(m,'append_audit'))
            with redirect_stdout(io.StringIO()):m.vm_update_policy_execute(args,intent)
            controller={'source':'instance_specification_v1','engine_commit':intent['engine_commit'],
                        'controllers':intent['controller_pair'],'authority_state_sha256':intent['evidence']['authority_state_sha256']}
            stack.enter_context(patch.object(m,'controller_identity_status',return_value=controller))
            stack.enter_context(patch.object(m,'resolve_build_directory',return_value=(root/'build',intent['engine_commit'])))
            stack.enter_context(patch.object(m,'verify_build_directory',return_value=({'binary_sha256':intent['evidence']['binary_sha256']},root/'binary')))
            stack.enter_context(patch.object(m,'verify_binary_version'))
            toolchain=stack.enter_context(patch.object(m,'validate_toolchain'))
            m.INSTANCE.mkdir()
            private=m.INSTANCE/'klokast-instance.json'
            def source():
                return {'rendered':{'inputs':[{'path':'klokast-instance.json','sha256':m.sha256_bytes(private.read_bytes())}]}}
            stack.enter_context(patch.object(m,'inventory_source_status',side_effect=source))
            private.write_text(json.dumps({'vm-updates':self.policy}))
            self.assertEqual(m.vm_update_policy_current()['intent'],intent)
            changed=copy.deepcopy(self.policy); changed['enabled']=False
            private.write_text(json.dumps({'vm-updates':changed}))
            with self.assertRaisesRegex(m.ApplyError,'revoked'):m.vm_update_policy_current()
            private.write_text(json.dumps({'vm-updates':self.policy}))
            controller['engine_commit']='d'*40
            with self.assertRaisesRegex(m.ApplyError,'engine changed'):m.vm_update_policy_current()
            controller['engine_commit']=intent['engine_commit']
            toolchain.side_effect=m.ApplyError('toolchain changed')
            with self.assertRaisesRegex(m.ApplyError,'toolchain changed'):m.vm_update_policy_current()
            toolchain.side_effect=None
            signature_check.side_effect=m.ApplyError('signer revoked')
            with self.assertRaisesRegex(m.ApplyError,'signer revoked'):m.vm_update_policy_current()

    def test_policy_storage_does_not_overwrite_immutable_evidence(self):
        m=self.m
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'receipt.json'
            m.vm_update_store(path,b'first')
            with self.assertRaises(FileExistsError):m.vm_update_store(path,b'second')
            self.assertEqual(path.read_bytes(),b'first')

    def test_duplicate_operations_share_one_installation_lock(self):
        m=self.m
        with m.vm_update_lock():
            with self.assertRaisesRegex(m.ApplyError,'installation lock'):
                with m.vm_update_lock():pass
        self.lock_path.chmod(0o600)
        with self.assertRaisesRegex(m.ApplyError,'unsafe metadata'):
            with m.vm_update_lock():pass

    def test_unknown_cli_controls_accept_no_evidence(self):
        m=self.m
        with patch.object(m,'vm_update_policy_control') as control, redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(['vm-update-policy','status','--plan','fake']),1)
            control.assert_not_called()

    def release_fixture(self, root):
        m = self.m
        operation = 'f' * 24
        policy = {**self.policy, 'targets': {'k001': ['dmz'], 'k002': ['dmz', 'iot']}}
        selection = {'kind': 'klokast.vm-update-auto-selection.v1', 'branch': 'v3.24',
                     'build_box': 'k001', 'targets': ['k001-dmz', 'k002-dmz', 'k002-iot'],
                     'policy_sha256': m.inventory_digest(policy),
                     'activation_sha256': 'b' * 64, 'engine_commit': 'c' * 40}
        manifest = [{'name': name, 'version': '1-r0', 'origin': name, 'architecture': 'x86_64',
                     'file': 'packages/' + name + '-1-r0.apk', 'bytes': 100, 'sha256': 'd' * 64}
                    for name in ('linux-virt', 'podman', 'tailscale')]
        inputs = {'kind': 'klokast.vm-template-inputs.v1', 'engine_commit': 'c' * 40,
                  'profile': 'shared-alpine-v1', 'branch': 'v3.24', 'architecture': 'x86_64',
                  'packages': manifest}
        inputs['inputs_sha256'] = release_contract.digest(inputs)
        normal = {'success': True, 'tests': dict.fromkeys(release_contract.OPENRC_TESTS, True)}
        personalized = {'success': True, 'tests': dict.fromkeys(release_contract.PERSONALIZED_TESTS, True)}
        maintenance = {'success': True}
        candidate = {'kind': 'klokast.vm-template-candidate.v1', 'operation_id': operation,
                     'box': 'k001', 'success': True, 'accepted': False,
                     'inputs_sha256': inputs['inputs_sha256'],
                     'tests': dict.fromkeys(release_contract.BASE_BUILD_TESTS, True),
                     'artifacts': {name: {'sha256': 'e' * 64, 'bytes': 100}
                                   for name in ('root', 'kernel', 'initramfs')},
                     'kernel_release': '6.18-virt', 'modules_release': '6.18-virt',
                     'boot_test': {'success': True,
                                   'tests': dict.fromkeys(release_contract.BASE_BOOT_TESTS, True),
                                   'openrc_test': normal, 'personalized_test': personalized,
                                   'maintenance_restore': maintenance}}
        release = release_contract.no_application_release(inputs, candidate, normal,
                                                          personalized, maintenance)
        directory = root / 'builds' / operation
        directory.mkdir(parents=True)
        records = {'automatic.json': {'kind': 'klokast.vm-update-auto-build.v1',
                                      'selection': selection, 'operation_id': operation,
                                      'inputs_sha256': inputs['inputs_sha256'],
                                      'release_sha256': release['release_sha256']}}
        for name, value in records.items():
            (root / name).write_text(m.canonical(value) + '\n')
        for name, value in [('inputs.json', inputs), ('candidate.json', candidate),
                            ('release-evidence.json', release),
                            ('transfer-k002.json', {'kind': 'klokast.vm-template-transfer.v1',
                                                    'operation_id': operation, 'source_box': 'k001',
                                                    'target_box': 'k002', 'artifacts': candidate['artifacts'],
                                                    'accepted': False})]:
            (directory / name).write_text(m.canonical(value) + '\n')
        policy_receipt = {'receipt_sha256': 'b' * 64,
                          'intent': {'policy': policy, 'policy_sha256': selection['policy_sha256'],
                                     'engine_commit': selection['engine_commit']}}
        return operation, policy_receipt, release

    def test_protected_release_import_binds_exact_build_and_is_idempotent(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation, policy, release = self.release_fixture(root / 'discovery')
            with patch.object(m, 'VM_UPDATE_DISCOVERY', root / 'discovery'), \
                    patch.object(m, 'VM_UPDATE_ROOT', root / 'executor'), \
                    patch.object(m, 'vm_update_release_contract', return_value=release_contract), \
                    patch.object(m, 'vm_update_policy_current', return_value=policy), \
                    patch.object(m, 'vm_update_pause_state', return_value=False), \
                    patch.object(m, 'require_root_active'), patch.object(m, 'require_self_match'), \
                    patch.object(m, 'append_audit'), redirect_stdout(io.StringIO()) as output:
                m.vm_update_release_import(operation)
                record_path = root / 'executor/releases' / policy['receipt_sha256'] / (release['release_sha256'] + '.json')
                record = json.loads(record_path.read_text())
                self.assertEqual(record['release']['package_manifest'], release['package_manifest'])
                self.assertEqual(record['release']['application_tests'], {'status': 'not-run', 'executed': False})
                self.assertEqual(record['record_sha256'], m.inventory_digest({k: v for k, v in record.items()
                                                                              if k != 'record_sha256'}))
                m.vm_update_release_import(operation)
                self.assertIn('"result":"unchanged"', output.getvalue())
                (root / 'discovery/builds' / operation / 'release-evidence.json').write_text(
                    m.canonical({**release, 'package_manifest': []}) + '\n')
                with self.assertRaisesRegex(m.ApplyError, 'release evidence is invalid'):
                    m.vm_update_release_import(operation)
                self.assertEqual(json.loads(record_path.read_text()), record)

    def test_protected_release_rejects_wrong_policy_and_transfer(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation, policy, _release = self.release_fixture(root)
            with patch.object(m, 'VM_UPDATE_DISCOVERY', root), \
                    patch.object(m, 'vm_update_release_contract', return_value=release_contract):
                excluded = copy.deepcopy(policy)
                excluded['intent']['policy']['exclusions'] = [{'box': 'k002', 'role': 'iot', 'reason': 'test'}]
                with self.assertRaisesRegex(m.ApplyError, 'outside current standing policy'):
                    m.vm_update_release_evidence(operation, excluded)
                transfer = root / 'builds' / operation / 'transfer-k002.json'
                value = json.loads(transfer.read_text())
                value['accepted'] = True
                transfer.write_text(m.canonical(value) + '\n')
                with self.assertRaisesRegex(m.ApplyError, 'transfer evidence is incomplete'):
                    m.vm_update_release_evidence(operation, policy)


if __name__ == '__main__':unittest.main()
