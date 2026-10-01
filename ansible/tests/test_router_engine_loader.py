"""Boot recovery loads the pending engine, with a closed and checked file set."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import yaml


def loader():
    path=Path(__file__).resolve().parents[1]/'roles/router-update-recovery/files/router-update-transaction'
    spec=importlib.util.spec_from_loader('router_engine_loader_test',importlib.machinery.SourceFileLoader('router_engine_loader_test',str(path)))
    result=importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.loader=loader()

    def test_installer_and_loader_agree_on_closed_module_set(self):
        root = Path(__file__).resolve().parents[1]
        tasks = yaml.safe_load((root/'roles/router-update-recovery/tasks/main.yml').read_text())
        declarations = [task['ansible.builtin.set_fact']['router_recovery_modules']
                        for task in tasks if task.get('name') == 'Declare the closed router recovery module list']
        self.assertEqual(len(declarations), 1)
        modules = declarations[0]
        self.assertEqual(len(modules), len(set(modules)))
        self.assertEqual({name + '.py' for name in modules}, self.loader.MODULES)
        for name in modules:
            self.assertTrue((root/'lib'/(name + '.py')).is_file())

    def test_pending_engine_wins_over_current_deployment_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); (base/'pending.json').write_text('pending')
            with mock.patch.object(self.loader,'BASE',base),mock.patch.object(self.loader,'read',return_value={'request':{'engine_commit':'a'*40}}) as read:
                self.assertEqual(self.loader.engine_for('boot-recover',None),'a'*40)
                read.assert_called_once_with(base/'pending.json')

    def test_cold_identity_stage_selects_only_its_existing_bundle_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            operation = 'b' * 24
            manifest = base / 'cold-backups' / operation / 'manifest.json'
            with mock.patch.object(self.loader, 'BASE', base), \
                 mock.patch.object(self.loader, 'read', return_value={
                     'kind': 'klokast.router-cold-metadata.v1',
                     'operation_id': operation, 'engine_commit': 'a' * 40}) as read:
                self.assertEqual(self.loader.engine_for('cold-stage-identity', operation), 'a' * 40)
                read.assert_called_once_with(manifest)
                with self.assertRaisesRegex(RuntimeError, 'exact operation'):
                    self.loader.engine_for('cold-stage-identity', '../other')
                read.return_value = {'kind': 'foreign', 'operation_id': operation,
                                     'engine_commit': 'a' * 40}
                with self.assertRaisesRegex(RuntimeError, 'metadata bundle'):
                    self.loader.engine_for('cold-stage-identity', operation)

    def test_cold_health_stage_selects_its_bundle_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            operation = 'b' * 24
            with mock.patch.object(self.loader, 'BASE', base), \
                 mock.patch.object(self.loader, 'read', return_value={
                     'kind': 'klokast.router-cold-metadata.v1',
                     'operation_id': operation, 'engine_commit': 'a' * 40}) as read:
                self.assertEqual(self.loader.engine_for('cold-health-stage', operation), 'a' * 40)
                read.assert_called_once_with(base / 'cold-backups' / operation / 'manifest.json')
                read.reset_mock()
                self.assertEqual(self.loader.engine_for('cold-health-clear', operation), 'a' * 40)
                read.assert_called_once_with(base / 'cold-backups' / operation / 'manifest.json')

    def test_cold_backup_allocation_selects_saved_bundle_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            operation = 'b' * 24
            with mock.patch.object(self.loader, 'BASE', base), \
                 mock.patch.object(self.loader, 'read', return_value={
                     'kind': 'klokast.router-cold-metadata.v1',
                     'operation_id': operation, 'engine_commit': 'a' * 40}) as read:
                for action in ('cold-allocate-backup', 'cold-request-stage'):
                    self.assertEqual(self.loader.engine_for(action, operation), 'a' * 40)
                self.assertEqual(read.call_count, 2)

    def test_cold_test_fence_blocks_boot_even_with_a_damaged_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            marker = base / 'cold-test.json'
            with mock.patch.object(self.loader, 'BASE', base), mock.patch.object(self.loader, 'read') as read:
                for kind in ('file', 'symlink', 'directory'):
                    if kind == 'file':
                        marker.write_text('incomplete')
                    elif kind == 'symlink':
                        marker.symlink_to(base / 'missing')
                    else:
                        marker.mkdir()
                    with self.subTest(kind=kind), self.assertRaisesRegex(RuntimeError, 'supervised router test'):
                        self.loader.engine_for('boot-recover', None)
                    if kind == 'directory':
                        marker.rmdir()
                    else:
                        marker.unlink()
                read.assert_not_called()

    def test_accepted_boot_verification_reaches_exact_installed_engine(self):
        host = mock.Mock(nodename='boxa-dom0')
        executor = mock.Mock()
        executor.main.return_value = 0
        with mock.patch.object(self.loader.os, 'uname', return_value=host), \
             mock.patch.object(self.loader.os, 'geteuid', return_value=0), \
             mock.patch.object(self.loader.Path, 'read_text', return_value='00000000-0000-0000-0000-000000000000'), \
             mock.patch.object(self.loader, 'engine_for', return_value='a' * 40) as selected, \
             mock.patch.object(self.loader, 'load', return_value=executor):
            self.assertEqual(self.loader.main(['verify-boot-assignment', '--box', 'boxa']), 0)
        selected.assert_called_once_with('verify-boot-assignment', None)
        executor.main.assert_called_once_with(['verify-boot-assignment', '--box', 'boxa'], 'a' * 40)

    def test_invalid_operation_and_engine_cannot_select_paths(self):
        for identity in ('../foreign','a'*25,None):
            with self.assertRaises(RuntimeError):
                self.loader.engine_for('worker',identity)
        with mock.patch.object(self.loader,'read',return_value={'engine_commit':'../other'}),self.assertRaises(RuntimeError):
            self.loader.engine_for('worker','a'*24)

    def test_changed_source_extra_package_and_pyc_fail_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); engine='a'*40; version=root/engine; version.mkdir()
            files={}
            for name in self.loader.MODULES:
                data=b'# exact source\n'; (version/name).write_bytes(data); files[name]=hashlib.sha256(data).hexdigest()
            manifest={'kind':'klokast.router-engine-files.v1','engine_commit':engine,'files':files}
            (version/'manifest.json').write_text(json.dumps(manifest))
            with mock.patch.object(self.loader,'ENGINES',root), \
                 mock.patch.object(self.loader,'read',return_value=manifest), \
                 mock.patch.object(self.loader,'secure',side_effect=lambda p,*a:p), \
                 mock.patch.object(self.loader.importlib,'import_module') as imported, \
                 mock.patch.object(self.loader.sys,'dont_write_bytecode',True), \
                 mock.patch.object(self.loader.sys,'path',list(self.loader.sys.path)):
                self.loader.load(engine)
                imported.assert_called_once_with('router_executor'); imported.reset_mock()
                (version/'router_executor.py').write_text('# different source\n')
                with self.assertRaises(RuntimeError):self.loader.load(engine)
                (version/'router_executor.py').write_bytes(b'# exact source\n')
                for name in ('router_executor','__pycache__'):
                    (version/name).mkdir()
                    with self.assertRaises(RuntimeError):self.loader.load(engine)
                    (version/name).rmdir()
                imported.assert_not_called()


if __name__ == '__main__':
    unittest.main()
