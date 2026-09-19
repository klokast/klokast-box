"""One-time cache retirement must never select application data or force removal."""
import hashlib
import importlib.machinery
import importlib.util
import json
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'roles/vm-update-image-cache-cleanup/files/cleanup-vm-image-cache'
loader = importlib.machinery.SourceFileLoader('vm_image_cache_cleanup', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
cleanup = importlib.util.module_from_spec(spec)
loader.exec_module(cleanup)


class ImageCacheCleanup(unittest.TestCase):
    def test_fixed_source_names_and_live_workload_refusal(self):
        self.assertEqual(cleanup.image_kind('quay.io/libpod/alpine:latest'), 'podman-test')
        self.assertEqual(cleanup.image_kind('docker.1ms.run/library/redis@sha256:' + 'a' * 64), 'redis')
        with self.assertRaises(ValueError):
            cleanup.image_kind('example.invalid/private-data:latest')
        info = {'host': {'security': {'rootless': True}},
                'store': {'graphRoot': cleanup.ROOT, 'runRoot': '/tmp/storage-run-1000/containers'}}
        with patch.object(cleanup, 'podman_json', side_effect=[info, [{'Id': 'live'}]]):
            with self.assertRaisesRegex(ValueError, 'containers inventory is not empty'):
                cleanup.inspect('k002-iot')

    def test_apply_requires_unchanged_preview_and_uses_only_exact_image_id(self):
        image = {'id': 'a' * 64, 'name': 'quay.io/libpod/alpine:latest', 'kind': 'podman-test'}
        expected = hashlib.sha256(cleanup.canonical([image]).encode()).hexdigest()
        user = types.SimpleNamespace(pw_uid=1000, pw_gid=1000)
        with (patch.object(cleanup, 'inspect', return_value=[image]),
              patch.object(cleanup.pwd, 'getpwnam', return_value=user),
              patch.object(cleanup.os, 'geteuid', return_value=1000),
              patch.object(cleanup.os, 'getegid', return_value=1000),
              patch.object(cleanup.socket, 'gethostname', return_value='k002-iot'),
              patch.object(cleanup, 'command', return_value='') as command,
              patch.object(cleanup, 'podman_json', side_effect=[[], [], [], []])):
            with self.assertRaisesRegex(ValueError, 'changed since the preview'):
                cleanup.run('k002-iot', '1' * 24, apply=True, expected_sha='0' * 64)
            self.assertFalse(command.called)
            receipt = cleanup.run('k002-iot', '1' * 24, apply=True, expected_sha=expected)
            self.assertTrue(receipt['applied'])
            self.assertEqual(receipt['registered_images_after'], 0)
            self.assertEqual(command.call_args_list[0].args,
                             ('/usr/bin/podman', 'image', 'rm', 'a' * 64))
            self.assertNotIn('--force', str(command.call_args_list))

    def test_playbook_requires_one_target_checked_intent_and_preview(self):
        play = yaml.safe_load((ROOT / 'playbooks/74-platform-update-image-cache-cleanup.yml').read_text())[0]
        self.assertEqual(play['hosts'], 'k001-dmz:k002-dmz:k002-iot')
        tasks = json.dumps(play)
        self.assertIn('ansible_play_hosts_all == [image_cache_box', tasks)
        self.assertIn('intent.eligible', tasks)
        self.assertIn('image_cache_preview.content', tasks)
        self.assertNotIn('--force', tasks)


if __name__ == '__main__':
    unittest.main()
