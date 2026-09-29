"""A retained inventory can qualify a template without publishing authority."""
from contextlib import nullcontext
import datetime as dt
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
    def test_rejected_initial_selection_allocates_no_build_operation(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache, state = root / 'cache', root / 'state'
            cache.mkdir(); state.mkdir()
            source = cache / ('b' * 24)
            source.mkdir()

            def command(argv, **kwargs):
                return ENGINE if 'rev-parse' in argv else ''

            with patch.object(cli, 'CACHE', cache), patch.object(cli, 'STATE', state), \
                    patch.object(cli.transport, 'require_controller'), \
                    patch.object(cli.transport, 'installation_lock', return_value=nullcontext()), \
                    patch.object(cli.transport, 'command', side_effect=command), \
                    patch.object(cli.transport, 'load', return_value={'profile':'router-alpine-v2'}), \
                    patch.object(cli, 'initial_template_selection',
                                 side_effect=cli.UpdateError('selection changed')):
                with self.assertRaisesRegex(cli.UpdateError, 'selection changed'):
                    cli.build_template('boxa', source, initial_selection=True)
            self.assertEqual(list(state.iterdir()), [])
            self.assertEqual(list(source.iterdir()), [])

    def test_initial_template_requires_the_current_exact_selection(self):
        cli = load_cli()
        source = Path('/var/cache/klokast/updates/router/' + 'b' * 24)
        profile = {'release_metadata':'https://alpinelinux.org/releases.json'}
        manifest = {'branch':'v3.24', 'inputs_sha256':'c' * 64}
        schedule = {'kind':'klokast.vm-update-schedule.v1', 'policy':{
            'branch-policy':'tested-stable', 'branch-delay-days':21, 'report-max-age-hours':72}}
        releases = {'release_branches':[{'rel_branch':'v3.24', 'git_branch':'3.24-stable',
            'branch_date':'2026-01-01',
            'eol_date':'2028-01-01', 'arches':['x86_64'], 'repos':[
                {'name':'main','eol_date':'2028-01-01'},
                {'name':'community','eol_date':'2028-01-01'}],
            'releases':[{'version':'3.24.0','date':'2026-01-01'}]}]}
        selection = cli.router_updates.seal({
            'kind':'klokast.router-bootstrap-input-selection.v1', 'engine_commit':ENGINE,
            'observed_at':dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'branch':'v3.24', 'branch_delay_days':21,
            'schedule_sha256':cli.router_updates.digest(schedule),
            'metadata_sha256':'d' * 64, 'inputs_sha256':'c' * 64,
            'replacement_authorized':False})
        with patch.object(cli.router_updates, 'validate_inputs'), \
                patch.object(cli.transport, 'approved_engine', return_value=ENGINE), \
                patch.object(cli.transport, 'load', side_effect=[manifest, selection]), \
                patch.object(cli, 'schedule_source', side_effect=[schedule, schedule]), \
                patch.object(cli.upstream, 'fetch_json', return_value=(releases, 'd' * 64)):
            self.assertEqual(cli.initial_template_selection(source, profile, ENGINE),
                             selection['receipt_sha256'])
        with patch.object(cli.router_updates, 'validate_inputs'), \
                patch.object(cli.transport, 'approved_engine', return_value=ENGINE), \
                patch.object(cli.transport, 'load', side_effect=[manifest, selection]), \
                patch.object(cli, 'schedule_source', return_value=schedule), \
                patch.object(cli.upstream, 'fetch_json', return_value=(releases, 'e' * 64)):
            with self.assertRaises(cli.UpdateError):
                cli.initial_template_selection(source, profile, ENGINE)

    def test_compatibility_inventory_never_publishes_a_release(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache, state = root / 'cache', root / 'state'
            cache.mkdir(); state.mkdir()
            source = cache / ('b' * 24)
            source.mkdir()
            commands = []
            writes = []

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
                    patch.object(cli.transport, 'write', side_effect=lambda path, value: writes.append((path, value))), \
                    patch.object(cli.transport, 'approved_engine', return_value=ENGINE), \
                    patch.object(cli.router_template_inputs, 'stage', return_value=stage), \
                    patch.object(cli.router_template_inputs, 'split_payload', return_value=[
                        {'name':'part-0000','bytes':1,'sha256':'d' * 64}]), \
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
            arguments = next(value for path, value in writes if path.name == 'arguments.json')
            self.assertEqual([item['artifact'] for item in arguments['router_template_transfer_parts']],
                             ['capsule', 'kernel', 'initramfs'])


if __name__ == '__main__':
    unittest.main()
