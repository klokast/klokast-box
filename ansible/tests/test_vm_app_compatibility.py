"""Fail-closed image, archive, and receipt boundaries for component tests."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import vm_app_compatibility as app


class ComponentTests(unittest.TestCase):
    def selection(self):
        return {'kind': 'klokast.vm-app-test-input.v1', 'app': 'static-site-web',
                'image_ref': 'ghcr.io/static-web-server/static-web-server@sha256:' + 'a' * 64,
                'manifest_sha256': 'a' * 64, 'image_id': 'b' * 64,
                'archive': {'sha256': 'c' * 64, 'bytes': 100},
                'config_sha256': 'd' * 64, 'adapter_sha256': 'e' * 64}

    def capsule(self, root, change=None):
        adapter = root / 'adapter.py'
        adapter.write_bytes(b'# approved adapter\n')
        image, config = b'opaque OCI bytes', b'[general]\n'
        selection = self.selection()
        selection.update(archive={'sha256': hashlib.sha256(image).hexdigest(), 'bytes': len(image)},
                         config_sha256=hashlib.sha256(config).hexdigest(), adapter_sha256=app.checksum(adapter))
        entries = [('request.json', app.canonical(selection), tarfile.REGTYPE),
                   ('config.toml', config, tarfile.REGTYPE), ('image.oci', image, tarfile.REGTYPE)]
        if change:
            change(entries)
        capsule = root / 'capsule.tar'
        with tarfile.open(capsule, 'x') as output:
            for name, value, kind in entries:
                info = tarfile.TarInfo(name)
                info.type, info.size = kind, len(value)
                output.addfile(info, io.BytesIO(value))
        return capsule, adapter, selection, {'sha256': app.checksum(capsule), 'bytes': capsule.stat().st_size}

    def test_fixed_selection_refuses_mutable_or_foreign_images_and_bad_types(self):
        mutations = [lambda v: v.update(app='arbitrary'), lambda v: v.update(image_ref='image:latest'),
                     lambda v: v.update(image_id='sha256:' + 'b' * 64), lambda v: v.update(config_sha256=None),
                     lambda v: v.update(adapter_sha256=[]), lambda v: v.update(extra='shell command'),
                     lambda v: v['archive'].update(bytes=True), lambda v: v['archive'].update(bytes=app.MAX_ARCHIVE + 1),
                     lambda v: v['archive'].update(path='/etc/shadow')]
        for mutate in mutations:
            value = self.selection()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                app.validate(value)

    def test_receipt_cannot_omit_tests_change_inputs_or_qualify_production(self):
        selection = self.selection()
        result = {'kind': 'klokast.vm-app-test-result.v1', 'selection': selection,
                  'tests': dict.fromkeys(app.TESTS, True), 'production_qualified': False}
        app.verify_result(result, selection)
        mutations = [lambda v: v['tests'].pop('restart'), lambda v: v['tests'].update(rootless=False), lambda v: v['tests'].update(rootless=1),
                     lambda v: v.update(production_qualified=True), lambda v: v['selection'].update(image_id='f' * 64),
                     lambda v: v.update(extra=True)]
        for mutate in mutations:
            changed = copy.deepcopy(result)
            mutate(changed)
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                app.verify_result(changed, selection)

    def test_capsule_copies_only_exact_bounded_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capsule, adapter, selection, expected = self.capsule(root)
            result = app.unpack(capsule, root / 'output', expected, adapter)
            self.assertEqual(result, selection)
            self.assertEqual({p.name for p in (root / 'output').iterdir()}, {'request.json', 'config.toml', 'image.oci'})

    def test_unsafe_duplicate_missing_and_changed_members_are_refused(self):
        changes = [lambda e: e.append(('../outside', b'bad', tarfile.REGTYPE)),
                   lambda e: e.append(e[0]), lambda e: e.pop(),
                   lambda e: e.__setitem__(2, ('image.oci', b'changed', tarfile.REGTYPE)),
                   lambda e: e.__setitem__(1, ('config.toml', b'changed', tarfile.REGTYPE)),
                   lambda e: e.__setitem__(2, ('image.oci', b'', tarfile.SYMTYPE)),
                   lambda e: e.__setitem__(2, ('image.oci', b'', tarfile.LNKTYPE)),
                   lambda e: e.__setitem__(2, ('image.oci', b'x' * 65537, tarfile.DIRTYPE))]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                capsule, adapter, _, expected = self.capsule(root, change)
                with self.assertRaises(RuntimeError):
                    app.unpack(capsule, root / 'output', expected, adapter)
                self.assertFalse((root / 'outside').exists())

    def test_capsule_hash_and_installed_code_are_checked(self):
        for change in ('capsule', 'adapter', 'truncated'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                capsule, adapter, _, expected = self.capsule(root)
                if change == 'capsule':
                    expected['sha256'] = 'f' * 64
                elif change == 'truncated':
                    capsule.write_bytes(b'short')
                else:
                    adapter.write_bytes(b'# drift\n')
                with self.assertRaises(RuntimeError):
                    app.unpack(capsule, root / 'output', expected, adapter)

    def test_partial_destination_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capsule, adapter, _, expected = self.capsule(root)
            (root / 'output').mkdir()
            with self.assertRaises(FileExistsError):
                app.unpack(capsule, root / 'output', expected, adapter)

    def test_duplicate_request_fields_are_refused(self):
        with self.assertRaises(RuntimeError):
            json.loads('{"app":"static-site-web","app":"other"}', object_pairs_hook=app.unique)

    def test_prepare_uses_catalog_digest_no_build_or_tag_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = app.canonical({'config': {'digest': 'sha256:' + 'b' * 64}})
            digest = hashlib.sha256(raw).hexdigest()
            lock = {'static_site_images': {'static_web_server': {
                'amd64_digest': 'sha256:' + digest,
                'pull_ref': 'ghcr.io/static-web-server/static-web-server@sha256:' + digest}}}
            calls = []
            def invoke(argv, **kwargs):
                calls.append(argv)
                if 'copy' in argv:
                    (root / 'inputs/image.oci').write_bytes(b'opaque archive')
                    return b''
                return raw
            with patch('yaml.safe_load', return_value=lock), patch.object(app, 'invoke', side_effect=invoke):
                result = app.prepare(REPO, root / 'inputs')
            self.assertEqual(result['selection']['manifest_sha256'], digest)
            self.assertIn('--preserve-digests', calls[0])
            self.assertIn('docker://' + lock['static_site_images']['static_web_server']['pull_ref'], calls[0])
            self.assertFalse(any('podman' in c or 'build' in c for c in calls))
            self.assertEqual(result['selection']['adapter_sha256'], app.checksum(
                REPO / 'apps/static-site/ansible/roles/static-site-runtime/files/vm_compatibility.py'))

    def test_prepare_rejects_changed_archive_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def invoke(argv, **kwargs):
                if 'copy' in argv:
                    (root / 'inputs/image.oci').write_bytes(b'opaque archive')
                return b'{}'
            with patch.object(app, 'invoke', side_effect=invoke), self.assertRaisesRegex(RuntimeError, 'manifest differs'):
                app.prepare(REPO, root / 'inputs')

    def test_adapter_refuses_non_test_process_before_commands(self):
        path = REPO / 'apps/static-site/ansible/roles/static-site-runtime/files/vm_compatibility.py'
        spec = importlib.util.spec_from_file_location('static_test', path)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        with patch.object(adapter, 'os') as operating:
            operating.getpid.return_value = 42
            with self.assertRaisesRegex(RuntimeError, 'PID 1'):
                adapter.test(lambda *_: self.fail('command outside Xen'), Path('/unused'), self.selection())


if __name__ == '__main__':
    unittest.main()
