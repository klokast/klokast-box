"""The controller aborts only an unlaunched K001 cold preparation."""
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_router_template_cli import load_cli


class ControllerAbortTests(unittest.TestCase):
    def test_bootstrap_cleanup_selects_separate_native_action_and_checks_its_closed_receipt(self):
        cli=load_cli();operation,engine='a'*24,'b'*40
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);result=root/operation;result.mkdir(mode=0o700)
            value=cli.router_generations.seal({'kind':'klokast.router-cold-bootstrap-cleanup.v1','box':'k001',
                'operation_id':operation,'source_engine_commit':'c'*40,'cleanup_engine_commit':engine,
                'abort_sha256':'d'*64,'bootstrap_sha256':'e'*64,'plan_sha256':'f'*64,
                'progress_sha256':'1'*64,'bytes_reclaimed':100,'status':'unused-bootstrap-retired'})
            def command(argv,**kwargs):
                args=[str(item) for item in argv]
                if args[0]=='git':return '' if args[-1]=='--untracked-files=all' else engine
                arguments=cli.transport.load(Path(args[-1][1:]))
                self.assertTrue(arguments['router_cold_abort_artifacts_only'])
                cli.transport.write(result/('cold-prepared-abort-'+arguments['router_cold_abort_token']+'.json'),
                    {'kind':'klokast.router-command-result.v1','box':'k001','action':'cold-retire-prepared-bootstrap',
                     'engine_commit':engine,'result':value})
            with mock.patch.object(cli,'STATE',root),mock.patch.object(cli.transport,'require_controller'),\
                    mock.patch.object(cli.transport,'approved_engine',return_value=engine),\
                    mock.patch.object(cli.transport,'installation_lock',side_effect=lambda:nullcontext()),\
                    mock.patch.object(cli.transport,'command',side_effect=command):
                self.assertEqual(cli.abort_cold_prepared('k001',operation,artifacts_only=True)['status'],'unused-bootstrap-retired')
                value=cli.router_generations.seal({**{k:v for k,v in value.items() if k!='record_sha256'},'bytes_reclaimed':2**40})
                with self.assertRaisesRegex(cli.UpdateError,'bounded exact'):cli.abort_cold_prepared('k001',operation,artifacts_only=True)

    def test_exact_abort_receipt_and_no_launch_guard(self):
        cli = load_cli()
        operation, engine = 'a' * 24, 'b' * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = root / operation
            result.mkdir(mode=0o700)
            value = cli.router_generations.seal({
                'kind': 'klokast.router-cold-prepared-abort.v1', 'box': 'k001',
                'operation_id': operation, 'source_engine_commit': 'c' * 40,
                'cleanup_engine_commit': engine, 'intent_sha256': 'd' * 64,
                'status': 'retired'})
            calls = []

            def command(argv, **kwargs):
                args = [str(item) for item in argv]
                if args[:3] == ['git', '-C', str(cli.REPO)]:
                    return '' if args[-1] == '--untracked-files=all' else engine
                calls.append(args)
                arguments = cli.transport.load(Path(args[-1][1:]))
                receipt = {'kind': 'klokast.router-command-result.v1', 'box': 'k001',
                           'action': 'cold-abort-prepared', 'engine_commit': engine,
                           'result': value}
                cli.transport.write(result / ('cold-prepared-abort-' +
                                    arguments['router_cold_abort_token'] + '.json'), receipt)

            with mock.patch.object(cli, 'STATE', root), \
                 mock.patch.object(cli.transport, 'require_controller'), \
                 mock.patch.object(cli.transport, 'approved_engine', return_value=engine), \
                 mock.patch.object(cli.transport, 'installation_lock', return_value=nullcontext()), \
                 mock.patch.object(cli.transport, 'command', side_effect=command):
                (result / 'cold-supervisor-request-stage.json').touch()
                self.assertEqual(cli.abort_cold_prepared('k001', operation)['status'], 'retired')
                self.assertEqual(len(calls), 1)
                (result / 'cold-supervisor-launch.json').touch()
                with self.assertRaisesRegex(cli.UpdateError, 'no launched outage'):
                    cli.abort_cold_prepared('k001', operation)
                self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
