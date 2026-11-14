import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import controller_checkout as checkout
import ops_vpn_proxy


def run(*argv, cwd):
    subprocess.run(argv, cwd=cwd, check=True, capture_output=True)


class ControllerCheckout(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.upstream, self.repo = root / 'upstream', root / 'repo'
        self.upstream.mkdir()
        run('git', 'init', '-q', '-b', 'main', cwd=self.upstream)
        run('git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'one', cwd=self.upstream)
        run('git', 'clone', '-q', str(self.upstream), str(self.repo), cwd=root)
        run('git', 'remote', 'set-url', 'origin', checkout.ORIGIN, cwd=self.repo)
        run('git', 'config', 'url.' + str(self.upstream) + '.insteadOf', checkout.ORIGIN, cwd=self.repo)
        run('git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'two', cwd=self.upstream)
        self.identity = patch.multiple(checkout, socket=unittest.mock.DEFAULT, pwd=unittest.mock.DEFAULT)
        mocks = self.identity.start()
        mocks['socket'].gethostname.return_value = 'boxa-ops.example'
        mocks['pwd'].getpwuid.return_value.pw_name = 'smith'

    def tearDown(self):
        self.identity.stop()
        self.temp.cleanup()
        ops_vpn_proxy.ENABLED = False

    def head(self, path):
        return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()

    def test_fetch_is_a_public_download_and_the_checkout_fast_forwards(self):
        with patch.object(ops_vpn_proxy, 'public_download', side_effect=lambda action, description: action()) as download:
            value = checkout.update(self.repo)
        self.assertEqual(download.call_args.args[1], 'public checkout fetch')
        self.assertEqual((value['commit'], value['path'], value['controller']), (self.head(self.upstream), 'direct', 'boxa-ops'))

    def test_local_changes_or_divergence_leave_the_checkout_unchanged(self):
        (self.repo / 'local').write_text('edit')
        before = self.head(self.repo)
        with patch.object(ops_vpn_proxy, 'public_download') as download, self.assertRaisesRegex(RuntimeError, 'local changes'):
            checkout.update(self.repo)
        download.assert_not_called()
        (self.repo / 'local').unlink()
        run('git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'mine', cwd=self.repo)
        before = self.head(self.repo)
        with self.assertRaisesRegex(RuntimeError, 'cannot fast-forward'):
            checkout.update(self.repo)
        self.assertEqual(self.head(self.repo), before)

    def test_only_smith_on_a_controller_with_the_public_origin(self):
        with patch.object(checkout.pwd, 'getpwuid') as user:
            user.return_value.pw_name = 'agent'
            with self.assertRaisesRegex(RuntimeError, 'as smith'): checkout.update(self.repo)
        run('git', 'remote', 'set-url', 'origin', 'https://example.invalid/x.git', cwd=self.repo)
        with self.assertRaisesRegex(RuntimeError, 'origin is not'): checkout.update(self.repo)


if __name__ == '__main__':
    unittest.main()
