"""kk answers only from the published Instance main."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'klokast-dev/lib'))
import instance_current as current

ENV = dict(os.environ, GIT_AUTHOR_NAME='fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
           GIT_COMMITTER_NAME='fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')


def git(*args):
    return subprocess.run(['git', *args], env=ENV, check=True, capture_output=True, text=True).stdout.strip()


def commit(root, content, *, push):
    (root / current.FILE).write_text(content)
    git('-C', str(root), 'add', current.FILE)
    git('-C', str(root), 'commit', '--quiet', '-m', 'change')
    if push: git('-C', str(root), 'push', '--quiet', 'origin', 'HEAD:main')
    return git('-C', str(root), 'rev-parse', 'HEAD')


class InstanceCurrent(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        self.origin, self.worktree, self.other = base / 'origin.git', base / 'worktree', base / 'other'
        git('init', '--quiet', '--bare', '-b', 'main', str(self.origin))
        git('init', '--quiet', '-b', 'main', str(self.other))
        git('-C', str(self.other), 'remote', 'add', 'origin', str(self.origin))
        commit(self.other, '{"version": 1}', push=True)
        git('clone', '--quiet', str(self.origin), str(self.worktree))

    def test_current_worktree_is_unchanged(self):
        head = git('-C', str(self.worktree), 'rev-parse', 'HEAD')
        self.assertEqual(current.refresh(self.worktree), (head, None))

    def test_clean_worktree_behind_main_is_fast_forwarded(self):
        before = git('-C', str(self.worktree), 'rev-parse', 'HEAD')
        published = commit(self.other, '{"version": 2}', push=True)
        self.assertEqual(current.refresh(self.worktree), (published, before))
        self.assertEqual((self.worktree / current.FILE).read_text(), '{"version": 2}')

    def test_unpublished_or_unverifiable_state_stops(self):
        cases = (
            ('uncommitted changes', lambda: (self.worktree / current.FILE).write_text('{"version": 9}')),
            ('not on GitHub main', lambda: commit(self.worktree, '{"version": 3}', push=False)),
            ('not on branch main', lambda: git('-C', str(self.worktree), 'switch', '--quiet', '-c', 'other')),
            ('cannot read the published Instance from GitHub',
             lambda: git('-C', str(self.worktree), 'remote', 'set-url', 'origin', str(self.origin) + '-missing')),
        )
        for message, change in cases:
            with self.subTest(message=message):
                self.setUp()
                change()
                with self.assertRaisesRegex(current.StaleInstance, message):
                    current.refresh(self.worktree)

    def test_diverged_worktree_stops_and_keeps_its_commit(self):
        commit(self.other, '{"version": 2}', push=True)
        local = commit(self.worktree, '{"version": 3}', push=False)
        with self.assertRaisesRegex(current.StaleInstance, 'not on GitHub main'):
            current.refresh(self.worktree)
        self.assertEqual(git('-C', str(self.worktree), 'rev-parse', 'HEAD'), local)

    def test_missing_or_looping_path_reads_no_instance(self):
        loop = self.worktree.parent / 'loop'; loop.symlink_to(loop)
        for path in (loop, self.worktree.parent / 'absent'):
            with self.subTest(path=path), self.assertRaisesRegex(current.StaleInstance, '^cannot read Instance: '):
                current.refresh(path)


if __name__ == '__main__':
    unittest.main()
