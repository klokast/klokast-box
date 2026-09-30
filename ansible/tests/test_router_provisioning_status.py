"""Provisioning must use the protected dom0 reader before allocation."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_router_template_cli import load_cli
from test_router_updates import ENGINE


class ProvisioningStatusTests(unittest.TestCase):
    def test_exact_engine_status_is_read_and_pending_replacement_blocks(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as root:
            state = Path(root)
            status = {'kind':'klokast.router-provisioning-status.v1','box':'boxa',
                      'assignment':None,'pending':None,'installation':None}
            def command(argv,**kwargs):
                args = [str(item) for item in argv]
                if args[0] == 'git':
                    return ENGINE if 'rev-parse' in args else ''
                arguments = json.loads(Path(args[-1][1:]).read_text())
                result = state / arguments['router_provision_operation'] / 'provisioning-status.json'
                result.write_text(json.dumps({'kind':'klokast.router-command-result.v1',
                    'box':'boxa','action':'provisioning-status','engine_commit':ENGINE,
                    'result':status}))
                return ''
            with patch.object(cli,'STATE',state), \
                 patch.object(cli.transport,'require_controller'), \
                 patch.object(cli.transport,'command',side_effect=command), \
                 patch.object(cli.transport,'approved_engine',return_value=ENGINE), \
                 patch.object(cli.transport,'installation_lock',return_value=nullcontext()):
                result = cli.provisioning_status('boxa')
                self.assertIsNone(result['assignment'])
                self.assertTrue(Path(result['evidence_directory']).is_dir())
                status['pending'] = {'different':'replacement'}
                with self.assertRaisesRegex(cli.UpdateError,'pending replacement'):
                    cli.provisioning_status('boxa')


if __name__ == '__main__':
    unittest.main()
