"""The accepted-source reader accepts every closed controller receipt name."""
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from test_router_template_cli import load_cli


ROOT = Path(__file__).resolve().parents[2]


class AcceptedSourceContractTests(unittest.TestCase):
    def test_playbook_output_names_cover_cold_and_replacement_readers(self):
        playbook = (ROOT / 'ansible/playbooks/74-router-accepted-source.yml').read_text()
        line = next(line for line in playbook.splitlines()
                    if 'router_check_output is match' in line)
        pattern = re.search(r"match\('([^']+)'\)", line).group(1)
        for name in ('accepted-source.json', 'accepted-source-final.json',
                     'cold-accepted-source.json', 'replacement-source.json',
                     'replacement-source-final.json',
                     'replacement-after-stage-abcdef-accepted.json',
                     'cutover-run-abcdef-accepted-final.json'):
            self.assertRegex(name, pattern)
        for name in ('../accepted-source.json', 'arbitrary.json',
                     'cold-accepted-source.json/other'):
            self.assertNotRegex(name, pattern)

    def test_failed_read_can_retry_with_a_new_log(self):
        cli = load_cli()
        engine, operation = 'a' * 40, 'b' * 24
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            receipt = {'kind': 'klokast.router-command-result.v1', 'box': 'k001',
                       'action': 'accepted-source', 'engine_commit': engine,
                       'result': {'kind': 'klokast.router-accepted-source.v1'}}
            calls = 0

            def command(argv, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise cli.UpdateError('first read failed')
                cli.transport.write(directory / 'cold-accepted-source.json', receipt)

            with mock.patch.object(cli.transport, 'command', side_effect=command):
                with self.assertRaisesRegex(cli.UpdateError, 'first read failed'):
                    cli.accepted_source_at('k001', directory, operation, engine,
                                           'cold-accepted-source')
                self.assertEqual(cli.accepted_source_at('k001', directory, operation,
                                 engine, 'cold-accepted-source'), receipt['result'])
            self.assertEqual(calls, 2)
            self.assertEqual(len(list(directory.glob('cold-accepted-source-*.log'))), 2)


if __name__ == '__main__':
    unittest.main()
