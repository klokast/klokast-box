"""Boot recovery loads the pending engine, with a closed and checked file set."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


def loader():
    path=Path(__file__).resolve().parents[1]/'roles/router-update-recovery/files/router-update-transaction'
    spec=importlib.util.spec_from_loader('router_engine_loader_test',importlib.machinery.SourceFileLoader('router_engine_loader_test',str(path)))
    result=importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.loader=loader()

    def test_pending_engine_wins_over_current_deployment_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); (base/'pending.json').write_text('pending')
            with mock.patch.object(self.loader,'BASE',base),mock.patch.object(self.loader,'read',return_value={'request':{'engine_commit':'a'*40}}) as read:
                self.assertEqual(self.loader.engine_for('boot-recover',None),'a'*40)
                read.assert_called_once_with(base/'pending.json')

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
