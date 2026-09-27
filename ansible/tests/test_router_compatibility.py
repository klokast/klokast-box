"""Legacy copies must be fenced, sanitized, and bound to exact source evidence."""
import ast
import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_fixture as fixture
import router_compatibility
from test_router_copy_qualification import module


class FixtureTests(unittest.TestCase):
    def test_native_key_fixture_uses_decoded_api_bytes_and_hides_invalid_data(self):
        value = 'privkey:' + '0' * 64
        encoded = base64.b64encode(value.encode()).decode()
        self.assertEqual(router_compatibility.native_key_bytes(encoded), value)
        for invalid in ('private-fixture-invalid', value, encoded + 'bad', None, 'x' * 1000):
            with self.assertRaisesRegex(RuntimeError, '^native rotation fixture has an unsupported machine-key encoding$'):
                router_compatibility.native_key_bytes(invalid)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.service = self.root / 'var/lib/tailscale'
        self.service.mkdir(parents=True)

    def test_clear_refuses_links_without_partial_deletion(self):
        good = self.service / 'tailscaled.state'; good.write_text('synthetic only')
        outside = self.root / 'outside'; outside.write_text('retained')
        for kind in ('symlink', 'hardlink', 'writable', 'fifo'):
            with self.subTest(kind=kind):
                bad = self.service / 'bad'
                if kind == 'symlink':
                    bad.symlink_to(outside)
                elif kind == 'hardlink':
                    os.link(outside, bad)
                elif kind == 'fifo':
                    os.mkfifo(bad)
                else:
                    bad.write_text('unsafe'); bad.chmod(0o666)
                with self.assertRaises(ValueError):
                    fixture.clear_directory(self.root, 'var/lib/tailscale')
                self.assertTrue(good.exists())
                self.assertEqual(outside.read_text(), 'retained')
                bad.unlink()
        fixture.clear_directory(self.root, 'var/lib/tailscale')
        self.assertEqual(list(self.service.iterdir()), [])
        self.assertTrue(self.service.is_dir())

    def test_unknown_paths_and_linked_parent_fail(self):
        for name in ('etc', '../escape', '/var/lib/tailscale'):
            with self.assertRaises(ValueError):
                fixture.clear_directory(self.root, name)
            with self.assertRaises(ValueError):
                fixture.remove(self.root, name)
        outside = self.root / 'outside'; outside.mkdir()
        self.service.rmdir(); self.service.symlink_to(outside)
        with self.assertRaises(ValueError):
            fixture.clear_directory(self.root, 'var/lib/tailscale')

    def test_lease_bound_is_distinct_from_small_identity_files(self):
        path = self.root / 'var/lib/misc/dnsmasq.leases'
        path.parent.mkdir()
        path.write_bytes(b'x' * 20000)
        fixture.remove(self.root, 'var/lib/misc/dnsmasq.leases', owner=os.geteuid())
        self.assertFalse(path.exists())
        path.write_bytes(b'x' * (4 * 1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            fixture.remove(self.root, 'var/lib/misc/dnsmasq.leases', owner=os.geteuid())
        self.assertTrue(path.exists())

    def test_account_selector_rejects_ambiguous_or_out_of_range_ids(self):
        etc = self.root / 'etc'; etc.mkdir()
        (etc / 'group').write_text('tailscale:x:103:\n')
        for line in ('dnsmasq:x:0:65:x:/var/lib:/sbin/nologin\n',
                     'dnsmasq:x:65:65:x:/var/lib:/sbin/nologin\n' * 2,
                     'dnsmasq:x:999999:65:x:/var/lib:/sbin/nologin\n'):
            (etc / 'passwd').write_text(line)
            with self.assertRaises(ValueError):
                fixture.accounts(self.root)
        (etc / 'passwd').write_text('dnsmasq:x:65:65:x:/var/lib:/sbin/nologin\n')
        self.assertEqual(fixture.accounts(self.root), {'dnsmasq_uid': 65, 'dnsmasq_gid': 65, 'tailscale_gid': 103})


class HostTests(unittest.TestCase):
    def setUp(self):
        self.host = module('router-compatibility-dom0')
        self.operation = 'a' * 24

    def test_boots_have_no_production_disk_or_network_and_copies_are_readonly(self):
        loops = {name: '/dev/loop' + str(index) for index, name in enumerate(self.host.SLOTS)}
        value = {'operation_id': self.operation, 'inputs_sha256': 'b' * 64, 'guest': {}}
        for phase in self.host.PHASES:
            text = self.host.configuration(Path('/operation'), value, phase, 'uuid', loops)
            parsed = {node.targets[0].id: ast.literal_eval(node.value) for node in ast.parse(text).body}
            self.assertEqual(parsed['vif'], [])
            self.assertNotEqual(parsed['name'], 'router')
            self.assertNotIn('/dev/vg', text)
            self.assertIn('klokast_job=', parsed['extra'])
            if phase in ('forward', 'reverse'):
                name = 'new' if phase == 'reverse' else 'old'
                self.assertEqual(parsed['disk'][0], 'phy:' + loops[name] + ',xvda,r')
            if phase in ('new', 'old'):
                self.assertIn('phy:' + loops['previous'] + ',xvdf,r', parsed['disk'])
                self.assertTrue(parsed['kernel'].endswith('/' + phase + '-kernel'))

    def test_snapshot_checks_uuid_origin_tag_permissions_capacity(self):
        record = {'uuid': 'snapshot', 'origin_uuid': 'source', 'path': '/dev/vg0/test', 'tag': 'tag'}
        row = {'lv_uuid': 'snapshot', 'origin_uuid': 'source', 'lv_path': '/dev/vg0/test',
               'lv_tags': 'tag', 'lv_attr': 'sri-a-s---', 'lv_size': str(1024 * self.host.MIB), 'data_percent': '0.1'}
        self.host.validate_snapshot(row, record)
        for field, wrong in (('lv_uuid', 'foreign'), ('origin_uuid', 'other'), ('lv_tags', 'other'),
                              ('lv_attr', 'swi-a-s---'), ('lv_attr', 'sri-I-s---'), ('data_percent', '95')):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.host.validate_snapshot({**row, field: wrong}, record)

    def test_cleanup_rejects_same_size_replacement_and_live_guests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            disk = root / 'old.slot'
            with disk.open('wb') as stream:
                stream.truncate(self.host.SLOTS['old'])
            record = {'operation_id': self.operation, 'stage': 'detached',
                      'uuids': dict.fromkeys(self.host.PHASES, '11111111-1111-4111-8111-111111111111'),
                      'slots': {name: self.host.file_identity(disk) for name in self.host.SLOTS}}
            (root / 'lifecycle.json').write_text(json.dumps(record))
            (root / 'snapshot.json').write_text(json.dumps({'stage': 'retired'}))
            for live, attached in (({}, []), (None, ['/dev/loop7'])):
                with patch.object(self.host, 'safe_file'), patch.object(self.host, 'domain', return_value=live), \
                     patch.object(self.host, 'loop_devices', return_value=attached), self.assertRaises(RuntimeError):
                    self.host.cleanup(root, self.operation)
                self.assertTrue(disk.exists())
            record['slots']['old']['inode'] += 1
            (root / 'lifecycle.json').write_text(json.dumps(record))
            with patch.object(self.host, 'safe_file'), patch.object(self.host, 'domain', return_value=None), \
                 patch.object(self.host, 'loop_devices', return_value=[]), self.assertRaises(RuntimeError):
                self.host.cleanup(root, self.operation)
            self.assertTrue(disk.exists())

    def test_changed_live_identity_refuses_before_snapshot_command(self):
        source = {'configuration_sha256': 'c' * 64, 'xen_runtime': {'uuid': 'old'},
                  'boot_artifacts': {}, 'disk': {'path': '/dev/vg0/lv_router', 'uuid': 'exact', 'bytes': 2147483648}}
        current = {**source, 'accepted_record_present': False, 'pending_record_present': False,
                   'xen_runtime_matches': True, 'xen': {'disk': ['phy:/dev/vg0/lv_router,xvda,w']},
                   'logical_volumes': {'report': [{'lv': [{'lv_path': '/dev/vg0/lv_router', 'lv_uuid': 'exact',
                     'lv_size': '2147483648', 'origin': ''}]}]}}
        for field, wrong in (('xen_runtime', {'uuid': 'new'}), ('pending_record_present', True),
                              ('accepted_record_present', True), ('configuration_sha256', 'changed')):
            changed = {**current, field: wrong}
            with patch.object(self.host.runpy, 'run_path', return_value={'inspect_dom0': lambda _: changed}), \
                 patch.object(self.host, 'run') as run, self.assertRaises(RuntimeError):
                self.host.source_identity(Path('/operation'), {'box': 'boxa', 'source': source})
            run.assert_not_called()

    def test_snapshot_reconciliation_needs_the_explicit_uuid_and_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / self.operation
            root.mkdir()
            planned = {'operation_id': self.operation, 'stage': 'planned',
                       'path': '/dev/vg0/routercompat_' + self.operation,
                       'tag': 'routercompat_' + self.operation, 'origin_uuid': 'source'}
            (root / 'snapshot.json').write_text(json.dumps(planned))
            row = {'lv_uuid': 'snapshot', 'origin_uuid': 'source', 'lv_path': planned['path'],
                   'lv_tags': planned['tag'], 'lv_attr': 'sri-a-s---',
                   'lv_size': str(1024 * self.host.MIB), 'data_percent': '0.1'}
            for change in ({'lv_uuid': 'foreign'}, {'origin_uuid': 'foreign'}, {'lv_tags': 'foreign'}):
                with patch.object(self.host, 'safe_file'), patch.object(self.host, 'source_identity'), \
                     patch.object(self.host, 'snapshot_info', return_value={**row, **change}), \
                     patch.object(self.host, 'run') as run, self.assertRaises(RuntimeError):
                    self.host.reconcile_snapshot(root, {'source': {'disk': {'uuid': 'source'}}}, 'snapshot')
                run.assert_not_called()
                self.assertEqual(json.loads((root / 'snapshot.json').read_text()), planned)


if __name__ == '__main__':
    unittest.main()
