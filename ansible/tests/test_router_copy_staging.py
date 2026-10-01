"""Retained copy staging preserves identity and validates real transported bytes."""
from contextlib import ExitStack, nullcontext
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_copy_inputs as inputs
import router_executor as executor
import router_generations as generations
import router_records as records
from platform_updates import UpdateError
from router_transaction import TransactionError
import test_router_copy_cache as cache_tests
from test_router_template_cli import load_cli


class CopyStagingTests(unittest.TestCase):
    def setUp(self):
        cache_tests.CopyCacheTests.setUp(self)
        self.controller = self.base / 'controller'
        self.controller.mkdir(mode=0o700)
        self.source = self.controller / 'source'
        self.source.mkdir(mode=0o700)
        self.manifest = {'inputs_sha256':'e'*64}
        (self.source / 'inputs.json').write_text(json.dumps(self.manifest))
        self.retained = self.controller / 'copy-capsule'
        self.build = mock.Mock(side_effect=self.bootstrap)
        for target, name, value in ((inputs.router_updates, 'validate_inputs', mock.Mock()),
                                   (inputs.vm_template_inputs, 'verify_inputs', mock.Mock()),
                                   (inputs.vm_template_inputs, 'bootstrap', self.build)):
            patch = mock.patch.object(target, name, value)
            patch.start(); self.addCleanup(patch.stop)
        self.repo = Path(__file__).resolve().parents[2]

    def bootstrap(self, source, boot, guest, **kwargs):
        self.assertEqual(source, self.source)
        self.assertEqual(guest.name, 'router-copy-transaction-guest')
        files = kwargs['job_files']
        self.assertEqual(set(files), {'router-copy-job.json',
            'usr/local/lib/klokast/router_state.py', 'usr/local/libexec/router-state-copy-guest'})
        self.assertEqual(inputs.load(files['router-copy-job.json']),
            inputs.job(self.request, self.old, self.new, 'e'*64))
        boot.mkdir(mode=0o700)
        result = {}
        for name in ('kernel', 'initramfs'):
            raw = ('copy-test-' + name).encode()
            (boot / name).write_bytes(raw)
            result[name] = {'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest()}
        return result

    def retain(self, **kwargs):
        return inputs.retain(self.source, self.retained, self.repo,
                             self.request, self.old, self.new, **kwargs)

    def test_build_once_then_reuse_exact_domain_ids_and_boot_bytes(self):
        first = self.retain()
        self.assertEqual(self.retain(allow_build=False), first)
        self.build.assert_called_once()
        for name in ('kernel', 'initramfs'):
            self.assertEqual((self.retained / 'boot' / name).stat().st_mode & 0o777, 0o600)
        (self.retained / 'boot/kernel').write_bytes(b'changed')
        with self.assertRaisesRegex(UpdateError, 'boot bytes changed'):
            self.retain()
        self.build.assert_called_once()

    def test_launch_cannot_build_and_partial_build_cannot_regenerate_identities(self):
        with self.assertRaisesRegex(UpdateError, 'no rebuild'):
            self.retain(allow_build=False)
        self.build.assert_not_called()
        self.build.side_effect = RuntimeError('assembly lost')
        with self.assertRaisesRegex(RuntimeError, 'assembly lost'):
            self.retain()
        with self.assertRaisesRegex(UpdateError, 'incomplete'):
            self.retain()
        self.build.assert_called_once()

    def test_unexpected_files_and_missing_native_boot_are_preserved_for_reconciliation(self):
        value = self.retain()
        unknown = self.retained / 'unexpected'
        unknown.write_bytes(b'preserve')
        with self.assertRaisesRegex(UpdateError, 'unexpected files'):
            self.retain()
        self.assertEqual(unknown.read_bytes(), b'preserve')
        unknown.unlink()
        records.write(self.work / 'capsule.json', value)
        for name in ('kernel', 'initramfs'):
            shutil.copyfile(self.retained / 'boot' / name, self.directory / name)
        (self.directory / 'kernel').unlink()
        with self.assertRaises(FileNotFoundError):
            self.transport.verify_boot(self.adapter, deadline=self.adapter.monotonic() + 10)
        self.assertIsNone(self.records.pending())
        self.build.assert_called_once()

    def test_changed_source_job_pair_or_unsafe_paths_refuse_without_rebuilding(self):
        self.retain()
        for field, value in (('inputs_sha256', '0'*64), ('transaction_sha256', '0'*64)):
            record = inputs.load(self.retained / 'capsule.json')
            changed = {**record, field:value}
            inputs.write(self.retained / 'capsule.json', changed)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.retain()
            inputs.write(self.retained / 'capsule.json', record)
        inputs.write(self.source / 'inputs.json', {'inputs_sha256':'0'*64})
        with self.assertRaisesRegex(UpdateError, 'frozen source'):
            self.retain()
        inputs.write(self.source / 'inputs.json', self.manifest)
        saved_job = inputs.load(self.retained / 'copy-job.json')
        inputs.write(self.retained / 'copy-job.json', {'changed':True})
        with self.assertRaisesRegex(UpdateError, 'frozen source or job'):
            self.retain()
        inputs.write(self.retained / 'copy-job.json', saved_job)
        (self.retained / 'boot/kernel').chmod(0o644)
        with self.assertRaisesRegex(UpdateError, 'private metadata'):
            self.retain()
        self.build.assert_called_once()

    def test_root_stage_verifies_actual_capsule_and_boot_without_allocating_slots(self):
        value = self.retain()
        records.write(self.work / 'capsule.json', value)
        records.write(self.work / 'proposed-generation.json', self.new)
        for name in ('kernel', 'initramfs'):
            shutil.copyfile(self.retained / 'boot' / name, self.directory / name)
        for name in [*cache_tests.native_copy.SLOTS, 'allocation.json']:
            (self.directory / name).unlink()
        self.copy.verify_boot = self.transport.verify_boot
        result = executor.stage_cutover(self.records, self.request['operation_id'], self.request['engine_commit'])
        self.assertEqual(result['kind'], 'klokast.router-cutover-staged.v2')
        self.assertEqual(result['capsule_sha256'], generations.digest(value))
        self.assertIsNone(self.records.pending())
        self.assertEqual({p.name for p in self.directory.iterdir()}, {'kernel','initramfs'})
        (self.directory / 'kernel').write_bytes(b'changed')
        with self.assertRaises(TransactionError):
            executor.stage_cutover(self.records, self.request['operation_id'], self.request['engine_commit'])

    def test_controller_stage_produces_launch_records_and_root_verified_copy_boot(self):
        cli = load_cli()
        operation, diagnostic = self.request['operation_id'], 'c'*24
        cache, state = self.controller / 'cache', self.controller / 'state'
        cache.mkdir(mode=0o700); state.mkdir(mode=0o700)
        work, result = cache / ('replacement-' + operation), state / operation
        diag, proof = cache / ('compatibility-' + diagnostic), state / diagnostic
        for directory in (work, result, diag, proof):
            directory.mkdir(mode=0o700)
        self.request.update(cutover_seconds=1800, recovery_seconds=900)
        self.adapter.request = self.request.copy()
        self.adapter.ready['request_sha256'] = generations.digest(self.request)
        records.write(self.work / 'transaction-request.json', self.request)
        records.write(self.work / 'readiness.json', self.adapter.ready)
        prepared = records.read(self.work / 'preparation-result.json')
        assignment = self.records.accepted()
        preparation = {'accepted_assignment_sha256':assignment['record_sha256'],
            'old_sha256':self.old['record_sha256'], 'check_operation':'b'*24,
            'inputs_sha256':'e'*64}
        documents = {work / 'request.json':preparation, work / 'release.json':{},
            result / 'proposed-generation.json':self.new,
            result / 'candidate-preflight.json':self.candidate_preflight,
            result / 'preparation-result.json':prepared,
            diag / 'request.json':{'source':{'accepted':{}}},
            proof / 'result.json':{}, proof / 'lifecycle.json':{}, proof / 'candidate-disk.json':{}}
        import router_replacement_qualification as qualification
        for phase in qualification.PHASES:
            documents[proof / (phase + '.json')] = {}
        for path, value in documents.items():
            cli.transport.write(path, value)
        context = dict.fromkeys(('report', 'binding', 'accepted', 'accepted_profile',
                                 'policy', 'overlay_source'), {})
        context.update(assignment=assignment, generation=self.old, frozen=self.source,
            inputs=self.manifest, policy_sha256=self.request['policy_sha256'], old_machine_id='nOldRouter')
        self.copy.verify_boot = self.transport.verify_boot
        (self.work / 'capsule.json').unlink()
        (self.work / 'enrollment-source.json').unlink()
        calls = []
        def command(argv, **kwargs):
            if argv[0] == 'git':
                return self.request['engine_commit'] if 'rev-parse' in argv else ''
            if argv[0] == 'ansible-inventory':
                return json.dumps({'router_backend_ipv4_address':'10.1.1.1'})
            self.assertEqual(argv[0], 'ansible-playbook')
            arguments = cli.transport.load(str(argv[-1])[1:])
            staged = arguments['router_replacement_records']
            self.assertEqual(set(staged), {'transaction-request','readiness','compatibility',
                'copy-qualification','enrollment-source','capsule'})
            for name, value in staged.items():
                target = self.work / (name + '.json')
                if target.exists():
                    self.assertEqual(records.read(target), value)
                else:
                    records.write(target, value)
            records.write(self.work / 'proposed-generation.json', self.new)
            copy_dir = Path(arguments['router_replacement_copy_dir'])
            for name in ('kernel', 'initramfs'):
                shutil.copyfile(copy_dir / 'boot' / name, self.directory / name)
            native = executor.stage_cutover(self.records, operation, self.request['engine_commit'])
            cli.transport.write(result / 'cutover-stage-result.json', {
                'kind':'klokast.router-command-result.v1', 'action':'stage-cutover',
                'box':'boxa', 'engine_commit':self.request['engine_commit'], 'result':native})
            calls.append(native)
            return ''
        with ExitStack() as stack:
            for target, name, value in ((cli, 'CACHE', cache), (cli, 'STATE', state),
                (cli.transport, 'require_controller', mock.Mock()),
                (cli.transport, 'approved_engine', mock.Mock(return_value=self.request['engine_commit'])),
                (cli.transport, 'installation_lock', lambda:nullcontext()),
                (cli.transport, 'command', command),
                (cli, 'replacement_context', mock.Mock(return_value=context)),
                (qualification, 'records', mock.Mock(return_value=(
                    self.compatibility, self.copy_qualification, self.adapter.ready)))):
                stack.enter_context(mock.patch.object(target, name, value))
            first = cli.stage_replacement_cutover('boxa', operation, diagnostic)
            self.assertEqual(cli.stage_replacement_cutover('boxa', operation, diagnostic), first)
            self.build.assert_called_once()
            self.assertEqual(calls[0], calls[1])
            for name in ('transaction-request','readiness','enrollment-source','capsule'):
                self.assertEqual(cli.transport.load(result / (name + '.json')),
                                 records.read(self.work / (name + '.json')))
            cli.transport.write(result / 'transaction-request.json', {'changed':True})
            with self.assertRaisesRegex(UpdateError, 'controller cutover record changed'):
                cli.stage_replacement_cutover('boxa', operation, diagnostic)
            self.assertEqual(len(calls), 2)
        self.assertIsNone(self.records.pending())


if __name__ == '__main__':
    unittest.main()
