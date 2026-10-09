"""Local image authority, pre-download refusal, and public receipt transport."""
import copy
import fcntl
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_platform_updates import load_cli
import test_vm_template_lifecycle as fixtures
import vm_local_images as images
from platform_updates import UpdateError, digest

REPO = Path(__file__).resolve().parents[2]


class LocalAuthorityTests(unittest.TestCase):
    def setUp(self):
        loader = SourceFileLoader('local_guard', str(REPO / 'ansible/roles/ops-controller/files/klokast-controller-guard'))
        self.guard = module_from_spec(spec_from_loader(loader.name, loader)); loader.exec_module(self.guard)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.marker = self.base / 'controller.json'; self.mode = self.base / 'deployment.json'
        self.record = dict(schema_version=1, hostname='boxa-ops', box='boxa', role='standby', active_box='boxb')
        self.marker.write_text(json.dumps(self.record))
        self.mode.write_text('{"schema_version":1,"lifecycle":"development"}')
        self.owner = 0
        original = Path.lstat
        def protected(path):
            value = original(path)
            return SimpleNamespace(st_uid=self.owner, st_mode=value.st_mode, st_size=value.st_size)
        for context in (patch.object(self.guard, 'DEFAULT_MARKER', self.marker),
                        patch.object(self.guard, 'DEPLOYMENT', self.mode),
                        patch.object(self.guard.socket, 'gethostname', return_value='boxa-ops'),
                        patch.object(self.guard.os, 'geteuid', return_value=1002),
                        patch.object(self.guard.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='smith')),
                        patch.object(Path, 'lstat', protected), patch.dict(os.environ, {}, clear=True)):
            context.start(); self.addCleanup(context.stop)

    def test_active_and_standby_own_only_their_box(self):
        for role in ('active', 'standby'):
            self.marker.write_text(json.dumps(dict(self.record, role=role)))
            self.assertEqual(self.guard.local_image_status('boxa', self.marker)['local_image_box'], 'boxa')
            with self.assertRaisesRegex(ValueError, 'unfenced'):
                self.guard.local_image_status('boxb', self.marker)

    def test_fenced_unconfigured_and_mismatched_records_refuse(self):
        for changes in ({'role': 'fenced'}, {'configured': False}, {'hostname': 'boxb-ops'},
                        {'box': 'boxb'}, {'schema_version': 2}):
            self.marker.write_text(json.dumps(dict(self.record, **changes)))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.guard.local_image_status('boxa', self.marker)

    def test_protected_identity_and_development_required(self):
        self.owner = 1002
        with self.assertRaises(ValueError): self.guard.local_image_status('boxa', self.marker)
        self.owner = 0
        self.marker.chmod(0o666)
        with self.assertRaises(ValueError): self.guard.local_image_status('boxa', self.marker)
        self.marker.chmod(0o600)
        self.mode.write_text('{"schema_version":1,"lifecycle":"production"}')
        with self.assertRaises(ValueError): self.guard.local_image_status('boxa', self.marker)
        self.mode.unlink(); self.mode.symlink_to(self.marker)
        with self.assertRaises(ValueError): self.guard.local_image_status('boxa', self.marker)

    def test_no_marker_override_or_other_account(self):
        with patch.dict(os.environ, {'KLOKAST_CONTROLLER_HA_MARKER': str(self.marker)}), self.assertRaises(ValueError):
            self.guard.local_image_status('boxa', self.marker)
        with patch.object(self.guard.os, 'geteuid', return_value=0), self.assertRaises(ValueError):
            self.guard.local_image_status('boxa', self.marker)
        with patch.object(self.guard.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='minion')), self.assertRaises(ValueError):
            self.guard.local_image_status('boxa', self.marker)


class PreparationBoundaryTests(unittest.TestCase):
    def test_preflight_uses_native_apk_path_outside_smith_path(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory)
            def which(name):
                return None if name == 'apk' else name
            def command(argv, **_options):
                if argv == ['tailscale', 'status', '--json']:
                    return json.dumps({'Self': {'DNSName': 'boxa-ops.example.ts.net.'}})
                self.assertEqual(argv[0], 'ansible-playbook')
                self.assertEqual(argv[3], result / 'inventory/hosts.json')
                (result / 'cleanup-preflight.json').write_text('{"box":"boxa","ready":true}')
                return ''
            with patch.object(cli.shutil, 'which', side_effect=which), \
                    patch.object(cli.shutil, 'disk_usage', return_value=SimpleNamespace(free=20 * 1024**3)), \
                    patch.object(cli, 'command', side_effect=command):
                cli.image_preflight('boxa', 'a' * 24, result)

    def test_authority_failure_before_download_or_inventory(self):
        cli = load_cli()
        with patch.object(cli.platform_source, 'require_local_image', side_effect=RuntimeError('wrong box')), \
                patch.object(cli, 'fetch_json') as download, patch.object(cli, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'wrong box'): cli.prepare('boxb')
            download.assert_not_called(); command.assert_not_called()

    def test_unavailable_dom0_or_competing_build_stops_before_download(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.object(cli, 'STATE', base), patch.object(cli, 'CACHE', base), \
                    patch.object(cli.platform_source, 'require_local_image'), \
                    patch.object(cli, 'command', return_value='a' * 40), \
                    patch.object(cli, 'image_preflight', side_effect=UpdateError('dom0 unavailable')) as preflight, \
                    patch.object(cli, 'fetch_json') as download:
                with self.assertRaisesRegex(UpdateError, 'dom0 unavailable'): cli.prepare('boxa')
                download.assert_not_called()
                preflight.reset_mock()
                with (base / 'build.lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.assertRaisesRegex(UpdateError, 'build lock'): cli.prepare('boxa')
                preflight.assert_not_called(); download.assert_not_called()

    def test_inventory_has_only_local_dom0_and_local_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = images.inventory(REPO, Path(directory) / 'inventory', 'boxa',
                                    {'Self': {'DNSName': 'boxa-ops.example.ts.net.'}})
            value = json.loads(path.read_text())['all']
            self.assertEqual(set(value['children']), {'dom0'})
            hosts = value['children']['dom0']['hosts']
            self.assertEqual(set(hosts), {'boxa-dom0'})
            self.assertEqual(hosts['boxa-dom0']['ansible_host'], 'boxa-dom0.example.ts.net')
            self.assertEqual(value['hosts']['localhost']['ansible_connection'], 'local')
            with self.assertRaises(UpdateError):
                images.inventory(REPO, Path(directory) / 'bad', 'boxb', {'Self': {'DNSName': 'boxa-ops.example.ts.net.'}})

    def test_dom0_capacity_and_pending_operation_refuse(self):
        from test_vm_template_cleanup import c
        with patch.object(c, 'references', side_effect=c.Refused('pending transaction')), self.assertRaises(c.Refused):
            c.preflight('boxa')
        for memory, free, ready in ((4096, 20 * 1024**3, False), (8192, 1024**3, False), (8192, 20 * 1024**3, True)):
            with patch.object(c, 'references', return_value=set()), \
                    patch.object(c, 'command', return_value='free_memory : ' + str(memory)), \
                    patch.object(c.shutil, 'disk_usage', return_value=SimpleNamespace(free=free)):
                if ready:
                    self.assertTrue(c.preflight('boxa')['ready'])
                else:
                    with self.assertRaises(c.Refused): c.preflight('boxa')


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.TemplateReuseTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        selection = {'observed_at': '2026-10-07T10:00:00Z', **{
            key: fixture.inputs[key] for key in ('profile', 'branch', 'architecture')}}
        fixture.cli.write(fixture.directory / 'selection.json', selection)
        self.receipt = images.export_receipt(fixture.root / 'builds', 'boxa', fixture.old)
        self.destination = fixture.root / 'imported'; self.destination.mkdir()

    def test_roundtrip_repeat_conflict_and_box_validation(self):
        operation = images.import_receipt(self.destination, 'boxa', self.receipt)
        self.assertEqual(images.export_receipt(self.destination, 'boxa', operation), self.receipt)
        self.assertEqual(images.import_receipt(self.destination, 'boxa', self.receipt), operation)
        with self.assertRaises(UpdateError): images.import_receipt(self.destination, 'boxb', self.receipt)
        changed = copy.deepcopy(self.receipt)
        changed['files']['build-result.json']['cleanup'] = {'status': 'complete'}
        changed['receipt_sha256'] = digest({k: v for k, v in changed.items() if k != 'receipt_sha256'})
        with self.assertRaisesRegex(UpdateError, 'conflict'): images.import_receipt(self.destination, 'boxa', changed)
        self.assertEqual(images.export_receipt(self.destination, 'boxa', operation), self.receipt)

    def test_bad_checksum_incomplete_cleanup_and_extra_files_refuse(self):
        for change in ('checksum', 'cleanup', 'extra', 'candidate', 'operation'):
            bad = copy.deepcopy(self.receipt)
            if change == 'checksum': bad['receipt_sha256'] = '0' * 64
            if change == 'cleanup': bad['files']['lifecycle.json']['stage'] = 'running'
            if change == 'extra': bad['files']['secret.json'] = {}
            if change == 'candidate': bad['files']['candidate.json']['box'] = 'boxb'
            if change == 'operation': bad['operation_id'] = '../escape'
            if change != 'checksum': bad['receipt_sha256'] = digest({k: v for k, v in bad.items() if k != 'receipt_sha256'})
            with self.subTest(change=change), self.assertRaises((UpdateError, ValueError)):
                images.import_receipt(self.destination, 'boxa', bad)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_symlink_partial_directory_and_interrupted_write_preserve_original(self):
        operation = self.fixture.old
        (self.destination / operation).symlink_to(self.fixture.directory)
        with self.assertRaises(UpdateError): images.import_receipt(self.destination, 'boxa', self.receipt)
        (self.destination / operation).unlink()
        with patch.object(images.os, 'fsync', side_effect=OSError('interrupted')), self.assertRaises(OSError):
            images.import_receipt(self.destination, 'boxa', self.receipt)
        self.assertEqual(list(self.destination.iterdir()), [])
        (self.destination / operation).mkdir()
        with self.assertRaises(OSError): images.import_receipt(self.destination, 'boxa', self.receipt)
        self.assertEqual(images.export_receipt(self.fixture.root / 'builds', 'boxa', operation), self.receipt)
