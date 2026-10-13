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


class MirrorTests(unittest.TestCase):
    def choice(self):
        mirrors = list(m.MIRRORS)
        return {'kind': m.KIND, 'policy': m.POLICY, 'box': 'boxa', 'country': 'CN',
                'selected_at': '2026-10-13T12:00:00Z', 'primary': mirrors[1],
                'fallback': mirrors[2], 'measurements': []}

    def test_location_is_a_hint_and_transfer_performance_selects(self):
        def probe(mirror, branch):
            return {'mirror': mirror, 'bytes_per_second': 10 if 'tuna' in mirror else 500}
        for country in ('', 'CN', 'FR'):
            with patch.object(m, 'probe', side_effect=probe) as probes:
                value = m.measure('boxa', country)
            self.assertNotIn('tuna', value['primary'])
            self.assertEqual(probes.call_count, 3)
            self.assertEqual(value['country'], country)

    def test_unavailable_mirrors_never_replace_a_valid_record(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'alpine.json'; m.publish(path, self.choice())
            original = path.read_bytes()
            with patch.object(m, 'require_storage'), patch('socket.gethostname', return_value='boxa-ops'), \
                    patch.object(m, 'probe', side_effect=RuntimeError('timeout')), \
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

    def test_missing_mount_and_unapproved_transport_are_rejected(self):
        with patch.object(m.os.path, 'ismount', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'not mounted'): m.require_storage()
        for mirror in ('http://mirrors.tuna.tsinghua.edu.cn/alpine', 'file:///tmp/alpine', 'https://unapproved.invalid/alpine'):
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

    def test_concurrent_refresh_does_not_fail_a_completed_fallback_download(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'alpine.json';m.publish(path,self.choice())
            with (path.parent/'selection.lock').open('a') as lock:
                m.fcntl.flock(lock,m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)
                m.promote_fallback(self.choice(),path)
            self.assertEqual(json.loads(path.read_text()),self.choice())

    def test_repository_choice_preserves_release_and_rejects_mixed_origins(self):
        value = m.repositories(self.choice()['primary'], 'v3.24')
        self.assertTrue(m.valid_repositories(value, 'v3.24'))
        self.assertFalse(m.valid_repositories(value, 'v3.23'))
        self.assertFalse(m.valid_repositories([value[0], inputs.ORIGIN+'/v3.24/community'], 'v3.24'))

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
                with patch.object(m, 'read', return_value=self.choice()), patch.object(inputs, 'freeze', side_effect=freeze), \
                        patch.object(m, 'promote_fallback') as promote:
                    if expected == 2:
                        self.assertEqual(inputs.freeze_local(output, {}, 'v3.24', 'a'*40), {'verified':True})
                        self.assertTrue((output/'inputs.json').is_file())
                        promote.assert_called_once()
                    else:
                        with self.assertRaises(inputs.UpdateError): inputs.freeze_local(output, {}, 'v3.24', 'a'*40)
                        self.assertFalse(output.exists()); promote.assert_not_called()
                self.assertEqual(len(calls), expected)

    def test_frozen_repository_origin_keeps_the_recorded_mirror(self):
        import re, yaml
        from jinja2.nativetypes import NativeEnvironment
        from ansible.plugins.filter.core import regex_replace
        tasks=yaml.safe_load((Path(__file__).resolve().parents[1]/'roles/alpine-mirror/tasks/main.yml').read_text())
        task=next(t for t in tasks if t['name'].startswith("Keep the image's recorded"))
        env=NativeEnvironment()
        import base64
        env.filters.update(b64decode=lambda v:base64.b64decode(v).decode(),regex_replace=regex_replace)
        env.tests['match']=lambda v,p:re.match(p,v) is not None
        for mirror in m.MIRRORS:
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
        self.assertGreaterEqual(found, 7)
