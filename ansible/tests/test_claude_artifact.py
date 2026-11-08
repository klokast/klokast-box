"""Claude Code image inputs, binary extraction and runner discussion restore."""
import contextlib
import gzip
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import claude_artifact
import air_image_workspace as image
import codex_workspace as workspace
from platform_updates import UpdateError

SESSION = '11111111-1111-4111-8111-111111111111'


def package(version='2.1.311-r1', binary=b'#!/bin/sh\n'):
    """A minimal APK v2 layout: concatenated gzip tar segments."""
    output = io.BytesIO()
    for members in ((('.PKGINFO', ('pkgname = claude-code\npkgver = ' + version + '\narch = x86_64\n').encode()),),
                    (('usr/bin/claude', binary),)):
        segment = io.BytesIO()
        with tarfile.open(fileobj=segment, mode='w') as archive:
            for name, content in members:
                item = tarfile.TarInfo(name); item.size = len(content); item.mode = 0o755
                archive.addfile(item, io.BytesIO(content))
        output.write(gzip.compress(segment.getvalue()))
    return output.getvalue()


def frozen(directory, version='2.1.311-r1'):
    root = Path(directory) / 'claude'; root.mkdir()
    data = package(version)
    (root / ('claude-code-' + version + '.apk')).write_bytes(data)
    (root / 'APKINDEX.tar.gz').write_bytes(b'signed index')
    (root / claude_artifact.KEY_NAME).write_bytes(claude_artifact.KEY)
    return {'version': version, 'repository': claude_artifact.REPOSITORY,
            'file': 'claude/claude-code-' + version + '.apk', 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'index_sha256': hashlib.sha256(b'signed index').hexdigest()}


class Artifact(unittest.TestCase):
    def test_pinned_key_and_versions_compare_numerically(self):
        self.assertEqual(hashlib.sha256(claude_artifact.KEY).hexdigest(), claude_artifact.KEY_SHA256)
        self.assertGreater(claude_artifact.version('2.1.311-r1'), claude_artifact.version('2.1.99'))
        self.assertEqual(claude_artifact.version('2.1.311 '[:-1]), claude_artifact.version('2.1.311-r2'))
        with self.assertRaisesRegex(UpdateError, 'unsupported'):
            claude_artifact.version('2.1.311-beta')

    def test_frozen_package_index_and_key_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            record = frozen(directory)
            claude_artifact.verify(directory, record)
            for change in ({'repository': 'https://example.invalid/apk'}, {'file': 'claude/other.apk'},
                           {'sha256': '0' * 64}, {'index_sha256': '0' * 64}, {'extra': True}):
                with self.assertRaises(UpdateError):
                    claude_artifact.verify(directory, dict(record, **change))
            (Path(directory) / 'claude' / claude_artifact.KEY_NAME).write_bytes(b'other key')
            with self.assertRaisesRegex(UpdateError, 'pinned key'):
                claude_artifact.verify(directory, record)

    def test_unexpected_artifact_file_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            record = frozen(directory)
            (Path(directory) / 'claude/extra').write_text('')
            with self.assertRaisesRegex(UpdateError, 'set changed'):
                claude_artifact.verify(directory, record)

    def test_stable_selection_requires_one_exact_package(self):
        resolver = claude_artifact.Resolver.__new__(claude_artifact.Resolver)
        for output, expected in (('claude-code-2.1.311-r1\n', '2.1.311-r1'), ('', None),
                                 ('claude-code-2.1.311-r1\nclaude-code-2.1.312-r1\n', None),
                                 ('claude-code-2.1.311\n', None)):
            with patch.object(resolver, 'apk', return_value=output, create=True):
                if expected:
                    self.assertEqual(resolver.latest(), expected)
                else:
                    with self.assertRaises(UpdateError):
                        resolver.latest()


class BuildGuest(unittest.TestCase):
    def guest(self):
        loader = importlib.machinery.SourceFileLoader(
            'claude_build_guest', str(REPO / 'ansible/roles/vm-template-builder/files/vm-template-build-guest'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec); loader.exec_module(module)
        return module

    def test_only_the_verified_binary_is_installed_outside_the_package_database(self):
        guest = self.guest()
        with tempfile.TemporaryDirectory() as directory:
            inputs = Path(directory) / 'inputs'; inputs.mkdir()
            root = Path(directory) / 'root'; root.mkdir()
            record = frozen(inputs)
            with patch.object(guest, 'INPUT', inputs), patch.object(guest, 'ROOT', root), \
                    patch.object(guest, 'run', return_value='2.1.311 (Claude Code)') as run:
                guest.install_claude(record)
            self.assertEqual((root / 'opt/claude/bin/claude').read_bytes(), b'#!/bin/sh\n')
            self.assertEqual(str((root / 'usr/local/bin/claude').readlink()), '/opt/claude/bin/claude')
            self.assertEqual(json.loads((root / 'etc/klokast-claude.json').read_text()), record)
            self.assertFalse((root / 'lib/apk').exists())
            self.assertIn('--version', run.call_args.args[0])

    def test_changed_package_or_identity_is_refused(self):
        guest = self.guest()
        with tempfile.TemporaryDirectory() as directory:
            inputs = Path(directory) / 'inputs'; inputs.mkdir()
            root = Path(directory) / 'root'; root.mkdir()
            record = frozen(inputs)
            with patch.object(guest, 'INPUT', inputs), patch.object(guest, 'ROOT', root):
                with self.assertRaisesRegex(RuntimeError, 'checksum'):
                    guest.install_claude(dict(record, sha256='0' * 64))
                with self.assertRaisesRegex(RuntimeError, 'identity'):
                    guest.install_claude(dict(record, version='2.1.312-r1', file=record['file']))


class RunnerActivity(unittest.TestCase):
    def client(self, home, pid, status='idle', start='77', session=SESSION):
        (home / 'sessions').mkdir(parents=True, exist_ok=True)
        (home / 'sessions' / (str(pid) + '.json')).write_text(json.dumps(
            {'pid': pid, 'sessionId': session, 'cwd': '/home/agent/repo', 'procStart': start,
             'kind': 'interactive', 'status': status}))

    def test_only_a_matching_process_record_reports_its_status(self):
        import air_workspace
        rows = {40: dict(parent=1, owned=True, argv=['claude'], state='S', start='77'),
                41: dict(parent=1, owned=True, argv=['/usr/local/bin/claude', '--resume', SESSION], state='S', start='78'),
                42: dict(parent=1, owned=False, argv=['claude'], state='S', start='79')}
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.client(home, 40)
            self.client(home, 41, start='1')  # A reused PID cannot inherit another record.
            clients = air_workspace.claude_clients(rows, home)
        self.assertEqual(clients[40], {'session': SESSION, 'status': 'idle', 'cwd': '/home/agent/repo'})
        self.assertEqual(clients[41]['status'], 'unknown')
        self.assertNotIn(42, clients)

    def test_busy_or_unknown_claude_blocks_replacement(self):
        for status in ('busy', 'unknown', 'waiting'):
            with patch('air_workspace.processes', return_value={}), \
                    patch('air_workspace.claude_clients', return_value={40: {'session': SESSION, 'status': status}}):
                with self.assertRaisesRegex(RuntimeError, 'Claude Code discussion is active'):
                    image.inspect_claude()
        with patch('air_workspace.processes', return_value={}), \
                patch('air_workspace.claude_clients', return_value={40: {'session': SESSION, 'status': 'idle'}}):
            self.assertEqual(image.inspect_claude(), [SESSION])

    def test_idle_claude_client_is_allowed_but_its_tool_process_is_not(self):
        import air_workspace
        rows = {40: dict(parent=1, owned=True, argv=['claude'], state='S'),
                41: dict(parent=40, owned=True, argv=['/bin/bash', '-c', 'make'], state='S')}
        value = dict(attached=0, busy_pids=[40], codex_threads=[], sessions=[])
        native = dict(threads=[], latest=0, claude=[SESSION])
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(air_workspace, 'STATE', Path(directory)), \
                patch.object(air_workspace, 'status', side_effect=lambda: dict(value)), \
                patch.object(air_workspace, 'processes', return_value=rows), \
                patch.object(air_workspace, 'claude_clients', return_value={40: {'session': SESSION, 'status': 'idle'}}), \
                patch.object(air_workspace, 'run', return_value=Mock(stdout=json.dumps(native))):
            self.assertTrue(air_workspace.image_idle()['idle'])
            value['busy_pids'] = [40, 41]
            self.assertFalse(air_workspace.image_idle()['idle'])


class Workspace(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in (('ROOT', self.root / 'handoffs'), ('HOME', self.root / '.codex')):
            mock = patch.object(image.handoff, name, value); mock.start(); self.addCleanup(mock.stop)
        mock = patch.object(image.handoff, 'codex_binary', return_value='/test/codex'); mock.start(); self.addCleanup(mock.stop)
        (self.root / '.claude/projects/-home-agent-repo').mkdir(parents=True)

    def tmux(self, command):
        listing = '\t'.join(['main', '1', 'Claude', 'abcd,120x40,0,0,2', '1', '0', '%2', command,
                             '/home/agent', '✳ Work', '1', '120', '40'])
        def run(*args, check=True):
            if args[0] == 'list-panes':
                return Mock(returncode=0, stdout=listing + '\n', stderr='')
            if args[0] == 'display-message':
                return Mock(returncode=0, stdout='30\n', stderr='')
            return Mock(returncode=0, stdout='', stderr='')
        return run

    def test_same_machine_inventory_records_the_claude_discussion(self):
        (self.root / '.claude/projects/-home-agent-repo' / (SESSION + '.jsonl')).write_text('{}\n')
        rows = {30: dict(parent=1, argv=['-bash']), 40: dict(parent=30, argv=['claude'])}
        with patch.object(workspace, 'tmux', side_effect=self.tmux('claude')), \
                patch('air_workspace.processes', return_value=rows), \
                patch('air_workspace.claude_clients', return_value={40: {'session': SESSION, 'status': 'idle'}}):
            value = workspace.inventory('a' * 24, persist=False, refresh=True, same_host=True)
            with patch.object(workspace, 'LiveRPC', return_value=contextlib.nullcontext(Mock())), \
                    patch.object(workspace, 'loaded', return_value=[]), \
                    self.assertRaisesRegex(RuntimeError, 'stay on their runner'):
                workspace.inventory('a' * 24, persist=False, refresh=True)
        self.assertEqual(value['claude'], [SESSION])
        self.assertEqual(value['sessions'][0]['windows'][0]['panes'][0]['claude'], SESSION)
        self.assertEqual(value['discussions'], [])

    def test_missing_transcript_stops_the_checkpoint(self):
        rows = {30: dict(parent=1, argv=['-bash']), 40: dict(parent=30, argv=['claude'])}
        with patch.object(workspace, 'tmux', side_effect=self.tmux('claude')), \
                patch('air_workspace.processes', return_value=rows), \
                patch('air_workspace.claude_clients', return_value={40: {'session': SESSION, 'status': 'idle'}}):
            with self.assertRaisesRegex(RuntimeError, 'no saved transcript'):
                workspace.inventory('a' * 24, persist=False, refresh=True, same_host=True)

    def test_checkpoint_closes_only_represented_idle_claude_panes(self):
        pane = {'index': 0, 'source_pane': '%2', 'cwd': '/home/agent', 'discussion': None, 'active': True, 'claude': SESSION}
        value = {'sessions': [{'name': 'main', 'windows': [{'panes': [pane]}]}], 'discussions': [], 'claude': [SESSION]}
        fenced = []
        def frozen_tuis(panes, program='codex'):
            fenced.append((program, panes)); return contextlib.nullcontext()
        with patch.object(image, 'inspect', return_value={'threads': [], 'claude': [SESSION]}), \
                patch.object(workspace, 'inventory', return_value=value), \
                patch.object(workspace, 'frozen_tuis', side_effect=frozen_tuis), \
                patch.object(workspace, 'claude_live', return_value=set()), \
                patch.object(image, 'stop_daemon'), patch.object(workspace, 'tmux') as tmux:
            saved = image.checkpoint('a' * 24)
        self.assertEqual(saved['phase'], 'closed')
        self.assertIn(('claude', ['%2']), fenced)
        tmux.assert_called_once_with('respawn-pane', '-k', '-t', '%2', '-c', '/home/agent', '/bin/bash -l')
        with patch.object(image, 'inspect', return_value={'threads': [], 'claude': [SESSION, 'other']}), \
                patch.object(workspace, 'inventory', return_value=value), patch.object(image, 'stop_daemon') as stop:
            with self.assertRaisesRegex(RuntimeError, 'Claude Code has discussions outside'):
                image.checkpoint('b' * 24)
            stop.assert_not_called()

    def test_restore_resumes_the_same_claude_discussion(self):
        pane = {'index': 0, 'source_pane': '%2', 'cwd': '/home/agent', 'discussion': None, 'active': True, 'claude': SESSION}
        value = {'version': 1, 'operation': 'a' * 24, 'discussions': [], 'claude': [SESSION],
                 'sessions': [{'name': 'main', 'windows': [{'index': 1, 'name': 'Claude', 'layout': 'abcd,120x40,0,0,2',
                                                            'active': True, 'width': 120, 'height': 40, 'panes': [pane]}]}]}
        calls = []
        def tmux(*args, check=True):
            calls.append(args)
            if args[0] == 'has-session':
                return Mock(returncode=1)
            if args[0] in ('display-message', 'list-panes'):
                return Mock(returncode=0, stdout='1\n' if args[0] == 'display-message' else '%9\n')
            return Mock(returncode=0, stdout='')
        with patch.object(workspace, 'tmux', side_effect=tmux), \
                patch.object(workspace.handoff, 'codex_environment', return_value={}), \
                patch.object(workspace, 'claude_live', return_value={SESSION}), \
                patch.object(workspace, 'live_writers', return_value=set()), \
                patch.object(workspace, 'finish_restore', side_effect=lambda saved, receipt: saved):
            saved = workspace.restore(value, 'a' * 24, same_host=True)
        self.assertEqual(saved['claude'], [SESSION])
        respawn = next(c for c in calls if c[0] == 'respawn-pane')
        self.assertEqual(respawn[-1], '/usr/local/bin/claude --resume ' + SESSION)


if __name__ == '__main__':
    unittest.main()
