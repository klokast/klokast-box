import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import codex_artifact as codex
from platform_updates import UpdateError


class CodexArtifactTests(unittest.TestCase):
    def release(self, version='1.2.3'):
        return {'tag_name': 'rust-v' + version, 'draft': False, 'prerelease': False,
                'assets': [{'name': codex.ASSET, 'size': 4,
                            'digest': 'sha256:' + hashlib.sha256(b'test').hexdigest(),
                            'browser_download_url': 'https://github.com/openai/codex/releases/download/rust-v' + version + '/' + codex.ASSET}]}

    def test_version_comes_from_current_metadata_and_binds_checksum(self):
        self.assertEqual(codex.selection(self.release())['version'], '1.2.3')
        self.assertEqual(codex.selection(self.release('8.9.10'))['version'], '8.9.10')
        record = codex.selection(self.release())
        with tempfile.TemporaryDirectory() as name:
            package = Path(name) / record['file']
            package.write_bytes(b'test')
            codex.verify(name, record)
            package.write_bytes(b'fail')
            with self.assertRaisesRegex(UpdateError, 'checksum failed'):
                codex.verify(name, record)

    def test_prerelease_foreign_download_and_missing_digest_refused(self):
        for change in ('draft', 'prerelease', 'url', 'digest', 'duplicate'):
            release = self.release()
            if change in ('draft', 'prerelease'): release[change] = True
            if change == 'url': release['assets'][0]['browser_download_url'] = 'https://example.com/codex'
            if change == 'digest': release['assets'][0].pop('digest')
            if change == 'duplicate': release['assets'].append(copy.deepcopy(release['assets'][0]))
            with self.subTest(change=change), self.assertRaises(UpdateError):
                codex.selection(release)
