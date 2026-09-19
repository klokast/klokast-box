"""Old Platform verification stages must be exact, idle, and source matched."""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml


SCRIPT = (Path(__file__).resolve().parents[1] /
          'roles/vm-update-verification-cache-cleanup/files/cleanup-vm-verification-cache')
loader = importlib.machinery.SourceFileLoader('vm_verification_cache_cleanup', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
cleanup = importlib.util.module_from_spec(spec)
loader.exec_module(cleanup)


class VerificationCacheCleanup(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'cache'
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o2755)
        self.stage = self.root / 'verify-one'
        self.stage.mkdir(mode=0o700)
        self.stage.chmod(0o2700)
        self.desired = self.stage / 'desired.json'
        self.desired.write_text(json.dumps({'schema_version': 1, 'compiler': 'platform-resources',
                                            'compiler_version': 21, 'app_resource_effective_files': []}))
        self.desired.chmod(0o600)
        self.helper = self.stage / 'klokast-app-resources-reconcile'
        self.helper.write_bytes(b'A' * 8253)
        self.helper.chmod(0o600)
        patches = (
            patch.object(cleanup, 'ROOT', str(self.root)),
            patch.object(cleanup, 'OWNER_UID', os.getuid()),
            patch.object(cleanup, 'OWNER_GID', os.getgid()),
            patch.object(cleanup, 'EXPECTED', {'test-dmz': ('verify-one',)}),
            patch.object(cleanup, 'HELPER_SHA256', hashlib.sha256(self.helper.read_bytes()).hexdigest()),
        )
        for fixture in patches:
            fixture.start()
            self.addCleanup(fixture.stop)

    def test_exact_stage_is_accounted_and_unknown_content_blocks(self):
        self.assertEqual(len(cleanup.inspect('test-dmz')), 1)
        extra = self.stage / 'unclassified-data'
        extra.write_text('PRIVATE')
        with self.assertRaisesRegex(ValueError, 'unknown entry'):
            cleanup.inspect('test-dmz')
        extra.unlink()
        self.helper.write_bytes(b'B' * 8253)
        with self.assertRaisesRegex(ValueError, 'differs from the reviewed source'):
            cleanup.inspect('test-dmz')

    def test_apply_requires_unchanged_preview_and_removes_only_fixed_stage(self):
        user = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        with (patch.object(cleanup.pwd, 'getpwnam', return_value=user),
              patch.object(cleanup.os, 'geteuid', return_value=0),
              patch.object(cleanup.os, 'getegid', return_value=0),
              patch.object(cleanup.socket, 'gethostname', return_value='test-dmz'),
              patch.object(cleanup, 'require_unused'),
              patch.object(cleanup, 'check_no_workload')):
            preview = cleanup.run('test-dmz', 'a' * 24)
            self.assertFalse(preview['applied'])
            with self.assertRaisesRegex(ValueError, 'changed since the preview'):
                cleanup.run('test-dmz', 'a' * 24, apply=True, expected_sha='0' * 64)
            self.assertTrue(self.stage.exists())
            applied = cleanup.run('test-dmz', 'a' * 24, apply=True,
                                  expected_sha=preview['plan_sha256'])
            self.assertTrue(applied['applied'])
            self.assertFalse(self.stage.exists())
            self.assertTrue(self.root.exists())

    def test_directory_or_file_in_use_blocks_cleanup(self):
        with patch.object(cleanup.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='123', stderr='')) as command:
            with self.assertRaisesRegex(ValueError, 'in use'):
                cleanup.require_unused('test-dmz')
            self.assertEqual(command.call_args.args[0][-1], str(self.stage))

    def test_new_live_rootless_workload_blocks_cleanup(self):
        with patch.object(cleanup.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='[{"Id":"new-container"}]')):
            with self.assertRaisesRegex(ValueError, 'workload blocks'):
                cleanup.check_no_workload()

    def test_playbook_rechecks_intent_and_binds_one_preview(self):
        path = SCRIPT.parents[3] / 'playbooks/74-platform-update-verification-cache-cleanup.yml'
        play = yaml.safe_load(path.read_text())[0]
        self.assertEqual(play['hosts'], 'k001-dmz:k002-dmz:k002-iot')
        source = json.dumps(play)
        for required in ('intent.eligible', 'intent.workloads', 'intent.datasets',
                         'verification_cache_preview.content', 'plan_sha256',
                         'ansible_play_hosts_all == [verification_cache_box'):
            self.assertIn(required, source)
        self.assertNotIn('--force', source)


if __name__ == '__main__':
    unittest.main()
