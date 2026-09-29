"""A retained inventory can qualify a template without publishing authority."""
from contextlib import nullcontext
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / 'ansible/bin/platform-router-update'
ENGINE = 'a' * 40


def load_cli():
    loader = SourceFileLoader('router_template_cli_test', str(CLI))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TemplateInventoryTests(unittest.TestCase):
    def test_compatibility_inventory_never_publishes_a_release(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache, state = root / 'cache', root / 'state'
            cache.mkdir(); state.mkdir()
            source = cache / ('b' * 24)
            source.mkdir()
            commands = []

            def command(argv, **kwargs):
                commands.append([str(value) for value in argv])
                if 'rev-parse' in argv:
                    return ENGINE
                return ''

            def load(path):
                if path == cli.PROFILE:
                    return {'profile':'router-alpine-v2'}
                if path.name == 'candidate.json':
                    return {'kind':'klokast.router-template-candidate.v1'}
                raise AssertionError(path)

            manifest = {'inputs_sha256':'c' * 64}
            stage = (manifest, {'sha256':'d' * 64, 'bytes':1},
                     {'kernel':{'sha256':'e' * 64, 'bytes':1},
                      'initramfs':{'sha256':'f' * 64, 'bytes':1}})
            with patch.object(cli, 'CACHE', cache), patch.object(cli, 'STATE', state), \
                    patch.object(cli.secrets, 'token_hex', return_value='1' * 24), \
                    patch.object(cli.transport, 'require_controller'), \
                    patch.object(cli.transport, 'installation_lock', return_value=nullcontext()), \
                    patch.object(cli.transport, 'command', side_effect=command), \
                    patch.object(cli.transport, 'load', side_effect=load), \
                    patch.object(cli.transport, 'write'), \
                    patch.object(cli.transport, 'approved_engine', return_value=ENGINE), \
                    patch.object(cli.router_template_inputs, 'stage', return_value=stage), \
                    patch.object(cli.router_template_inputs, 'release') as release:
                result = cli.build_template('boxa', source, compatibility_inventory=True)

            self.assertIsNone(result['release_sha256'])
            self.assertFalse(result['engine_approved'])
            self.assertFalse(result['replacement_authorized'])
            release.assert_not_called()
            playbook = next(argv for argv in commands if argv[0] == 'ansible-playbook')
            self.assertEqual(playbook[playbook.index('-vv') + 1:playbook.index(str(
                REPO / 'ansible/playbooks/74-router-template.yml'))], [
                    '-i', str(REPO / 'ansible/inventory/hosts.yml'),
                    '-i', str(state / ('1' * 24) / 'compatibility-inventory.yml')])
            self.assertTrue(any(str(REPO / 'ansible/bin/render-node-inventory') == argv[0]
                                for argv in commands))


if __name__ == '__main__':
    unittest.main()
