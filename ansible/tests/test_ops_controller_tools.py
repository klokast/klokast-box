"""Controller tool setup must not turn standby installation into activation."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ansible/lib"))
import platform_source as source
TASKS = ROOT / 'ansible/roles/ops-controller/tasks'


class ToolsWrapperTests(unittest.TestCase):
    def invoke(self, *options):
        with tempfile.TemporaryDirectory(prefix='controller-tools-test-') as directory:
            work = Path(directory)
            commands = work / 'bin'
            commands.mkdir()
            for name in ('ansible-playbook', 'ansible-inventory'):
                command = commands / name
                command.write_text('#!/bin/sh\nexit 0\n')
                command.chmod(0o755)
            env = dict(os.environ, PATH=str(commands) + ':' + os.environ['PATH'],
                       HOME=str(work), KLOKAST_MAGICDNS_SUFFIX='example.ts.net')
            return subprocess.run(
                ['bash', str(ROOT / 'ansible/bin/converge-ops-controller'),
                 '--box', 'boxa', '--connection-user', 'smith', '--dry-run-plan', *options],
                env=env, text=True, capture_output=True)

    def test_tools_only_selects_narrow_playbook_and_one_host(self):
        result = self.invoke('--tools-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('67-ops-controller-tools.yml', result.stdout)
        self.assertNotIn('67-ops-controller-converge.yml', result.stdout)
        self.assertIn('--limit boxa-ops', result.stdout)
        self.assertIn('ControlMaster=auto', result.stdout)
        self.assertIn('ControlPersist=60s', result.stdout)

    def test_default_keeps_full_controller_convergence(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('67-ops-controller-converge.yml', result.stdout)
        self.assertNotIn('67-ops-controller-tools.yml', result.stdout)
        self.assertIn('ControlMaster=auto', result.stdout)

    def test_tools_only_rejects_package_pruning(self):
        result = self.invoke('--tools-only', '--prune-package-drift')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not allow package pruning', result.stderr)
        self.assertNotIn('Command:', result.stdout)


class InstallationBoundaryTests(unittest.TestCase):
    def test_tools_playbook_uses_only_shared_local_installers(self):
        play = yaml.safe_load((ROOT / 'ansible/playbooks/67-ops-controller-tools.yml').read_text())[0]
        imports = [task['ansible.builtin.import_tasks'] for task in play['tasks']
                   if 'ansible.builtin.import_tasks' in task]
        self.assertEqual(imports, ['../roles/ops-controller/tasks/packages.yml',
                                  '../roles/ops-controller/tasks/ansible-toolchain.yml',
                                  '../roles/ops-controller/tasks/go-toolchain.yml',
                                  '../roles/ops-controller/tasks/development-tools.yml'])
        tasks = yaml.safe_load((TASKS / 'development-tools.yml').read_text())
        self.assertFalse(any('ansible.builtin.blockinfile' in task for task in tasks))
        activation = yaml.safe_load((TASKS / 'development-activation.yml').read_text())
        self.assertEqual(activation[0]['ansible.builtin.import_tasks'],
                         '../../platform-update-discovery/tasks/main.yml')

    def test_standby_source_reader_refuses_before_reading_instance(self):
        import subprocess as processes
        with patch.object(source.os, 'geteuid', return_value=1000), \
                patch.object(source.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='smith')), \
                patch.object(source.subprocess, 'run', return_value=processes.CompletedProcess(
                    [], 1, stdout=json.dumps({'active': False, 'configured': True, 'role': 'standby'}),
                    stderr='controller is not active')) as guard, \
                patch.object(source, 'read_json') as reader:
            with self.assertRaises(source.SourceError):
                source.snapshot()
        reader.assert_not_called()
        self.assertIn('--require-active', guard.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
