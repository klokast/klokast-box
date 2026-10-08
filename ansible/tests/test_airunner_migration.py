"""Exercise the data-preservation boundary, including interrupted copies."""
import ast
import contextlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_infrastructure_guest import load, ROOT


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.module = load('runner_migration', ROOT / 'ansible/roles/airunner-migration/files/airunner-migrate')
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.module.STATE = self.root / 'state'
        self.module.SOURCE_AUTOSTART = self.root / 'default-airunner'
        self.module.SOURCE_AUTOSTART.symlink_to('/etc/init.d/airunner')
        self.destination = self.root / 'destination'
        self.destination.mkdir()
        self.copies = 0
        self.checksum_copies = 0
        self.tree = 'empty'
        self.restored = False
        def copy(box, source, destination, **kwargs):
            if kwargs.get('verify'): return
            self.copies += 1
            if kwargs.get('checksum'): self.checksum_copies += 1
            shutil.copytree(source, self.destination, dirs_exist_ok=True)
            self.tree = 'copied'
        def remote(box, program, *args, **kwargs):
            compile(program, 'remote-python', 'exec')
            if program == self.module.VALIDATE: return '{"databases":1,"ownership":true,"sqlite":true}'
            return '{}'
        for target, replacement in (
            ('copy_home', copy), ('remote', remote), ('fence', lambda *_: '/bin/bash'),
            ('unfence', lambda *_: None),
            ('inspect', lambda *_: {'tree': self.tree, 'bytes': 0, 'free': 100 * 1024**3}),
        ):
            p = patch.object(self.module, target, replacement); p.start(); self.addCleanup(p.stop)
        self.uid, self.gid = os.getuid(), os.getgid()

    def migrate(self, phase, interrupt=False):
        return self.module.migrate('boxa', phase, True, self.uid, self.gid, interrupt)

    def test_interrupted_copy_retries_without_losing_uncommitted_files_or_sessions(self):
        with self.assertRaisesRegex(RuntimeError, 'injected'): self.migrate('precopy', True)
        self.assertEqual(self.migrate('status')['phase'], 'copy-failed')
        self.migrate('precopy')
        result = self.migrate('cutover')
        self.assertEqual(result['phase'], 'verified')
        self.assertEqual(self.checksum_copies, 1)
        self.assertEqual((self.destination / 'src/example/uncommitted.txt').read_text(), 'synthetic uncommitted work\n')
        with sqlite3.connect(self.destination / '.codex/state.sqlite') as db:
            self.assertEqual(db.execute('SELECT text FROM sessions').fetchone(), ('synthetic session',))
        self.assertEqual((self.destination.stat().st_uid, self.destination.stat().st_gid), (self.uid, self.gid))

    def test_new_destination_writes_block_another_copy(self):
        self.migrate('precopy')
        self.tree = 'newer-session'
        with self.assertRaisesRegex(RuntimeError, 'destination changed'): self.migrate('cutover')
        self.assertEqual(self.copies, 1)

    def test_completed_cutover_never_overwrites_destination(self):
        self.migrate('precopy'); self.migrate('cutover')
        with self.assertRaisesRegex(RuntimeError, 'writes are enabled'): self.migrate('precopy')
        self.assertEqual(self.copies, 2)

    def test_failure_after_write_boundary_preserves_both_copies(self):
        self.migrate('precopy')
        with patch.object(self.module, 'unfence', side_effect=RuntimeError('disconnected')):
            with self.assertRaisesRegex(RuntimeError, 'disconnected'): self.migrate('cutover')
        self.assertEqual(self.migrate('status')['phase'], 'destination-enabled')
        self.assertTrue((self.module.STATE / 'boxa/synthetic-source/.codex/state.sqlite').exists())
        self.assertTrue((self.destination / '.codex/state.sqlite').exists())
        self.assertEqual(self.migrate('verify')['phase'], 'verified')

    def test_failed_final_copy_restores_source_before_destination_use(self):
        source = self.root / 'source'; source.mkdir()
        work = self.module.STATE / 'boxa'; work.mkdir(parents=True)
        self.module.save(work / 'migration.json', {
            'box':'boxa', 'phase':'precopy', 'synthetic':False,
            'destination_tree':'empty', 'login_shell':'/bin/bash',
        })
        commands = []
        def run(argv, **kwargs):
            commands.append(argv)
            return SimpleNamespace(returncode=1 if argv[0]=='pgrep' else 0)
        with patch.object(self.module.socket, 'gethostname', return_value='boxa-ops'), \
                patch.object(self.module, 'Path', side_effect=lambda p: source if str(p)=='/home/agent' else Path(p)), \
                patch.object(self.module, 'required_bytes', return_value=0), \
                patch.object(self.module, 'run', side_effect=run), \
                patch.object(self.module, 'copy_home', side_effect=RuntimeError('network interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'network interrupted'):
                self.module.migrate('boxa', 'cutover', False, self.uid, self.gid)
        self.assertIn(['rc-service','airunner','stop'], commands)
        self.assertIn(['rc-service','airunner','start'], commands)
        self.assertLess(commands.index(['rc-update','del','airunner','default']),
                        commands.index(['rc-service','airunner','stop']))
        self.assertIn(['rc-update','add','airunner','default'], commands)
        state = json.loads((work / 'migration.json').read_text())
        self.assertTrue(state['source_autostart'])
        self.assertTrue(state['source_autostart_restored'])
        self.assertTrue(state['source_restored'])

    def test_working_write_boundary_keeps_source_boot_startup_disabled(self):
        source = self.root / 'source'; source.mkdir()
        work = self.module.STATE / 'boxa'; work.mkdir(parents=True)
        self.module.save(work / 'migration.json', {
            'box':'boxa', 'phase':'precopy', 'synthetic':False,
            'destination_tree':'empty', 'login_shell':'/bin/bash',
        })
        commands = []
        def run(argv, **kwargs):
            commands.append(argv)
            return SimpleNamespace(returncode=1 if argv[0]=='pgrep' else 0)
        with patch.object(self.module.socket, 'gethostname', return_value='boxa-ops'), \
                patch.object(self.module, 'Path', side_effect=lambda p: source if str(p)=='/home/agent' else Path(p)), \
                patch.object(self.module, 'required_bytes', return_value=0), \
                patch.object(self.module, 'run', side_effect=run), \
                patch.object(self.module, 'archives'), \
                patch.object(self.module, 'authenticate', side_effect=RuntimeError('authentication unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'authentication unavailable'):
                self.module.migrate('boxa', 'cutover', False, self.uid, self.gid)
        self.assertIn(['rc-update','del','airunner','default'], commands)
        self.assertNotIn(['rc-update','add','airunner','default'], commands)
        self.assertNotIn(['rc-service','airunner','start'], commands)
        self.assertEqual(json.loads((work / 'migration.json').read_text())['phase'], 'destination-enabled')

    def test_capacity_failure_precedes_copy(self):
        with patch.object(self.module, 'inspect', return_value={'tree':'empty','bytes':0,'free':0}):
            with self.assertRaisesRegex(RuntimeError, 'do not fit'): self.migrate('precopy')
        self.assertEqual(self.copies, 0)

    def test_checksum_or_ownership_difference_is_a_failed_copy(self):
        with patch.object(self.module, 'run', return_value=SimpleNamespace(stdout='>fc........ session\n')):
            # Call the original implementation instead of the simulated transport.
            original = load('runner_migration_checksum', ROOT / 'ansible/roles/airunner-migration/files/airunner-migrate')
            with patch.object(original, 'run', return_value=SimpleNamespace(stdout='>fc........ session\n')):
                with self.assertRaisesRegex(RuntimeError, 'differs from source'):
                    original.copy_home('boxa', self.root, '/home/agent', verify=True)

    def test_capacity_count_excludes_caches_and_does_not_follow_symlinks(self):
        data = self.root / 'source'; data.mkdir()
        (data / 'work').write_bytes(b'work')
        os.link(data / 'work', data / 'hardlink')
        (data / 'link').symlink_to('/etc')
        (data / '.cache').mkdir(); (data / '.cache/cache').write_bytes(b'cache')
        self.assertEqual(self.module.required_bytes(data, self.module.EXCLUDES), 4)

    def test_sqlite_validation_checks_actual_database_and_numeric_identity(self):
        source = self.module.synthetic_source(self.root, self.uid, self.gid)
        program = self.module.VALIDATE.replace("pwd.getpwnam('agent')", 'pwd.getpwuid(os.getuid())')
        result = subprocess.run(['python3', '-', str(source), str(self.uid), str(self.gid)],
                                input=program, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        (source / '.codex/state.sqlite').write_bytes(b'corrupt database')
        result = subprocess.run(['python3', '-', str(source), str(self.uid), str(self.gid)],
                                input=program, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_embedded_remote_programs_compile(self):
        tree = ast.parse((ROOT / 'ansible/roles/airunner-migration/files/airunner-migrate').read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith('import '):
                compile(node.value, 'embedded-program', 'exec')

    def archive_program(self, mounted_source=None):
        source = self.root / 'archives'; source.mkdir()
        mount = self.root / 'archive-mount'; mount.mkdir()
        fstab = self.root / 'fstab'; fstab.write_text('/dev/xvda / ext4 defaults 0 1\n')
        mountinfo = self.root / 'mountinfo'
        mountinfo.write_text('' if mounted_source is None else
                             f'36 27 202:0 {mounted_source} {mount} rw,relatime - ext4 /dev/xvda rw\n')
        program = self.module.ARCHIVE_MOUNT
        for old, new in (('/var/lib/klokast/runner-archives', source),
                         ('/home/agent/.codex-archives', mount),
                         ('/etc/fstab', fstab), ('/proc/self/mountinfo', mountinfo)):
            program = program.replace(old, str(new))
        return program, source, mount, fstab

    def test_archive_convergence_does_not_stack_same_filesystem_bind_mounts(self):
        program, source, mount, fstab = self.archive_program(str(self.root / 'archives'))
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=0)) as command, \
                patch('os.statvfs', return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
            exec(compile(program, 'archive-mount', 'exec'), {})
        self.assertNotIn(['mount', '--bind', str(source), str(mount)],
                         [call.args[0] for call in command.call_args_list])
        self.assertIn(['mount', '-o', 'remount,bind,ro', str(mount)],
                      [call.args[0] for call in command.call_args_list])
        self.assertIn(f'{source} {mount} none bind,ro 0 0', fstab.read_text())

    def test_archive_convergence_refuses_foreign_mount(self):
        program, _, _, _ = self.archive_program('/other-data')
        with patch('subprocess.run') as command, \
                self.assertRaisesRegex(SystemExit, 'unexpected mount'):
            exec(compile(program, 'archive-mount', 'exec'), {})
        command.assert_not_called()

    def test_archive_convergence_requires_read_only_result(self):
        program, _, _, _ = self.archive_program()
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=0)), \
                patch('os.statvfs', return_value=SimpleNamespace(f_flag=0)), \
                self.assertRaisesRegex(SystemExit, 'not read-only'):
            exec(compile(program, 'archive-mount', 'exec'), {})


if __name__ == '__main__': unittest.main()
