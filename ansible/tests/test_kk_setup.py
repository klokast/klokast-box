"""Exercise MacBook setup with local Git repositories and an isolated home."""
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
URL = 'https://github.com/family/klokast-instance'
SSH_URL = 'git@github.com:family/klokast-instance.git'
BASH = os.environ.get('KLOKAST_TEST_BASH', shutil.which('bash'))


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='kk-setup-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home with spaces '$dollar"
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('GIT_', 'KLOKAST_'))}
        self.env.update(HOME=str(self.home), GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                        GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid',
                        GIT_TERMINAL_PROMPT='0')
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        for name, value in [('uname', 'Darwin'), ('id', '501')]:
            tool = self.tools / name
            tool.write_text(f'#!/bin/sh\nprintf "%s\\n" "{value}"\n')
            tool.chmod(0o755)
        self.env['PATH'] = f'{self.tools}:{os.environ["PATH"]}'
        self.source = self.root / 'public source'
        self.source.mkdir()
        self.git(self.source, 'init', '-b', 'main')
        for relative in ('klokast-dev/bin/kk', 'klokast-dev/bin/prepare-private-instance-worktree',
                         'klokast-dev/lib/setup.sh'):
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        kk = self.source / 'klokast-dev/bin/kk'
        kk.write_text(kk.read_text().replace('doctor() {', '''doctor() {
  printf '%s\\n' "$*" >> "$HOME/doctor-arguments"
  return "${KLOKAST_TEST_DOCTOR_EXIT:-0}"
''', 1))
        self.commit(self.source)
        self.repo = self.root / "public checkout '$dollar"
        self.git(self.root, 'clone', str(self.source), str(self.repo))
        self.kk = self.repo / 'klokast-dev/bin/kk'
        self.private = self.root / 'private source'
        self.private.mkdir()
        self.git(self.private, 'init', '-b', 'main')
        (self.private / 'klokast-instance.json').write_text(json.dumps({'schema-version': 1}))
        self.commit(self.private)
        self.env.update(GIT_CONFIG_COUNT='1',
                        GIT_CONFIG_KEY_0=f'url.{self.private.as_uri()}.insteadOf',
                        GIT_CONFIG_VALUE_0=SSH_URL)
        self.instance = self.home / 'src/private-klokast/klokast-instance'
        self.profile = self.home / '.zprofile'

    def git(self, path, *args):
        return subprocess.run(['git', '-C', str(path), *args], env=self.env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, path):
        self.git(path, 'add', '.')
        self.git(path, 'commit', '-m', 'Fixture')

    def setup(self, *args):
        return subprocess.run([BASH, str(self.kk), 'setup', *args], env=self.env,
                              input='', capture_output=True, text=True)

    def install(self):
        result = self.setup('--instance', URL)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_clone_and_repeat_preserve_private_edits_and_profile(self):
        self.profile.write_text('export KEEP=yes\nexport KLOKAST_INSTANCE=/old\n')
        self.profile.chmod(0o640)
        self.install()
        content = self.profile.read_bytes()
        self.assertIn(b'export KEEP=yes', content)
        self.assertEqual(content.count(b'# >>> kk setup >>>'), 1)
        self.assertEqual(self.profile.stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.home / 'doctor-arguments').read_text(), '--install\n')
        self.assertEqual(self.git(self.instance, 'config', '--get', 'remote.origin.url'), SSH_URL)
        (self.instance / 'klokast-instance.json').write_text('{"local": true}')
        self.env['KLOKAST_INSTANCE'] = '/stale/environment'
        self.install()
        self.assertEqual(self.profile.read_bytes(), content)
        self.assertEqual((self.instance / 'klokast-instance.json').read_text(), '{"local": true}')
        # Quoting must preserve literal shell metacharacters in both paths.
        shells = [BASH]
        if shutil.which('zsh'):
            shells.append(shutil.which('zsh'))
        for shell in shells:
            with self.subTest(shell=shell):
                result = subprocess.run([shell, '-c', '. "$HOME/.zprofile"; printf "%s\\n" "$KLOKAST_INSTANCE"; command -v kk'],
                                        env=self.env, capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.splitlines(), [str(self.instance), str(self.kk)])

    def test_updated_helper_runs_and_git_suffix_is_accepted(self):
        helper = self.source / 'klokast-dev/lib/setup.sh'
        helper.write_text(helper.read_text().replace('set -euo pipefail',
                         'set -euo pipefail\nprintf updated > "$HOME/updated-helper"'))
        self.commit(self.source)
        result = self.setup('--instance', URL + '.git')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / 'updated-helper').read_text(), 'updated')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.git(self.source, 'rev-parse', 'HEAD'))

    def test_symlink_profile_and_block_moved_last(self):
        target = self.home / 'dotfiles-profile'
        target.write_text('# >>> kk setup >>>\nold\n# <<< kk setup <<<\nexport KEEP=after\n')
        self.profile.symlink_to(target)
        self.install()
        self.assertTrue(self.profile.is_symlink())
        self.assertTrue(target.read_text().startswith('export KEEP=after\n'))
        self.assertTrue(target.read_text().endswith('# <<< kk setup <<<\n'))

    def test_broken_markers_do_not_overwrite_profile(self):
        for content in ('# >>> kk setup >>>\n',
                        '# <<< kk setup <<<\n# >>> kk setup >>>\n',
                        '# >>> kk setup >>>\n# >>> kk setup >>>\n# <<< kk setup <<<\n'):
            with self.subTest(content=content):
                self.profile.write_text(content)
                result = self.setup('--instance', URL)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('markers', result.stderr)
                self.assertEqual(self.profile.read_text(), content)
                self.assertEqual(list(self.home.glob('.kk-zprofile-*')), [])

    def test_doctor_failure_stops_before_clone_or_profile(self):
        self.env['KLOKAST_TEST_DOCTOR_EXIT'] = '2'
        self.assertEqual(self.setup('--instance', URL).returncode, 2)
        self.assertFalse(self.instance.exists())
        self.assertFalse(self.profile.exists())

    def test_conflicting_destination_and_invalid_instance(self):
        self.instance.mkdir(parents=True)
        result = self.setup('--instance', URL)
        self.assertIn('not a standalone Git checkout', result.stderr)
        self.assertFalse(self.profile.exists())
        self.git(self.instance, 'init', '-b', 'main')
        self.git(self.instance, 'remote', 'add', 'origin', 'git@github.com:other/klokast-instance.git')
        result = self.setup('--instance', URL)
        self.assertIn('origin does not match', result.stderr)
        self.assertFalse(self.profile.exists())
        self.git(self.instance, 'remote', 'set-url', 'origin', URL)
        for content in ('invalid', '[]'):
            (self.instance / 'klokast-instance.json').write_text(content)
            self.assertEqual(self.setup('--instance', URL).returncode, 2)
            self.assertFalse(self.profile.exists())
        (self.instance / 'klokast-instance.json').unlink()
        self.assertEqual(self.setup('--instance', URL).returncode, 2)

    def test_public_git_failures_stop_before_doctor(self):
        (self.repo / 'uncommitted').write_text('local')
        self.assertIn('local changes', self.setup('--instance', URL).stderr)
        (self.repo / 'uncommitted').unlink()
        self.git(self.repo, 'checkout', '--detach')
        self.assertIn('detached HEAD', self.setup('--instance', URL).stderr)
        self.git(self.repo, 'switch', 'main')
        self.git(self.repo, 'branch', '--unset-upstream')
        self.assertIn('upstream', self.setup('--instance', URL).stderr)
        self.git(self.repo, 'branch', '--set-upstream-to=origin/main')
        (self.repo / 'local').write_text('local')
        self.commit(self.repo)
        (self.source / 'remote').write_text('remote')
        self.commit(self.source)
        self.assertIn('update failed', self.setup('--instance', URL).stderr)
        self.assertFalse((self.home / 'doctor-arguments').exists())
        self.assertFalse(self.profile.exists())

    def test_invalid_arguments_and_help_have_no_effects(self):
        for args in (['--instance'], ['--instance', ''], ['--bogus'],
                     ['--instance', '/local/path'], ['--instance', URL + '?token=secret'],
                     ['--instance', URL, '--instance', URL]):
            with self.subTest(args=args):
                self.assertEqual(self.setup(*args).returncode, 2)
        self.assertIn('no terminal', self.setup().stderr)
        result = subprocess.run([BASH, str(self.kk), '--instance', '/local', 'setup'],
                                env=self.env, capture_output=True, text=True)
        self.assertIn('global --instance', result.stderr)
        for name, value, message in [('uname', 'Linux', 'macOS'), ('id', '0', 'without sudo')]:
            tool = self.tools / name
            original = tool.read_text()
            tool.write_text(f'#!/bin/sh\necho {value}\n')
            self.assertIn(message, self.setup('--instance', URL).stderr)
            self.assertEqual(self.setup('--help').returncode, 0)
            tool.write_text(original)
        self.assertFalse((self.home / 'doctor-arguments').exists())
        self.assertFalse(self.profile.exists())

    def test_interactive_repository_prompt(self):
        master, slave = pty.openpty()
        process = subprocess.Popen([BASH, str(self.kk), 'setup'], env=self.env,
                                   stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        try:
            output = b''
            deadline = time.monotonic() + 15
            while b'Private Instance repository URL:' not in output:
                self.assertLess(time.monotonic(), deadline, output)
                if select.select([master], [], [], 0.2)[0]:
                    output += os.read(master, 4096)
            os.write(master, (URL + '\n').encode())
            self.assertEqual(process.wait(timeout=15), 0)
            self.assertTrue(self.profile.exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)


if __name__ == '__main__':
    unittest.main()
