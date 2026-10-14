"""BAK uses the shared lifecycle without copying or retiring persistent data."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_dmz_reconstruction as shared
import test_vm_service_updates as service_tests
import test_vm_update_transaction as transactions
import dmz_provisioning
import vm_service_updates as updates


class BAKCreation(shared.DMZCreation):
    role = 'bak'


class BAKHealth(shared.DMZHealth):
    role = 'bak'


class BAKRetirement(shared.DMZRetirement):
    role = 'bak'


class BAKFinalization(shared.DMZNativeFinalization):
    role = 'bak'


class BAKTransactions(transactions.PersistentDataTransactions):
    role = 'bak'


class BAKInputs(unittest.TestCase):
    def setUp(self):
        service_tests.FinalizerInputs.setUp(self)
        self.public = {
            'kind': 'klokast.infrastructure-config.v1', 'box': 'boxa', 'role': 'bak',
            'address': '192.168.100.10/24', 'gateway': '192.168.100.254', 'bridge': 'br-bak',
            'bootstrap_source': '192.168.100.1', 'public_key': 'ssh-ed25519 AAAATEST',
            'agent_uid': 1004, 'agent_gid': 1004,
            'data_uuid': '11111111-1111-4111-8111-111111111111',
        }
        self.value.update(bak_creation=self.public, bak_machine_id_sha256=None,
                          bak_storage={'uuid': self.public['data_uuid'],
                                       'initialize': True, 'persistent_before': False})

    def test_backend_configuration_and_frozen_pointer_are_role_bound(self):
        self.assertEqual(self.m.validate(self.value), self.value)
        infrastructure = shared.load('bak_config', shared.ROOT /
            'ansible/roles/vm-template-builder/files/vm_infrastructure_personalize.py')
        pointer = {'input_snapshot': 'a' * 24, 'inputs_sha256': 'b' * 64}
        infrastructure.validate(dict(self.public, bak_inputs=pointer))
        for changes in ({'bridge': 'br-dmz'}, {'dmz_inputs': pointer}, {'iot_inputs': pointer}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                infrastructure.validate(dict(self.public, **changes))
        for changes in ({'role': 'dmz'}, {'data_uuid': '22222222-2222-4222-8222-222222222222'}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.m.validate(dict(self.value, bak_creation=dict(self.public, **changes)))


class BAKUpdateDecisions(unittest.TestCase):
    def setUp(self):
        service_tests.ServiceDecisions.setUp(self)
        self.step.return_value = {'pending': [], 'installed': None, 'present': True}
        self.before.update(root_partition='3', source_files={}, retained_uuid=None,
                           persistent_storage=None, application_writers=[])
        self.probe.return_value = self.before

    @contextlib.contextmanager
    def preparation(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            work = Path(directory)
            (work/'build.lock').touch()
            receipt = {'files': {'inputs.json': {'profile_sha256': updates.digest({})},
                                'release-evidence.json': {'kernel_release': 'test', 'packages': {}}}}
            stack.enter_context(patch.object(updates.images, 'local_action', return_value=receipt))
            stack.enter_context(patch.object(updates.images, 'qualify_profile', return_value={}))
            stack.enter_context(patch.object(updates.vm_local_images, 'validate'))
            stack.enter_context(patch.object(updates.vm_local_images, 'import_receipt'))
            stack.enter_context(patch.object(updates, 'Path', side_effect=lambda value:
                work if str(value) == '/var/lib/klokast/updates/discovery' else Path(value)))
            stack.enter_context(patch.object(updates.platform_source, 'snapshot', return_value={
                'instance': {'boxes': {'boxa': {'routing-profile': {'bootstrap-dns': ['1.1.1.1', '8.8.8.8']}}}}}))
            stack.enter_context(patch.object(updates.routing_profile, 'validate', side_effect=lambda value: value))
            stack.enter_context(patch.object(dmz_provisioning, 'configuration', return_value=(
                {'box': 'boxa', 'role': 'bak'}, {'shared_guest_role': 'bak'})))
            freeze = stack.enter_context(patch.object(updates.inputs, 'freeze', return_value=(work, 'f'*64)))
            finish = stack.enter_context(patch.object(updates, 'finish_frozen', return_value={'state': 'complete'}))
            yield freeze, finish

    def test_first_transition_requires_explicit_discard_before_freezing(self):
        with self.preparation() as (freeze, finish), self.assertRaisesRegex(RuntimeError, '--discard-root-data'):
            updates.execute('boxa', 'bak', image='e'*24)
        freeze.assert_not_called()
        finish.assert_not_called()

    def test_discard_binds_backend_inputs_and_old_workload_evidence(self):
        with self.preparation() as (freeze, finish):
            updates.execute('boxa', 'bak', image='e'*24, discard_root_data=True)
        values = freeze.call_args.args[1]
        config = values['service_configuration']
        self.assertTrue(config['discard_root_data'])
        self.assertEqual(config['before']['workloads'], self.before['workloads'])
        self.assertEqual(config['bak_creation']['role'], 'bak')
        self.assertEqual(values['bak_runtime']['shared_guest_role'], 'bak')
        self.assertEqual(finish.call_count, 1)

    def test_routine_update_retains_data_and_refuses_a_second_discard(self):
        self.before.update(retained_uuid='11111111-1111-4111-8111-111111111111',
                           persistent_storage={'role': 'bak'})
        with self.preparation() as (freeze, finish):
            updates.execute('boxa', 'bak', image='e'*24)
            self.assertFalse(freeze.call_args.args[1]['service_configuration']['discard_root_data'])
            freeze.reset_mock(); finish.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'omit --discard-root-data'):
                updates.execute('boxa', 'bak', image='e'*24, discard_root_data=True)
            freeze.assert_not_called(); finish.assert_not_called()

    def test_writers_block_even_an_authorized_first_transition(self):
        for changes in ({'application_writers': [{'pid': '2', 'path': '/srv/app'}]},
                        {'workloads': {'running': ['active']}}):
            with self.subTest(changes=changes), self.preparation() as (freeze, finish):
                self.probe.return_value = dict(self.before, **changes)
                with self.assertRaisesRegex(RuntimeError, 'no disks were allocated'):
                    updates.execute('boxa', 'bak', image='e'*24, discard_root_data=True)
                freeze.assert_not_called(); finish.assert_not_called()

    def test_discard_cannot_be_added_to_resume_or_retirement(self):
        for options in ({'resume': 'a'*24}, {'retire': True}, {'abandon': True}):
            with self.subTest(options=options), self.assertRaisesRegex(RuntimeError, 'recorded decision'):
                updates.execute('boxa', 'bak', discard_root_data=True, **options)

    def test_explicit_rebuild_is_available_for_unchanged_backend_image(self):
        self.step.return_value['installed'] = copy.deepcopy(self.record)
        with patch.object(updates.images, 'local_action', side_effect=RuntimeError('prepare reached')):
            with self.assertRaisesRegex(RuntimeError, 'prepare reached'):
                updates.execute('boxa', 'bak', rebuild=True)

    def test_selected_image_rebuild_checks_receipt_without_preparing_an_image(self):
        self.step.return_value['installed'] = copy.deepcopy(self.record)
        self.before['persistent_storage'] = {'role': 'bak'}
        with self.preparation() as (freeze, finish):
            updates.execute('boxa', 'bak', image=self.record['image'], rebuild=True)
            updates.images.local_action.assert_called_once_with(
                'boxa', 'shared-alpine-v1', 'c'*40, 'image-receipt', self.record['image'])
        self.assertEqual(freeze.call_args.args[1]['service_image'], self.record['image'])
        self.assertFalse(freeze.call_args.args[1]['service_configuration']['discard_root_data'])
        finish.assert_called_once()

    def test_rebuild_cannot_change_resume_or_retirement_inputs(self):
        for options in ({'resume': 'a'*24}, {'retire': True}):
            with self.subTest(options=options), self.assertRaisesRegex(RuntimeError, 'cannot accompany'):
                updates.execute('boxa', 'bak', rebuild=True, **options)
        self.authority.assert_not_called(); self.step.assert_not_called()

    def test_cli_accepts_an_explicit_image_for_rebuild(self):
        cli = shared.load('bak_update_cli', shared.ROOT/'ansible/bin/platform-update')
        with patch.object(cli.platform_proxy, 'environment', return_value={}), \
                patch.object(updates, 'execute', return_value={'state': 'complete'}) as execute, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['update', '--box', 'boxa', '--role', 'bak',
                                      '--image', 'a'*24, '--rebuild']), 0)
        self.assertEqual(execute.call_args.kwargs['image'], 'a'*24)
        self.assertTrue(execute.call_args.kwargs['rebuild'])

    def test_backend_retirement_uses_the_protected_service_dispatch(self):
        updates.execute('boxa', 'bak', retire=True, dry_run=True)
        self.assertEqual([call.args[2] for call in self.step.call_args_list], ['status', 'retire-plan'])

    def test_stopped_backend_skips_before_inspection_or_discard(self):
        self.authority.return_value = 'stopped'
        self.assertEqual(updates.execute('boxa', 'bak', rebuild=True, discard_root_data=True)['state'], 'skipped')
        self.step.assert_not_called(); self.probe.assert_not_called()
