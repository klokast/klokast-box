"""Cold metadata recovery preserves the live lock and refuses other state."""
import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_cold_backup as cold
import router_generation_device as devices
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
from test_router_generations import generation, reseal


class Host:
    def __init__(self):
        self.running = False
        self.attached = False
        self.calls = []

    def guard(self, box, *, deadline):
        self.calls.append(('guard', box))

    def disk(self, disk, *, deadline):
        self.calls.append(('disk', disk))

    def guest(self, pair, *, deadline):
        self.calls.append(('guest', pair))
        return ('accepted', {}) if self.running else None

    def detached(self, paths, *, deadline):
        self.calls.append(('detached', paths))
        if self.attached:
            raise TransactionError('router disk still has a Xen block backend')


class ColdBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in (('ROOT_UID', os.geteuid()), ('parents', lambda path: None)):
            patch = mock.patch.object(records, name, value)
            patch.start(); self.addCleanup(patch.stop)
        base = self.root / 'mnt/dom0_data/klokast-router-updates'
        for name in ('records', 'generations', 'operations'):
            (base / name).mkdir(parents=True, mode=0o700)
        self.storage = records.Records('boxa', base)
        self.generation = generation('legacy')
        for name, item in self.generation['boot'].items():
            payload = (name + '-test-payload').encode()
            path = self.root / item['path'].removeprefix('/')
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(0o644)
            item.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        reseal(self.generation)
        with self.storage.lock():
            self.assignment = self.storage.adopt(self.generation)
        devices.remember(self.storage, self.generation['record_sha256'], 'test-device', 'boxa-router', 'f'*64)
        xen = self.root / 'etc/xen'
        (xen / 'auto').mkdir(parents=True, mode=0o700)
        (xen / 'router.cfg').write_text(generations.configuration(self.generation))
        (xen / 'router.cfg').chmod(0o644)
        (xen / 'auto/router.cfg').symlink_to('../router.cfg')
        self.host = Host()
        self.bundle = cold.Bundle(self.storage, 'a'*24, 'c'*40, root=self.root, host=self.host)
        self.lock_inode = (base / 'transaction.lock').stat().st_ino

    def capture(self):
        return self.bundle.capture(self.generation['record_sha256'])

    def remove_original(self):
        for path in self.bundle.paths(self.generation, True).values():
            path.unlink()
        (self.root / 'etc/xen/auto/router.cfg').unlink()

    def test_capture_and_restore_exact_files_preserve_lock_and_unrelated_entries(self):
        paths = self.bundle.paths(self.generation, True)
        before = {name: (path.read_bytes(), path.stat().st_mode) for name, path in paths.items()}
        unrelated = self.storage.base / 'records/unrelated.json'
        records.write(unrelated, {'preserve': True})
        self.host.running = True
        saved = self.capture()
        self.assertFalse(saved['disk_included'])
        self.assertEqual(before, {name: (path.read_bytes(), path.stat().st_mode) for name, path in paths.items()})
        self.assertEqual([call[0] for call in self.host.calls], ['guard'])
        self.assertEqual(saved, self.capture())
        self.remove_original()
        self.host.running = False
        self.assertEqual(saved, self.bundle.restore())
        before['xen.cfg'] = (generations.configuration(self.generation).encode(), 0o100600)
        self.assertEqual(before, {name: (path.read_bytes(), path.stat().st_mode) for name, path in paths.items()})
        self.assertEqual(self.storage.accepted(), self.assignment)
        self.assertEqual(self.bundle.link(), '../router.cfg')
        self.assertEqual(records.read(unrelated), {'preserve': True})
        self.assertEqual((self.storage.base / 'transaction.lock').stat().st_ino, self.lock_inode)
        self.assertEqual(saved, self.bundle.restore())
        self.assertEqual([call[0] for call in self.host.calls[-4:]], ['guard', 'disk', 'guest', 'detached'])

    def test_legacy_implicit_uuid_is_pinned_in_restored_boot_configuration(self):
        config = self.root / 'etc/xen/router.cfg'
        original = ''.join(line for line in config.read_text().splitlines(keepends=True)
                           if not line.startswith('uuid ='))
        config.write_text(original)
        self.capture()
        self.assertEqual((self.bundle.directory / 'xen.cfg').read_text(), original)
        self.remove_original()
        self.bundle.restore()
        self.assertEqual(config.read_text(), generations.configuration(self.generation))
        self.assertEqual(cold.native.literal_configuration(config.read_text())['uuid'], self.generation['xen']['uuid'])

    def test_capture_requires_expected_legacy_generation_and_no_pending_install(self):
        with self.assertRaisesRegex(TransactionError, 'changed before capture'):
            self.bundle.capture('0'*64)
        with mock.patch.object(self.storage, 'pending', return_value={'phase': 'armed'}):
            with self.assertRaisesRegex(TransactionError, 'pending update'):
                self.capture()
        with mock.patch.object(self.storage, 'installation', return_value={'stage': 'prepared'}):
            with self.assertRaisesRegex(TransactionError, 'pending update'):
                self.capture()
        self.assertFalse(self.bundle.directory.exists())

    def test_capture_partial_copy_retries_without_publishing_a_complete_manifest(self):
        original = cold.copy_exact
        def interrupted(source, destination, expected, maximum):
            if destination.name == 'kernel':
                raise OSError('injected disk write interruption')
            return original(source, destination, expected, maximum)
        with mock.patch.object(cold, 'copy_exact', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.capture()
        self.assertFalse((self.bundle.directory / 'manifest.json').exists())
        self.assertEqual(self.storage.accepted(), self.assignment)
        saved = self.capture()
        self.assertEqual(saved, self.bundle.verify()[0])

    def test_retry_refuses_changed_source_metadata(self):
        self.capture()
        xen = self.root / 'etc/xen/router.cfg'
        xen.write_text(xen.read_text() + '# unexpected change\n')
        with self.assertRaisesRegex(TransactionError, 'retry found changed metadata'):
            self.capture()

    def test_invalid_boot_content_never_publishes_completion(self):
        kernel = self.bundle.paths(self.generation, True)['kernel']
        kernel.write_bytes(b'wrong kernel')
        with self.assertRaisesRegex(TransactionError, 'boot file differs'):
            self.capture()
        self.assertFalse((self.bundle.directory / 'manifest.json').exists())

    def test_restore_refuses_running_router_attached_disk_or_changed_lv(self):
        self.capture()
        self.remove_original()
        self.host.running = True
        with self.assertRaisesRegex(TransactionError, 'router stopped'):
            self.bundle.restore()
        self.host.running = False
        self.host.attached = True
        with self.assertRaisesRegex(TransactionError, 'block backend'):
            self.bundle.restore()
        self.host.attached = False
        with mock.patch.object(self.host, 'disk', side_effect=TransactionError('LV UUID changed')):
            with self.assertRaisesRegex(TransactionError, 'LV UUID changed'):
                self.bundle.restore()
        self.assertFalse((self.storage.base / 'accepted.json').exists())
        self.assertFalse((self.root / 'etc/xen/router.cfg').exists())

    def test_other_accepted_assignment_is_never_overwritten(self):
        self.capture()
        self.remove_original()
        target = self.storage.base / 'accepted.json'
        records.write(target, {'another': 'assignment'})
        with self.assertRaisesRegex(TransactionError, 'different live file: accepted.json'):
            self.bundle.restore()
        self.assertEqual(records.read(target), {'another': 'assignment'})
        self.assertFalse((self.root / 'etc/xen/router.cfg').exists())

    def test_restore_interruption_leaves_assignment_unpublished_and_retry_finishes(self):
        self.capture()
        self.remove_original()
        original = cold.copy_exact
        def interrupted(source, destination, expected, maximum):
            if source.name == 'initramfs':
                raise OSError('injected recovery interruption')
            return original(source, destination, expected, maximum)
        with mock.patch.object(cold, 'copy_exact', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.bundle.restore()
        self.assertFalse((self.storage.base / 'accepted.json').exists())
        self.assertFalse((self.root / 'etc/xen/auto/router.cfg').is_symlink())
        self.bundle.restore()
        self.assertEqual(self.storage.accepted(), self.assignment)
        self.assertEqual((self.storage.base / 'transaction.lock').stat().st_ino, self.lock_inode)

    def test_corrupt_missing_or_aliased_bundle_file_refuses_all_restore_writes(self):
        self.capture()
        self.remove_original()
        kernel = self.bundle.directory / 'kernel'
        payload = kernel.read_bytes()
        for failure in ('corrupt', 'missing', 'symlink', 'hardlink'):
            with self.subTest(failure=failure):
                kernel.unlink(missing_ok=True)
                if failure == 'corrupt':
                    kernel.write_bytes(b'bad')
                elif failure == 'symlink':
                    kernel.symlink_to(self.bundle.directory / 'initramfs')
                elif failure == 'hardlink':
                    kernel.hardlink_to(self.bundle.directory / 'initramfs')
                with self.assertRaises((TransactionError, FileNotFoundError)):
                    self.bundle.restore()
                self.assertFalse((self.storage.base / 'accepted.json').exists())
                self.assertFalse((self.root / 'etc/xen/router.cfg').exists())
        kernel.unlink()
        kernel.write_bytes(payload)
        kernel.chmod(0o644)
        self.bundle.restore()

    def test_manifest_cannot_select_arbitrary_destinations_or_claim_disk_backup(self):
        saved = self.capture()
        for edit in ('path', 'disk', 'operation', 'engine'):
            value = copy.deepcopy(saved)
            value.pop('record_sha256')
            if edit == 'path':
                value['files']['../../etc/shadow'] = value['files'].pop('kernel')
            elif edit == 'disk':
                value['disk_included'] = True
            elif edit == 'operation':
                value['operation_id'] = 'b'*24
            else:
                value['engine_commit'] = 'd'*40
            records.write(self.bundle.directory / 'manifest.json', generations.seal(value))
            with self.subTest(edit=edit), self.assertRaises(TransactionError):
                self.bundle.verify()

    def test_bundle_operation_uses_the_existing_transaction_lock(self):
        with self.storage.lock(), self.assertRaisesRegex(TransactionError, 'holds the transaction lock'):
            self.capture()
        self.capture()
        with self.storage.lock(), self.assertRaisesRegex(TransactionError, 'holds the transaction lock'):
            self.bundle.restore()


if __name__ == '__main__':
    unittest.main()
