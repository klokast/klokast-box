import contextlib
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import alpine_mirrors as m
import vm_template_inputs as inputs

TUNA = 'https://mirrors.tuna.tsinghua.edu.cn/alpine'
USTC = 'https://mirrors.ustc.edu.cn/alpine'
CURRENT = ['a' * 64, 'b' * 64]
OLD = ['0' * 64, '0' * 64]


class MirrorTests(unittest.TestCase):
    def choice(self):
        return {'kind': m.KIND, 'policy': m.POLICY, 'box': 'boxa', 'country': 'CN',
                'selected_at': '2026-10-13T12:00:00Z', 'primary': TUNA,
                'fallback': USTC, 'measurements': []}

    def measuring(self, speeds, stale=(), unanswered=()):
        """Replace the network: a faster mirror also answers first."""
        def probe(mirror, branch):
            if speeds[mirror] is None: raise RuntimeError('timeout')
            return {'mirror': mirror, 'bytes_per_second': speeds[mirror],
                    'indexes': list(OLD if mirror in stale else CURRENT)}
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(m, 'catalog', return_value=sorted(speeds)))
        stack.enter_context(patch.object(m, 'first_byte', side_effect=lambda mirror, branch:
                                         None if mirror in unanswered else 1 / (speeds[mirror] or 1)))
        stack.enter_context(patch.object(m, 'reference', return_value=list(CURRENT)))
        return stack, stack.enter_context(patch.object(m, 'probe', side_effect=probe))

    def test_speed_selects_among_official_mirrors_with_current_indexes(self):
        stack, probes = self.measuring({m.ORIGIN: 500, TUNA: 900, USTC: 300}, stale={TUNA})
        with stack:
            value = m.measure('boxa', 'CN')
        self.assertEqual((value['primary'], value['fallback'], value['country']), (m.ORIGIN, USTC, 'CN'))
        old = next(row for row in value['measurements'] if row['mirror'] == TUNA)
        self.assertFalse(old['current'])
        self.assertNotIn('indexes', old)

    def test_only_the_nearest_answering_mirrors_are_sampled(self):
        speeds = {f'https://m{i}.example/alpine': 100 + i for i in range(20)}
        stack, probes = self.measuring(speeds, unanswered={'https://m19.example/alpine'})
        with stack:
            value = m.measure('boxa')
        self.assertEqual({call.args[0] for call in probes.call_args_list},
                         {f'https://m{i}.example/alpine' for i in range(11, 19)})
        self.assertEqual(value['primary'], 'https://m18.example/alpine')

    def test_an_index_published_during_the_samples_is_current(self):
        stack, probes = self.measuring({m.ORIGIN: 500, TUNA: 900, USTC: 300})
        with stack, patch.object(m, 'reference', side_effect=[list(OLD), list(CURRENT)]):
            self.assertEqual(m.measure('boxa')['primary'], TUNA)

    def test_one_current_mirror_is_not_enough(self):
        stack, probes = self.measuring({m.ORIGIN: 500, TUNA: 900, USTC: None}, stale={TUNA})
        with stack, self.assertRaisesRegex(RuntimeError, 'fewer than two of the 3 nearest'):
            m.measure('boxa')

    def test_catalog_keeps_https_mirrors_in_the_rewritable_form(self):
        entries = [{'urls': ['http://a.example/alpine/', 'https://a.example/alpine/']},
                   {'urls': ['https://b.example/pub/alpine/', 'https://B.example/alpine', 'https://c.example/alpine']},
                   'invalid', {'urls': 'https://d.example/alpine'}, {'urls': [42, 'https://e.example/alpine?x']}]
        def download(url, output, *limits):
            self.assertEqual(url, m.CATALOG)
            output.write_text(json.dumps(entries))
        with patch.object(m, 'download', side_effect=download):
            self.assertEqual(m.catalog(), sorted([m.ORIGIN, 'https://a.example/alpine', 'https://c.example/alpine']))
        for content in ('[]', '{"urls": []}', 'not json'):
            with patch.object(m, 'download', side_effect=lambda url, output, *limits: output.write_text(content)):
                with self.assertRaises(RuntimeError): m.catalog()

    def test_a_full_index_and_the_cdn_range_identify_the_same_signed_version(self):
        index = b'\x1f\x8b' + b's' * 746 + b'd' * 9000
        def download(url, output, seconds, minimum, size, head=False):
            data = new if 'mirror.example' in url else index
            output.write_bytes(data[:m.HEAD] if head else data)
        # A changed tail is out of scope here: APK rejects it against the signature.
        for new, current in ((index[:m.HEAD] + b'x' * 9000, True), (b'\x1f\x8b' + b'o' * 746 + b'd' * 9000, False)):
            with tempfile.TemporaryDirectory() as temp, patch.object(m, 'download', side_effect=download):
                mirror = m.indexes('https://mirror.example/alpine', 'v3.23', temp, 12, 32768)
                self.assertEqual(mirror[0], 2 * len(new))
                self.assertEqual(mirror[1] == m.reference('v3.23'), current)
        with tempfile.TemporaryDirectory() as temp, patch.object(m, 'download', side_effect=lambda url, output, *a, **k: output.write_bytes(b'<html>' * 1000)):
            with self.assertRaisesRegex(RuntimeError, 'non-APK'): m.indexes(m.ORIGIN, 'v3.23', temp, 12, 32768)

    def test_unavailable_mirrors_never_replace_a_valid_record(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'alpine.json'; m.publish(path, self.choice())
            original = path.read_bytes()
            stack, probes = self.measuring({m.ORIGIN: None, TUNA: None, USTC: None})
            with stack, patch.object(m, 'require_storage'), patch('socket.gethostname', return_value='boxa-ops'), \
                    patch.object(m.Path, 'read_text', autospec=True, side_effect=lambda p: '3.23.3' if str(p)=='/etc/alpine-release' else original.decode()):
                with self.assertRaisesRegex(RuntimeError, 'fewer than two'):
                    m.select('boxa', True, 'CN', path)
            self.assertEqual(path.read_bytes(), original)

    def test_cache_reads_do_not_probe_and_wrong_box_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'alpine.json'; m.publish(path, self.choice())
            with patch.object(m, 'require_storage'), patch.object(m, 'probe') as probe, \
                    patch('socket.gethostname', return_value='boxa-ops'):
                self.assertEqual(m.select('boxa', path=path), self.choice())
                probe.assert_not_called()
                with self.assertRaises(RuntimeError): m.read('boxb', path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_changed_instance_location_measures_again(self):
        read_text = m.Path.read_text
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'alpine.json'; m.publish(path, self.choice())
            stack, probes = self.measuring({m.ORIGIN: 900, TUNA: 10, USTC: 10})
            with stack, patch.object(m, 'require_storage'), patch('socket.gethostname', return_value='boxa-ops'), \
                    patch.object(m.Path, 'read_text', autospec=True, side_effect=lambda p: '3.23.3' if str(p)=='/etc/alpine-release' else read_text(p)):
                # The same location, or no location from the caller, reuses the record.
                self.assertEqual(m.select('boxa', country='CN', path=path), self.choice())
                self.assertEqual(m.select('boxa', path=path), self.choice())
                probes.assert_not_called()
                value = m.select('boxa', country='FR', path=path)
                self.assertEqual(probes.call_count, 3)
                self.assertEqual((value['country'], value['primary']), ('FR', 'https://dl-cdn.alpinelinux.org/alpine'))
                self.assertEqual(m.read('boxa', path), value)
                # A refresh without a location keeps the recorded one.
                self.assertEqual(m.select('boxa', True, path=path)['country'], 'FR')

    def test_missing_mount_and_unapproved_transport_are_rejected(self):
        with patch.object(m.os.path, 'ismount', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'not mounted'): m.require_storage()
        for mirror in ('http://mirrors.tuna.tsinghua.edu.cn/alpine', 'file:///tmp/alpine', 'https://mirror.example/pub/alpine',
                       'https://Mirror.example/alpine', 'https://mirror.example/alpine/', 'https://user@mirror.example/alpine', 42):
            value = self.choice(); value['primary'] = mirror
            with self.assertRaises(RuntimeError): m.validate(value)

    def test_direct_environment_removes_all_proxy_variants(self):
        with patch.dict(os.environ, {'HTTP_PROXY':'proxy', 'https_proxy':'proxy', 'ALL_PROXY':'proxy', 'PATH':'/bin'}, clear=True):
            self.assertEqual(m.direct_environment(), {'PATH':'/bin'})

    def test_alpine_metadata_ignores_the_github_proxy_environment(self):
        import platform_update_metadata as metadata
        from unittest.mock import MagicMock
        response=MagicMock()
        response.open.return_value.__enter__.return_value.read.return_value=b'{}'
        with patch.object(metadata.urllib.request,'build_opener',return_value=response) as factory:
            self.assertEqual(metadata.fetch_json('https://alpinelinux.org/releases.json')[0],{})
        self.assertEqual(factory.call_args.args[0].proxies,{})

    def test_repository_choice_preserves_release_and_rejects_mixed_origins(self):
        value = m.repositories(self.choice()['primary'], 'v3.24')
        self.assertTrue(m.valid_repositories(value, 'v3.24'))
        self.assertFalse(m.valid_repositories(value, 'v3.23'))
        self.assertFalse(m.valid_repositories([value[0], inputs.ORIGIN+'/v3.24/community'], 'v3.24'))
        for other in ('https://mirror.example/pub/alpine', 'http://mirror.example/alpine'):
            self.assertFalse(m.valid_repositories([other + '/v3.24/main', other + '/v3.24/community'], 'v3.24'))
        self.assertFalse(m.valid_repositories('https://mirror.example/alpine/v3.24/main', 'v3.24'))
        self.assertFalse(m.valid_repositories([], 'v3.24'))

    def test_freeze_retries_once_before_publication_and_keeps_other_failures(self):
        for error, expected in ((inputs.AlpineDownloadError('download'), 2), (inputs.UpdateError('bad signature'), 1)):
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / 'inputs'
                calls = []
                def freeze(path, *args, **kwargs):
                    calls.append(kwargs['mirror'])
                    if len(calls) == 1: raise error
                    path.mkdir(); (path/'inputs.json').write_text('{}')
                    return {'verified':True}
                # The fallback serves this download only; the saved selection stays.
                with patch.object(m, 'read', return_value=self.choice()), patch.object(inputs, 'freeze', side_effect=freeze), \
                        patch.object(m, 'publish') as publish:
                    if expected == 2:
                        self.assertEqual(inputs.freeze_local(output, {}, 'v3.24', 'a'*40), {'verified':True})
                        self.assertTrue((output/'inputs.json').is_file())
                    else:
                        with self.assertRaises(inputs.UpdateError): inputs.freeze_local(output, {}, 'v3.24', 'a'*40)
                        self.assertFalse(output.exists())
                    publish.assert_not_called()
                self.assertEqual(len(calls), expected)

    def test_frozen_repository_origin_keeps_the_recorded_mirror(self):
        import re, yaml
        from jinja2.nativetypes import NativeEnvironment
        try:
            from ansible.plugins.filter.core import regex_replace
        except ModuleNotFoundError:
            self.skipTest('requires Ansible; run on a controller')
        tasks=yaml.safe_load((Path(__file__).resolve().parents[1]/'roles/alpine-mirror/tasks/main.yml').read_text())
        task=next(t for t in tasks if t['name'].startswith("Keep the image's recorded"))
        env=NativeEnvironment()
        import base64
        env.filters.update(b64decode=lambda v:base64.b64decode(v).decode(),regex_replace=regex_replace)
        env.tests['match']=lambda v,p:re.match(p,v) is not None
        for mirror in (m.ORIGIN, TUNA, 'https://ftp2.crifo.org/alpine'):
            content=base64.b64encode(('\n'.join(m.repositories(mirror,'v3.24'))+'\n').encode()).decode()
            value=env.from_string(task['ansible.builtin.set_fact']['alpine_mirror']).render(alpine_frozen_repositories={'content':content})
            self.assertEqual(value,mirror)

    def test_container_recipes_require_an_explicit_mirror_and_keep_proxy_out_of_apk(self):
        root = Path(__file__).resolve().parents[2]
        found = 0
        for path in (root/'apps').glob('*/images/*/Containerfile'):
            text = path.read_text()
            if 'apk add ' not in text: continue
            found += 1
            self.assertIn('ARG ALPINE_MIRROR', text, str(path))
            self.assertIn('test -n "$ALPINE_MIRROR"', text, str(path))
            self.assertIn('-u all_proxy apk add', text, str(path))
        self.assertGreaterEqual(found, 6)
