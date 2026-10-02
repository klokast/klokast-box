"""Used-backup cleanup binds old recovery to current native retirement."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_router_template_cli import load_cli


class UsedBackupControllerTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.operation, self.engine, self.source = 'a' * 24, 'b' * 40, 'c' * 40
        self.result = self.root / self.operation
        self.result.mkdir(mode=0o700)
        self.request = self.cli.router_generations.seal({
            'kind': 'klokast.router-cold-supervised-request.v1', 'box': 'k001',
            'operation_id': self.operation, 'engine_commit': self.source,
            'metadata_sha256': '1' * 64, 'generation_sha256': '2' * 64,
            'identity_sha256': '3' * 64, 'baseline_sha256': '4' * 64})
        self.completion = self.cli.router_generations.seal({
            'kind': 'klokast.router-cold-completion.v1', 'box': 'k001',
            'operation_id': self.operation, 'engine_commit': self.source,
            **{k: self.request[k] for k in ('metadata_sha256', 'generation_sha256',
                                           'identity_sha256', 'baseline_sha256')}})
        self.device = self.cli.router_generations.seal({
            'kind': 'klokast.router-cold-device-cleanup.v1', 'box': 'k001',
            'operation_id': self.operation, 'engine_commit': self.source,
            'source_sha256': '5' * 64, 'status': 'no-device', 'machine_id': None, 'provider_id': None})
        self.cli.transport.write(self.result / 'cold-supervisor-request.json', self.request)
        self.cli.transport.write(self.result / 'cold-health-clear.json', {
            'kind': 'klokast.router-command-result.v1', 'box': 'k001', 'action': 'cold-health-clear',
            'engine_commit': self.source, 'result': self.completion})
        self.cli.transport.write(self.result / 'cold-device-cleanup.json', self.device)
        self.value = self.cli.router_generations.seal({
            'kind': 'klokast.router-cold-used-backup-retirement.v1', 'box': 'k001',
            'operation_id': self.operation, 'source_engine_commit': self.source,
            'cleanup_engine_commit': self.engine, 'completion_sha256': self.completion['record_sha256'],
            'test_source_sha256': self.device['source_sha256'],
            'device_cleanup_sha256': self.device['record_sha256'], 'intent_sha256': '6' * 64,
            'bytes_reclaimed': 2147483648, 'status': 'used-backup-retired'})
        self.play_calls = []

    def command(self, argv, **kwargs):
        args = [str(item) for item in argv]
        if args[0] == 'git':
            return '' if args[-1] == '--untracked-files=all' else self.engine
        self.play_calls.append(args)
        arguments = self.cli.transport.load(Path(args[-1][1:]))
        self.assertEqual(arguments['router_cold_cleanup_source_engine'], self.source)
        self.assertEqual(arguments['router_cold_cleanup_device_receipt'], self.device)
        self.cli.transport.write(self.result / (arguments['router_cold_cleanup_prefix'] +
            arguments['router_cold_cleanup_token'] + '.json'), {
                'kind': 'klokast.router-command-result.v1', 'box': 'k001', 'action': arguments['router_cold_cleanup_action'],
                'engine_commit': self.engine, 'result': self.value})

    def run_cleanup(self, artifacts_only=False):
        with ExitStack() as stack:
            for target, name, options in (
                    (self.cli, 'STATE', {'new': self.root}),
                    (self.cli.transport, 'require_controller', {}),
                    (self.cli.transport, 'approved_engine', {'return_value': self.engine}),
                    (self.cli.transport, 'installation_lock', {'side_effect': lambda: nullcontext()}),
                    (self.cli.transport, 'command', {'side_effect': self.command})):
                stack.enter_context(mock.patch.object(target, name, **options))
            return self.cli.cleanup_cold_used_backup('k001', self.operation, artifacts_only=artifacts_only)

    def test_current_cleanup_can_bind_completed_historical_source(self):
        result = self.run_cleanup()
        self.assertEqual(result['source_engine_commit'], self.source)
        self.assertEqual(result['cleanup_engine_commit'], self.engine)
        self.assertEqual(result['device_cleanup_sha256'], self.device['record_sha256'])
        self.assertEqual(len(self.play_calls), 1)

    def test_foreign_or_unfinished_device_receipt_refuses_before_native_transport(self):
        for changes in ({'operation_id': 'd' * 24}, {'engine_commit': self.engine}, {'status': 'pending'}):
            device = self.cli.router_generations.seal({**{k: v for k, v in self.device.items()
                                                         if k != 'record_sha256'}, **changes})
            self.cli.transport.write(self.result / 'cold-device-cleanup.json', device)
            with self.subTest(changes=changes), self.assertRaisesRegex(self.cli.UpdateError, 'exact test-device cleanup'):
                self.run_cleanup()
        self.assertEqual(self.play_calls, [])

    def test_wrong_original_completion_refuses_before_native_transport(self):
        completion = self.cli.router_generations.seal({**{k: v for k, v in self.completion.items()
                                                         if k != 'record_sha256'}, 'generation_sha256': 'f' * 64})
        self.cli.transport.write(self.result / 'cold-health-clear.json', {
            'kind': 'klokast.router-command-result.v1', 'box': 'k001', 'action': 'cold-health-clear',
            'engine_commit': self.source, 'result': completion})
        with self.assertRaisesRegex(self.cli.UpdateError, 'saved original'):
            self.run_cleanup()
        self.assertEqual(self.play_calls, [])

    def test_foreign_native_result_or_wrong_byte_count_is_not_completion(self):
        original = dict(self.value)
        for changes in ({'source_engine_commit': self.engine}, {'bytes_reclaimed': 1},
                        {'device_cleanup_sha256': 'f' * 64}, {'completion_sha256': 'f' * 64}):
            self.value = self.cli.router_generations.seal({**{k: v for k, v in original.items()
                                                             if k != 'record_sha256'}, **changes})
            with self.subTest(changes=changes), self.assertRaisesRegex(self.cli.UpdateError, 'differs from exact'):
                self.run_cleanup()

    def test_nonprivate_operation_evidence_refuses(self):
        self.result.chmod(0o755)
        with self.assertRaisesRegex(self.cli.UpdateError, 'private controller evidence'):
            self.run_cleanup()
        self.assertEqual(self.play_calls, [])

    def stage_artifact_cleanup(self):
        self.retirement = dict(self.value)
        self.cli.transport.write(self.result / 'cold-used-backup-retirement.json', self.retirement)
        self.value = self.cli.router_generations.seal({
            'kind': 'klokast.router-cold-used-inspector-cleanup.v1', 'box': 'k001',
            'operation_id': self.operation, 'source_engine_commit': self.source,
            'cleanup_engine_commit': self.engine, 'retirement_sha256': self.retirement['record_sha256'],
            'bootstrap_sha256': '7'*64, 'ownership_sha256': '8'*64,
            'plan_sha256': '9'*64, 'progress_sha256': '0'*64,
            'bytes_reclaimed': 84125866, 'status': 'used-inspector-retired'})

    def test_artifact_cleanup_binds_exact_previous_backup_retirement(self):
        self.stage_artifact_cleanup()
        result = self.run_cleanup(True)
        self.assertEqual(result['retirement_sha256'], self.retirement['record_sha256'])
        self.assertEqual(len(self.play_calls), 1)

    def test_artifact_cleanup_rejects_wrong_previous_retirement_before_transport(self):
        self.stage_artifact_cleanup()
        wrong = self.cli.router_generations.seal({**{k: v for k, v in self.retirement.items() if k != 'record_sha256'},
            'completion_sha256': 'f'*64})
        self.cli.transport.write(self.result / 'cold-used-backup-retirement.json', wrong)
        with self.assertRaisesRegex(self.cli.UpdateError, 'exact completed backup retirement'):
            self.run_cleanup(True)
        self.assertEqual(self.play_calls, [])

    def test_artifact_cleanup_rejects_swapped_retirement_or_unbounded_result(self):
        self.stage_artifact_cleanup()
        original = self.value
        for change in ({'retirement_sha256': 'f'*64}, {'bytes_reclaimed': True},
                       {'bytes_reclaimed': 2**40}, {'ownership_sha256': ''}, {'status': 'pending'}):
            with self.subTest(change=change):
                self.value = self.cli.router_generations.seal({**{k: v for k, v in original.items() if k != 'record_sha256'}, **change})
                with self.assertRaisesRegex(self.cli.UpdateError, 'exact backup retirement'):
                    self.run_cleanup(True)

    def test_backup_cleanup_publishes_stable_exact_retirement_for_artifact_cleanup(self):
        self.run_cleanup()
        self.assertEqual(self.cli.transport.load(self.result / 'cold-used-backup-retirement.json'), self.value)


if __name__ == '__main__':
    unittest.main()
