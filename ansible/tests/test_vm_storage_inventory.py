#!/usr/bin/env python3
import copy
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import vm_storage_inventory as storage
from platform_updates import UpdateError

CATALOG = json.loads((REPO / 'apps/music/vm-retention.json').read_text())
STORE = '/home/neo/.local/share/containers/storage'


def load_collector():
    loader = SourceFileLoader('vm_update_collector', str(REPO / 'ansible/roles/vm-update-inventory/files/collect-vm-update-facts'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def volume(name):
    return {'Name': name, 'Driver': 'local', 'Mountpoint': STORE + '/volumes/' + name + '/_data',
            'options_empty': True, 'directory_verified': True}


def fact():
    return {'role': 'bak', 'runtime_owner': {'uid': 1000, 'gid': 1000},
            'subuid': 'neo:100000:65536\n', 'subgid': '1000:100000:65536\n',
            'podman_storage': {'rootless': True, 'graph_root': STORE, 'volume_path': STORE + '/volumes',
                               'transient': False, 'graph_root_directory': True},
            'podman_inventory_stable': True, 'containers': [],
            'volumes': [volume(name) for name in CATALOG['datasets']['library']['volumes']]}


def container(mounts=None):
    return {'id': 'a' * 64, 'name': 'music-test', 'image_id': 'b' * 64,
            'read_only_root': True, 'mounts': mounts or []}


class AssessmentTests(unittest.TestCase):
    def assess(self, value=None):
        return storage.assess(fact() if value is None else value, [CATALOG], 'boxa-bak')

    def codes(self, value):
        return {v['code'] for v in self.assess(value)['findings']}

    def test_unused_dataset_is_recognized_without_granting_authority(self):
        original = fact()
        saved = copy.deepcopy(original)
        result = self.assess(original)
        self.assertEqual(original, saved)
        self.assertEqual(len(result['volumes']), 2)
        self.assertEqual(result['volumes'][0]['catalog_match'], {'app': 'music', 'dataset': 'library'})
        self.assertTrue(all(v['path_supported'] for v in result['volumes']))
        self.assertTrue(all(v['retention_approved'] is False for v in result['volumes']))
        self.assertFalse(result['adoption_ready'])
        self.assertIn('instance-retention', result['unverified_gates'])
        self.assertIn('host-data-completeness', result['unverified_gates'])
        self.assertEqual(result['runtime_identity']['subgid'], [[100000, 65536]])
        self.assertEqual(self.codes(original), {'storage.adoption-unverified'})

    def test_catalog_match_is_role_specific(self):
        value = fact(); value['role'] = 'dmz'
        self.assertIn('storage.volume-unclassified', self.codes(value))
        self.assertTrue(all(v['catalog_match'] is None for v in self.assess(value)['volumes']))

    def test_partial_dataset_and_unknown_volumes_are_never_discarded(self):
        value = fact(); value['volumes'].pop(); value['volumes'].append(volume('print-data'))
        result = self.assess(value)
        self.assertIn('storage.dataset-incomplete', self.codes(value))
        self.assertIn('storage.volume-unclassified', self.codes(value))
        self.assertEqual({v['name'] for v in result['volumes']}, {'klokast-music-library', 'print-data'})

    def test_unverified_remote_custom_and_alias_paths_block(self):
        for change in ({'Driver': 'nfs'}, {'options_empty': False}, {'directory_verified': False},
                       {'Mountpoint': '/etc'}, {'Mountpoint': STORE + '/volumes/../secret'}, {'Mountpoint': None}):
            value = fact(); value['volumes'][0].update(change)
            with self.subTest(change=change):
                self.assertIn('storage.volume-unsafe', self.codes(value))
                self.assertFalse(self.assess(value)['volumes'][0]['path_supported'])

    def test_ambiguous_volume_names_block(self):
        for entry in (volume('bad/name'), volume('klokast-music-library'), {}, None):
            value = fact(); value['volumes'].append(entry)
            with self.subTest(entry=entry):
                self.assertIn('storage.volume-invalid', self.codes(value))

    def test_absent_and_racing_inventory_is_not_empty_success(self):
        value = fact(); value.update(containers=None, volumes=None, podman_inventory_stable=False)
        self.assertTrue({'storage.containers-unknown', 'storage.volumes-unknown', 'storage.inventory-unstable'} <= self.codes(value))
        self.assertFalse(self.assess(value)['adoption_ready'])

    def test_custom_root_rootful_transient_or_unverified_store_blocks(self):
        for change in ({'graph_root': '/srv/containers'}, {'rootless': False}, {'transient': True},
                       {'graph_root_directory': False}, {'volume_path': '/srv/volumes'}):
            value = fact(); value['podman_storage'].update(change)
            with self.subTest(change=change):
                self.assertIn('storage.runtime-unknown', self.codes(value))
                self.assertTrue(all(not v['path_supported'] for v in self.assess(value)['volumes']))

    def test_numeric_identities_require_valid_nonoverlapping_ranges(self):
        for content in ('', 'neo:1:0', 'neo:100000:65536\nother:100001:10', 'neo:100000:65536\nneo:100000:65536',
                        'neo:1000:65536', 'neo:4294967295:2', 'other:100000:65536', 'neo:x:65536'):
            value = fact(); value['subuid'] = content
            with self.subTest(content=content):
                self.assertIn('storage.identity-unknown', self.codes(value))
                self.assertIsNone(self.assess(value)['runtime_identity'])
        for owner in ({'uid': 0, 'gid': 1000}, {'uid': True, 'gid': 1000}, None):
            value = fact(); value['runtime_owner'] = owner
            self.assertIn('storage.identity-unknown', self.codes(value))

    def test_mapped_volume_must_agree_with_container_mount(self):
        v = fact()['volumes'][0]
        mount = {'Type': 'volume', 'Name': v['Name'], 'Source': v['Mountpoint'], 'Destination': '/music', 'RW': True}
        value = fact(); value['containers'] = [container([mount])]
        self.assertNotIn('storage.mount-conflict', self.codes(value))
        mount['Source'] = '/unexpected'
        self.assertIn('storage.mount-conflict', self.codes(value))
        mount['Name'] = 'unknown-volume'
        self.assertIn('storage.mount-conflict', self.codes(value))

    def test_read_only_bind_mounts_still_require_accounting(self):
        value = fact(); value['containers'] = [container([{'Type': 'bind', 'Source': '/srv/site/public', 'Destination': '/public', 'RW': False}])]
        result = self.assess(value)
        self.assertIn('storage.bind-unclassified', self.codes(value))
        self.assertEqual(result['bind_mounts'][0]['source'], '/srv/site/public')
        self.assertFalse(result['bind_mounts'][0]['writable'])

    def test_writable_or_unknown_container_layer_is_blocked(self):
        for read_only in (False, None, 'true'):
            value = fact(); value['containers'] = [{**container(), 'read_only_root': read_only}]
            self.assertIn('storage.writable-layer', self.codes(value))

    def test_invalid_container_and_mount_evidence_blocks(self):
        for bad in ({}, {**container(), 'id': []}, None):
            value = fact(); value['containers'] = [bad]
            self.assertIn('storage.container-invalid', self.codes(value))
        for mount in ({'Type': 'bind', 'Destination': '/a/../b', 'RW': True},
                      {'Type': 'bind', 'Destination': '/a', 'RW': 'true'}, None):
            value = fact(); value['containers'] = [container([mount])]
            self.assertIn('storage.mount-invalid', self.codes(value))
        value = fact(); value['containers'] = [container(), container()]
        self.assertIn('storage.container-invalid', self.codes(value))

    def test_image_less_pod_infrastructure_needs_adapter_and_keeps_mounts(self):
        value = fact()
        value['containers'] = [{**container([{'Type': 'bind', 'Source': '/srv/private/data', 'Destination': '/data', 'RW': True}]),
                                'image_id': '', 'infra': True, 'pod': 'c' * 64}]
        result = self.assess(value)
        self.assertIn('storage.infra-unqualified', self.codes(value))
        self.assertNotIn('storage.image-unknown', self.codes(value))
        self.assertFalse(result['adoption_ready'])
        self.assertEqual(len(result['bind_mounts']), 1)
        for change in ({'infra': False}, {'infra': 'true'}, {'pod': ''}, {'image_id': 'latest'}):
            bad = copy.deepcopy(value); bad['containers'][0].update(change)
            self.assertIn('storage.image-unknown', self.codes(bad))
            self.assertEqual(len(self.assess(bad)['bind_mounts']), 1)

    def test_catalog_rejects_duplicate_owners_and_unsafe_mappings(self):
        with self.assertRaises(UpdateError): storage.catalog_index([CATALOG, CATALOG], 'bak')
        second = copy.deepcopy(CATALOG); second['app'] = 'second'
        with self.assertRaises(UpdateError): storage.catalog_index([CATALOG, second], 'bak')
        for mapping in ({'volumes': ['../secret'], 'legacy_directories': []},
                        {'volumes': ['okay'], 'legacy_directories': ['/etc']},
                        {'volumes': ['okay'], 'legacy_directories': [], 'delete': True}):
            bad = copy.deepcopy(CATALOG); bad['datasets']['library'] = mapping
            with self.assertRaises(UpdateError): storage.catalog_index([bad], 'bak')

    def test_catalog_matches_existing_music_defaults_and_logical_dataset(self):
        import yaml
        defaults = yaml.safe_load((REPO / 'apps/music/ansible/roles/music-backend/defaults/main.yml').read_text())
        manifest = yaml.safe_load((REPO / 'apps/music/platform-resources.yml').read_text())
        self.assertEqual(CATALOG['datasets']['library']['volumes'], [defaults['music_library_volume'], defaults['music_playlist_volume']])
        self.assertEqual(CATALOG['datasets']['library']['legacy_directories'], [defaults['music_legacy_library_root'], defaults['music_legacy_playlist_root']])
        self.assertIn('library', [v['id'] for v in manifest['datasets']])


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.collector = load_collector()
        self.responses = {'ps --all --format=json': [{'Id': 'a' * 64}],
                          'container inspect ' + 'a' * 64: [{'Id': 'a' * 64, 'Name': 'sample', 'Image': 'b' * 64,
                              'IsInfra': False, 'Pod': '',
                              'HostConfig': {'ReadonlyRootfs': True}, 'Mounts': [], 'Config': {'Env': ['SECRET=hidden']}}],
                          'volume inspect --all': [{'Name': 'sample', 'Driver': 'local', 'Mountpoint': '/safe',
                                                   'Options': {'password': 'hidden'}, 'Labels': {'secret': 'hidden'}}],
                          'info --format=json': {'host': {'security': {'rootless': True}}, 'store': {
                              'graphRoot': STORE, 'volumePath': STORE + '/volumes', 'transientStore': False}}}

    def test_secret_fields_never_leave_collector(self):
        result = self.collector.podman_snapshot(self.responses.get)
        self.assertNotIn('hidden', json.dumps(result))
        self.assertFalse(result['volumes'][0]['options_empty'])
        self.assertTrue(result['containers'][0]['read_only_root'])
        self.assertIs(result['containers'][0]['infra'], False)

    def test_inspect_failure_and_partial_coverage_stay_unknown(self):
        for inspected in (None, [], [{'Id': 'b' * 64}], [{'Id': []}]):
            self.responses['container inspect ' + 'a' * 64] = inspected
            self.assertIsNone(self.collector.podman_snapshot(self.responses.get))

    def test_empty_inventory_is_distinct_from_command_failure(self):
        self.responses['ps --all --format=json'] = []
        self.responses['volume inspect --all'] = []
        self.assertEqual(self.collector.podman_snapshot(self.responses.get), {'containers': [], 'volumes': []})
        self.responses['volume inspect --all'] = None
        self.assertIsNone(self.collector.podman_snapshot(self.responses.get))

    def test_untrusted_ids_never_become_shell_commands(self):
        calls = []
        def podman(command):
            calls.append(command)
            return [{'Id': '$(cat /secret)'}]
        self.assertIsNone(self.collector.podman_snapshot(podman))
        self.assertEqual(calls, ['ps --all --format=json'])

    def test_collection_requires_matching_snapshots(self):
        first = self.collector.podman_snapshot(self.responses.get)
        for second, expected in ((first, True), (None, False), ({'containers': [], 'volumes': []}, False)):
            with patch.object(self.collector, 'podman_snapshot', side_effect=[first, second]), \
                    patch.object(self.collector, 'mount_inventory', return_value=[{'path': '/', 'type': 'ext4', 'root': '/'}]), \
                    patch.object(self.collector, 'directory_verified', return_value=True):
                self.assertEqual(self.collector.collect_storage(self.responses.get)['podman_inventory_stable'], expected)

    def test_mount_inventory_does_not_export_secret_options(self):
        with patch.object(self.collector, 'text', return_value='20 1 8:1 / / rw,password=hidden - ext4 /dev/a rw,password=hidden\n'):
            result = self.collector.mount_inventory()
        self.assertEqual(result, [{'device': '8:1', 'root': '/', 'path': '/', 'type': 'ext4'}])
        self.assertNotIn('hidden', json.dumps(result))
        with patch.object(self.collector, 'text', return_value='broken'):
            self.assertIsNone(self.collector.mount_inventory())

    def test_missing_mount_evidence_cannot_claim_stable_inventory(self):
        with patch.object(self.collector, 'mount_inventory', return_value=None):
            result = self.collector.collect_storage(self.responses.get)
        self.assertFalse(result['podman_inventory_stable'])
        self.assertFalse(result['podman_storage']['graph_root_directory'])
        self.assertFalse(result['volumes'][0]['directory_verified'])

    def test_directory_probe_rejects_symlinks_and_nested_mounts(self):
        mounts = [{'path': '/', 'type': 'ext4', 'root': '/'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); real = root / 'real'; real.mkdir()
            link = root / 'alias'; link.symlink_to(real, target_is_directory=True)
            self.assertTrue(self.collector.directory_verified(str(real), mounts))
            self.assertFalse(self.collector.directory_verified(str(link), mounts))
            nested = [*mounts, {'path': str(real / 'nested'), 'type': 'ext4', 'root': '/'}]
            self.assertFalse(self.collector.directory_verified(str(real), nested))
            alias = [*mounts, {'path': directory, 'type': 'ext4', 'root': '/elsewhere'}]
            self.assertFalse(self.collector.directory_verified(str(real), alias))
            self.assertFalse(self.collector.directory_verified(str(real), None))


if __name__ == '__main__':
    unittest.main()
