"""Recovery readiness checks saved bytes, ownership, and the boot dependency."""
import hashlib
import io
from pathlib import Path
import os
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_native as native
import router_records as records
from router_transaction import TransactionError


class RecoveryBootChainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.media = self.base / 'root', self.base / 'media'
        self.root.mkdir(mode=0o700); self.media.mkdir(mode=0o700)
        for name, value in (('ROOT_UID', os.geteuid()), ('parents', lambda path: None)):
            patch = mock.patch.object(records, name, value)
            patch.start(); self.addCleanup(patch.stop)
        self.engine = 'a' * 40
        prefix = 'usr/local/lib/klokast/router-updates/'
        self.entries = {
            'usr/local/sbin/router-update-transaction': b'fixed helper\n',
            'etc/init.d/klokast-router-update-recovery': b'oneshot service\n',
            'etc/conf.d/xendomains': b'rc_need="${rc_need:-} localmount klokast-router-update-recovery"\n',
            prefix + self.engine + '/router_executor.py': b'fixed module\n'}
        for name, data in self.entries.items():
            self.write(name, data)
        records.write(self.root / (prefix + 'current.json'),
            {'kind': 'klokast.router-installed-engine.v1', 'engine_commit': self.engine})
        records.write(self.root / (prefix + self.engine + '/manifest.json'),
            {'kind': 'klokast.router-engine-files.v1', 'engine_commit': self.engine,
             'files': {'router_executor.py': hashlib.sha256(b'fixed module\n').hexdigest()}})
        for name in (prefix + 'current.json', prefix + self.engine + '/manifest.json'):
            self.entries[name] = (self.root / name).read_bytes()
        self.runlevel = 'etc/runlevels/default/klokast-router-update-recovery'
        link = self.root / self.runlevel
        link.parent.mkdir(parents=True, mode=0o700)
        link.symlink_to('/etc/init.d/klokast-router-update-recovery')
        self.archive = self.media / 'boxa.apkovl.tar.gz'
        self.save()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(data)
        path.chmod(0o600)

    def save(self, *, changes=None, omitted=None, extra=None, runlevel_target=None):
        entries = {**self.entries, **(changes or {})}
        with tarfile.open(self.archive, 'w:gz') as saved:
            for name, data in entries.items():
                if name == omitted:
                    continue
                member = tarfile.TarInfo('./' + name)
                member.uid = 0; member.mode = 0o600; member.size = len(data)
                saved.addfile(member, io.BytesIO(data))
            member = tarfile.TarInfo('./' + self.runlevel)
            member.uid = 0; member.mode = 0o777; member.type = tarfile.SYMTYPE
            member.linkname = runlevel_target or '/etc/init.d/klokast-router-update-recovery'
            saved.addfile(member)
            if extra is not None:
                saved.addfile(extra, io.BytesIO(b'x' * extra.size) if extra.isfile() else None)
        self.archive.chmod(0o600)

    def check(self):
        return native.recovery_boot_chain(self.engine, root=self.root, media=self.media)

    def test_exact_saved_chain_matches_without_extracting_or_running_code(self):
        value = self.check()
        self.assertEqual(value['engine_commit'], self.engine)
        self.assertEqual(set(value['files']), set(self.entries))
        self.assertEqual(value['files']['usr/local/sbin/router-update-transaction'],
                         hashlib.sha256(self.entries['usr/local/sbin/router-update-transaction']).hexdigest())
        self.assertFalse((self.media / 'usr').exists())

    def test_saved_helper_module_dependency_or_missing_selector_refuse(self):
        for name in ('usr/local/sbin/router-update-transaction',
                'usr/local/lib/klokast/router-updates/' + self.engine + '/router_executor.py',
                'etc/conf.d/xendomains'):
            with self.subTest(path=name):
                self.save(changes={name: b'changed\n'})
                with self.assertRaisesRegex(TransactionError, 'bytes differ'):
                    self.check()
        self.save(omitted='usr/local/lib/klokast/router-updates/current.json')
        with self.assertRaisesRegex(TransactionError, 'incomplete'):
            self.check()

    def test_installed_drift_or_disabled_recovery_refuses(self):
        module = 'usr/local/lib/klokast/router-updates/' + self.engine + '/router_executor.py'
        self.write(module, b'changed\n')
        with self.assertRaisesRegex(TransactionError, 'changed installed module'):
            self.check()
        self.write(module, self.entries[module])
        link = self.root / self.runlevel
        link.unlink(); link.symlink_to('/etc/init.d/other')
        with self.assertRaisesRegex(TransactionError, 'runlevel link'):
            self.check()

    def test_duplicate_required_entry_private_live_data_and_unsafe_paths_refuse(self):
        for name in ('./usr/local/sbin/router-update-transaction', './mnt/dom0_data',
                     '././mnt/dom0_data/private', '../escape', '/mnt/dom0_data/private'):
            with self.subTest(name=name):
                extra = tarfile.TarInfo(name)
                extra.uid = 0; extra.mode = 0o600; extra.size = 1
                self.save(extra=extra)
                with self.assertRaises(TransactionError):
                    self.check()

    def test_missing_multiple_aliased_archives_and_wrong_saved_runlevel_refuse(self):
        self.save(runlevel_target='/etc/init.d/other')
        with self.assertRaisesRegex(TransactionError, 'runlevel differs'):
            self.check()
        self.save()
        duplicate = self.media / 'other.apkovl.tar.gz'
        duplicate.write_bytes(self.archive.read_bytes())
        with self.assertRaisesRegex(TransactionError, 'unique'):
            self.check()
        duplicate.unlink()
        real = self.media / 'saved.tar.gz'
        self.archive.rename(real)
        self.archive.symlink_to(real)
        with self.assertRaises(TransactionError):
            self.check()

    def test_changed_manifest_engine_or_module_path_cannot_select_arbitrary_files(self):
        path = self.root / ('usr/local/lib/klokast/router-updates/' + self.engine + '/manifest.json')
        for manifest in (
            {'kind': 'klokast.router-engine-files.v1', 'engine_commit': 'b' * 40, 'files': {}},
            {'kind': 'klokast.router-engine-files.v1', 'engine_commit': self.engine,
             'files': {'../private.py': 'c' * 64}}):
            with self.subTest(manifest=manifest):
                records.write(path, manifest)
                with self.assertRaisesRegex(TransactionError, 'module manifest'):
                    self.check()


if __name__ == '__main__':
    unittest.main()
