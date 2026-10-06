"""Resource CLI regression tests."""
import argparse
import io
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import yaml

from platform_resource_test_support import ResourceTestCase, REPO_ROOT, SCRIPT
import platform_resource_compiler as compiler
import platform_resource_runtime as runtime


class ResourceCliTest(ResourceTestCase):
    def test_controller_install_checks_modules_before_copying_entrypoint(self):
        tasks = yaml.safe_load((REPO_ROOT / 'ansible/roles/ops-controller/tasks/main.yml').read_text())
        check = next(task for task in tasks if task.get('name') ==
                     'Check the resource compiler modules in the controller checkout')
        install = next(task for task in tasks if task.get('name') ==
                       'Install the checked Platform resource compiler')
        self.assertLess(tasks.index(check), tasks.index(install))
        code = check['ansible.builtin.command']['argv'][-1]
        with tempfile.TemporaryDirectory() as empty:
            for checkout, expected in ((REPO_ROOT, 0), (Path(empty), 1)):
                with self.subTest(checkout=checkout):
                    relocated = code.replace('/home/smith/src/klokast/klokast-box', str(checkout))
                    result = subprocess.run([sys.executable, '-I', '-B', '-c', relocated],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stderr)

    def test_entrypoint_help_in_checkout_and_installed_layout(self):
        resolve = Path.resolve
        for installed in (False, True):
            def resolved(path, *args, **kwargs):
                if installed and path == SCRIPT:
                    return Path('/usr/local/sbin/platform-resources')
                return resolve(path, *args, **kwargs)

            with self.subTest(installed=installed), patch.object(Path, 'resolve', resolved):
                # Use the actual checkout modules, while checking installed path
                # selection without writing to /usr/local or the controller home.
                with patch.object(sys, 'path', list(sys.path)):
                    cli = runpy.run_path(str(SCRIPT))
                    expected = Path('/home/smith/src/klokast/klokast-box') if installed else REPO_ROOT
                    self.assertEqual(cli['REPO_ROOT'], expected)
                    self.assertEqual(sys.path[0], str(expected / 'ansible/lib'))
                    output = io.StringIO()
                    with patch.object(sys, 'argv', ['platform-resources', '--help']), \
                            patch.object(runtime.subprocess, 'run', side_effect=AssertionError('external execution')), \
                            redirect_stdout(output), self.assertRaises(SystemExit) as result:
                        cli['main']()
                    self.assertEqual(result.exception.code, 0)
                    self.assertIn('apply-box-access', output.getvalue())

    def test_mutation_stops_when_commit_or_controller_check_fails(self):
        for command in ('apply', 'apply-shared-guests', 'apply-box-access'):
            for refused in ('assert_approved_commit', 'require_active_controller'):
                with self.subTest(command=command, refused=refused):
                    args = SimpleNamespace(command=command, registry='/unused', app=[],
                        box=['boxa'], shared_guest_role=[], check=False,
                        magicdns_suffix='tail.test.ts.net', approved_commit='c' * 40)
                    with patch.object(self.cli, 'parse_args', return_value=args), \
                            patch.object(self.cli, 'registry_source_input'), \
                            patch.object(compiler, 'compile_registry', return_value={}), \
                            patch.object(compiler, 'compile_box_registry_plan', return_value={}), \
                            patch.object(runtime, 'assert_approved_commit') as commit, \
                            patch.object(runtime, 'require_active_controller') as controller, \
                            patch.object(runtime, 'vm_update_installation_lock') as lock, \
                            patch.object(runtime, 'run_ansible') as resources, \
                            patch.object(runtime, 'run_shared_guests') as shared, \
                            patch.object(runtime, 'run_box_access') as access:
                        (commit if refused == 'assert_approved_commit' else controller).side_effect = SystemExit(1)
                        with self.assertRaises(SystemExit):
                            self.cli.main()
                        lock.assert_not_called()
                        resources.assert_not_called()
                        shared.assert_not_called()
                        access.assert_not_called()

    def test_rejected_registry_source_stops_before_compilation(self):
        result = SimpleNamespace(returncode=0, stdout='{"kind":"unverified"}')
        with patch.object(sys, 'argv', ['platform-resources', 'show']), \
                patch.object(self.cli.subprocess, 'run', return_value=result), \
                patch.object(compiler, 'compile_registry') as compile_plan, \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.cli.main()
        compile_plan.assert_not_called()

    def test_resource_apply_keeps_the_installation_lock_through_reconciliation(self):
        args = SimpleNamespace(command='apply', registry='/unused', app=[],
                               magicdns_suffix='tail.test.ts.net', approved_commit='c' * 40)
        held = [False]
        @contextmanager
        def lock():
            held[0] = True
            try:
                yield
            finally:
                held[0] = False
        def checked(*_args, **_kwargs):
            self.assertTrue(held[0])
        with patch.object(self.cli, 'parse_args', return_value=args), \
                patch.object(self.cli, 'assert_command_scope'), \
                patch.object(self.cli, 'registry_source_input'), \
                patch.object(compiler, 'compile_registry', return_value={}), \
                patch.object(self.cli, 'requested_apps_present'), \
                patch.object(runtime, 'assert_approved_commit'), \
                patch.object(runtime, 'require_active_controller'), \
                patch.object(runtime, 'vm_update_installation_lock', side_effect=lock), \
                patch.object(runtime, 'run_shared_guests', side_effect=checked) as guests, \
                patch.object(runtime, 'run_ansible', side_effect=checked) as resources:
            self.cli.main()
        guests.assert_called_once()
        resources.assert_called_once()
        self.assertFalse(held[0])

    def test_app_scoped_apply_is_allowed(self):
        args = argparse.Namespace(command="apply", app=["nextcloud-v2"])
        self.cli.assert_command_scope(args)

    def test_app_scoped_verify_compiles_only_requested_app(self):
        path = self.write_registry({"schema_version": 1, "apps": {}})
        compile_calls = []

        def fake_compile(registry_path, app_filter, **kwargs):
            compile_calls.append((registry_path, list(app_filter)))
            return {"apps": {"immich": {}}}

        with patch.object(compiler, "compile_registry", side_effect=fake_compile), patch.object(self.cli, "registry_source_input", return_value={"registry": {}}):
            with patch.object(runtime, "run_ansible") as run_ansible:
                with patch.object(
                    sys,
                    "argv",
                    [
                        "platform-resources",
                        "--registry",
                        str(path),
                        "--app",
                        "immich",
                        "verify",
                    ],
                ):
                    self.cli.main()

        self.assertEqual(compile_calls, [(path, ["immich"])])
        run_ansible.assert_called_once()


if __name__ == "__main__":
    unittest.main()
