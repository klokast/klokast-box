"""Copy qualification must contain failures within exact synthetic resources."""
import ast
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import vm_template_inputs
from platform_updates import UpdateError


def module(name):
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'),
        str(REPO / 'ansible/roles/router-state-copy/files' / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    result = importlib.util.module_from_spec(spec)
    loader.exec_module(result)
    return result


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.host = module('router-copy-test-dom0')

    def test_guest_host_and_controller_agree_on_corrupt_source_phases(self):
        guest = module('router-copy-test-guest')
        path = REPO / 'ansible/bin/platform-router-update'
        loader = importlib.machinery.SourceFileLoader('router_copy_cli_test', str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        self.assertEqual(self.host.PHASES, guest.PHASES)
        self.assertEqual(list(self.host.PHASES), cli.COPY_TEST_PHASES)

    def test_synthetic_copy_has_no_dom0_package_reconciliation(self):
        path = REPO / 'ansible/roles/router-state-copy/tasks/qualification.yml'
        source = path.read_text(encoding='utf-8')
        self.assertNotIn('sfdisk', source)
        self.assertNotIn('maintenance-unlock.yml', source)
        self.assertNotIn('maintenance-lock.yml', source)
        self.assertNotIn('dom0-apk-policy', source)

    def test_fixed_synthetic_mbr_has_bounded_partitions(self):
        with tempfile.TemporaryDirectory() as temporary:
            disk = Path(temporary) / 'original.slot'
            with disk.open('wb') as stream:
                stream.truncate(self.host.SLOTS['original'])
            self.host.partition_blank_disk(disk)
            with disk.open('rb') as stream:
                mbr = stream.read(512)
            self.assertEqual(mbr[:446], bytes(446))
            self.assertEqual(mbr[510:], b'\x55\xaa')
            rows = [struct.unpack('<B3sB3sII',mbr[446+i*16:462+i*16])
                    for i in range(3)]
            self.assertEqual([(row[0],row[2],row[4],row[5]) for row in rows], [
                (0,0x83,2048,8192),(0,0x83,10240,2048),
                (0,0x83,12288,131072-12288)])
            with self.assertRaisesRegex(RuntimeError,'blank scratch'):
                self.host.partition_blank_disk(disk)
            short = Path(temporary) / 'short.slot'
            short.write_bytes(bytes(512))
            with self.assertRaisesRegex(RuntimeError,'fixed-size'):
                self.host.partition_blank_disk(short)

    def test_each_copy_has_readonly_source_and_no_network(self):
        loops = {name: '/dev/loop' + str(i) for i, name in enumerate(self.host.SLOTS)}
        request = {'operation_id': 'a' * 24, 'inputs_sha256': 'b' * 64}
        for phase in self.host.PHASES:
            text = self.host.configuration(Path('/operation'), request, phase, 'test-uuid', loops)
            values = {node.targets[0].id: ast.literal_eval(node.value) for node in ast.parse(text).body}
            self.assertEqual(values['vif'], [])
            self.assertEqual(len(values['disk']), 5)
            self.assertNotEqual(values['name'], 'router')
            self.assertNotIn('/dev/vg', text)
            if phase in ('forward', 'reverse'):
                source = 'candidate' if phase == 'reverse' else 'original'
                self.assertEqual(values['disk'][0], 'phy:' + loops[source] + ',xvda,r')
                self.assertTrue(values['disk'][1].endswith(',xvdb,w'))
            if phase == 'corrupt':
                self.assertEqual(values['disk'][0], 'phy:' + loops['candidate'] + ',xvda,w')
                self.assertEqual(values['disk'][1], 'phy:' + loops['original'] + ',xvdb,r')
            if phase == 'reject':
                self.assertEqual(values['disk'][0], 'phy:' + loops['candidate'] + ',xvda,r')
                self.assertEqual(values['disk'][1], 'phy:' + loops['original'] + ',xvdb,w')

    def test_foreign_request_fails_before_boot_inputs_are_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            value = {'kind': 'klokast.router-copy-test-request.v2', 'box': 'boxa', 'role': 'router',
                     'operation_id': 'a' * 24, 'inputs_sha256': 'b' * 64, 'engine_commit': 'c' * 40,
                     'bootstrap': {name: {'bytes': 1, 'sha256': 'd' * 64} for name in ('kernel', 'initramfs')}}
            for field, wrong in (('kind', 'klokast.router-copy-test-request.v1'), ('box', 'boxb'), ('role', 'dmz'), ('operation_id', 'f' * 24), ('engine_commit', 'HEAD')):
                (root / 'request.json').write_text(json.dumps({**value, field: wrong}))
                with self.subTest(field=field), patch.object(self.host, 'safe_file'), self.assertRaises(RuntimeError):
                    self.host.request(root, 'boxa', 'a' * 24)

    def test_cleanup_preserves_disks_on_domain_or_attachment_uncertainty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation = 'a' * 24
            (root / 'lifecycle.json').write_text(json.dumps({'operation_id': operation, 'stage': 'detached',
                'uuids': {phase: '11111111-1111-4111-8111-111111111111' for phase in self.host.PHASES}}))
            disk = root / 'original.slot'
            with disk.open('wb') as stream:
                stream.truncate(self.host.SLOTS['original'])
            for live, attached in (({}, []), (None, ['/dev/loop7'])):
                with patch.object(self.host, 'safe_file'), patch.object(self.host, 'domain', return_value=live), \
                     patch.object(self.host, 'loop_devices', return_value=attached), self.assertRaises(RuntimeError):
                    self.host.cleanup(root, operation)
                self.assertTrue(disk.exists())
            with patch.object(self.host, 'safe_file'), patch.object(self.host, 'domain', return_value=None), \
                 patch.object(self.host, 'loop_devices', return_value=[]):
                self.assertEqual(self.host.cleanup(root, operation), self.host.SLOTS['original'])
            self.assertFalse(disk.exists())

    def test_cold_request_rejects_other_boxes_and_invalid_capsule_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            value = {'kind': 'klokast.router-copy-test-request.v3', 'box': 'k001',
                'role': 'router', 'operation_id': 'a' * 24, 'inputs_sha256': 'b' * 64,
                'engine_commit': 'c' * 40, 'cold_bootstrap_sha256': 'd' * 64,
                'bootstrap': {name: {'bytes': 1, 'sha256': 'e' * 64}
                              for name in ('kernel', 'initramfs')}}
            for change, box in (({'box': 'k002'}, 'k002'),
                    ({'cold_bootstrap_sha256': 'HEAD'}, 'k001'),
                    ({'cold_bootstrap_sha256': None}, 'k001')):
                (root / 'request.json').write_text(json.dumps({**value, **change}))
                with self.subTest(change=change), patch.object(self.host, 'safe_file'), \
                     self.assertRaisesRegex(RuntimeError, 'invalid target or contract'):
                    self.host.request(root, box, 'a' * 24)

    def test_cleanup_retains_source_when_only_cold_inspector_remains(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation = 'a' * 24
            (root / 'lifecycle.json').write_text(json.dumps({
                'operation_id': operation, 'stage': 'detached', 'uuids': {
                    phase: '11111111-1111-4111-8111-111111111111'
                    for phase in self.host.PHASES}}))
            disk = root / 'original.slot'
            disk.write_bytes(b'preserve this source')
            with patch.object(self.host, 'safe_file'), patch.object(self.host, 'domain',
                    side_effect=lambda name: {} if name.startswith('router-cold-fs-') else None), \
                 self.assertRaisesRegex(RuntimeError, 'cold fixture domain remains'):
                self.host.cleanup(root, operation)
            self.assertEqual(disk.read_bytes(), b'preserve this source')

    def test_job_modules_cannot_escape_or_replace_package_content(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            root = directory / 'root'; root.mkdir()
            source = directory / 'helper.py'; source.write_text('pass\n')
            for name in ('../escape.py', '/absolute.py', 'x/../escape.py'):
                with self.subTest(name=name), self.assertRaises(UpdateError):
                    vm_template_inputs.stage_job_files(root, {name: source})
            (root / 'linked').symlink_to(directory)
            with self.assertRaises(UpdateError):
                vm_template_inputs.stage_job_files(root, {'linked/escape.py': source})
            vm_template_inputs.stage_job_files(root, {'usr/local/lib/helper.py': source})
            with self.assertRaises(UpdateError):
                vm_template_inputs.stage_job_files(root, {'usr/local/lib/helper.py': source})
            self.assertEqual((root / 'usr/local/lib/helper.py').read_text(), 'pass\n')


if __name__ == '__main__':
    unittest.main()
