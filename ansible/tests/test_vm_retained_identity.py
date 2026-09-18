"""Exact machine-state migration without copying an entire secret directory."""
import copy
import json
import os
import shutil
import unittest
from unittest.mock import patch

import test_vm_retained_stage as stage_tests

d = stage_tests.d


@unittest.skipUnless(shutil.which('rsync'), 'native rsync is required')
class RetainedIdentity(unittest.TestCase):
    stage = stage_tests.RetainedStage.stage
    finalize = stage_tests.RetainedStage.finalize

    def setUp(self):
        stage_tests.RetainedStage.setUp(self)
        self.request['kind'] = 'klokast.vm-retained-stage.v2'
        self.request['entries'][0]['type'] = 'directory'
        self.request['entries'].append({'key': 'platform-tailscale-state',
                                        'source': 'var/lib/tailscale/tailscaled.state', 'type': 'identity-file'})
        self.identity = self.source / 'var/lib/tailscale/tailscaled.state'
        self.identity.parent.mkdir(parents=True)
        self.identity.write_bytes(b'SYNTHETIC-SECRET-before')
        self.identity.chmod(0o600)
        (self.identity.parent / 'unrelated').write_text('do not copy')

    def test_exact_file_final_sync_and_receipts_keep_identity_private(self):
        os.setxattr(self.identity, 'user.synthetic', b'metadata')
        initial = self.stage()
        before = self.identity.stat()
        self.identity.write_bytes(b'SYNTHETIC-SECRET-after!')
        os.utime(self.identity, ns=(before.st_atime_ns, before.st_mtime_ns))
        final = self.finalize()
        destination = self.target / 'platform-tailscale-state'
        self.assertEqual(destination.read_bytes(), self.identity.read_bytes())
        self.assertEqual(d.tree(destination, self.deadline), d.tree(self.identity, self.deadline))
        self.assertEqual(destination.stat().st_mode & 0o7777, 0o600)
        self.assertFalse((self.target / 'unrelated').exists())
        self.assertFalse((self.target / 'var').exists())
        self.assertEqual(initial['kind'], 'klokast.vm-retained-stage-result.v2')
        self.assertEqual(final['kind'], 'klokast.vm-retained-final-result.v2')
        self.assertFalse(final['adoption_accepted'])
        self.assertNotIn('SYNTHETIC-SECRET', json.dumps([initial, final]))

    def test_next_retained_generation_needs_no_legacy_secret_directory(self):
        self.stage(); self.finalize()
        target = self.root / 'next'
        target.mkdir()
        request = copy.deepcopy(self.request)
        request.update(source_layout='retained-data', operation_id='b' * 24,
                       source_uuid=request['destination_uuid'],
                       destination_uuid='33333333-3333-3333-3333-333333333333')
        for entry in request['entries']:
            entry['source'] = entry['key']
        with patch.object(d, 'SOURCE', self.target), patch.object(d, 'TARGET', target), \
                patch.object(d, 'runtime_identity', side_effect=AssertionError('no old /etc')):
            receipt = d.stage(request, self.deadline)
            final = d.finalize(request, receipt['receipt_sha256'], self.deadline)
        self.assertTrue(final['copy_verified'])
        self.assertEqual((target / 'platform-tailscale-state').read_bytes(), self.identity.read_bytes())

    def test_only_supported_exact_identity_mapping_is_allowed(self):
        for change in ({'source': 'etc/shadow'}, {'source': 'var/lib/tailscale'},
                       {'source': 'var/lib/tailscale/files/data'}, {'key': 'other'},
                       {'type': 'directory'}, {'type': 'file'}, {'extra': True}):
            request = copy.deepcopy(self.request)
            request['entries'][1].update(change)
            with self.subTest(change=change), self.assertRaises(d.CopyError):
                d.stage(request, self.deadline)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_v1_does_not_silently_accept_typed_file_mappings(self):
        request = copy.deepcopy(self.request)
        request['kind'] = 'klokast.vm-retained-stage.v1'
        with self.assertRaises(d.CopyError):
            d.stage(request, self.deadline)
        request['entries'] = [{'key': 'identity', 'source': 'var/lib/tailscale/tailscaled.state'}]
        with self.assertRaisesRegex(d.CopyError, 'existing directory'):
            d.stage(request, self.deadline)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_unsafe_permissions_empty_large_and_hardlinked_identity_refuse_before_writes(self):
        original = self.identity.read_bytes()
        self.identity.chmod(0o640)
        with self.assertRaisesRegex(d.CopyError, 'machine identity file'):
            self.stage()
        self.identity.chmod(0o600)
        self.identity.write_bytes(b'')
        with self.assertRaisesRegex(d.CopyError, 'machine identity file'):
            self.stage()
        with self.identity.open('wb') as stream:
            stream.truncate(d.MAX_IDENTITY_BYTES + 1)
        with self.assertRaisesRegex(d.CopyError, 'machine identity file'):
            self.stage()
        self.identity.write_bytes(original)
        os.link(self.identity, self.identity.parent / 'alias')
        with self.assertRaisesRegex(d.CopyError, 'machine identity file'):
            self.stage()
        self.assertEqual(list(self.target.iterdir()), [])

    def test_identity_owner_directory_fifo_and_symlink_are_refused(self):
        with patch.object(d.os, 'geteuid', return_value=os.getuid() + 1):
            with self.assertRaisesRegex(d.CopyError, 'machine identity file'):
                self.stage()
        self.identity.unlink(); self.identity.mkdir()
        with self.assertRaisesRegex(d.CopyError, 'machine identity file'):
            self.stage()
        self.identity.rmdir(); os.mkfifo(self.identity)
        with self.assertRaisesRegex(d.CopyError, 'machine identity file'):
            self.stage()
        self.identity.unlink(); self.identity.symlink_to('/etc/shadow')
        with self.assertRaisesRegex(d.CopyError, 'symlink'):
            self.stage()
        self.assertEqual(list(self.target.iterdir()), [])

    def test_identity_change_in_staging_cannot_be_overwritten(self):
        self.stage()
        destination = self.target / 'platform-tailscale-state'
        destination.write_bytes(b'tampered')
        with self.assertRaisesRegex(d.CopyError, 'destination changed'):
            self.finalize()
        self.assertEqual(destination.read_bytes(), b'tampered')
        self.assertFalse((self.target / '.klokast-final-pending').exists())

    def test_identity_final_sync_never_has_directory_deletion_flags(self):
        self.stage()
        native, calls = d.run, []
        def record(argv, deadline):
            calls.append(argv)
            return native(argv, deadline)
        with patch.object(d, 'run', side_effect=record):
            self.finalize()
        calls = [v for v in calls if v[0] == 'rsync' and v[-1].endswith('platform-tailscale-state')]
        self.assertEqual(len(calls), 1)
        self.assertIn('--checksum', calls[0])
        self.assertNotIn('--delete-delay', calls[0])
        self.assertFalse(calls[0][-2].endswith('/'))

    def test_cross_version_receipt_and_mapping_type_change_refuse_before_sync(self):
        self.stage()
        request = copy.deepcopy(self.request)
        request['entries'][0]['type'] = 'identity-file'
        with self.assertRaises(d.CopyError):
            d.finalize(request, self.receipt['receipt_sha256'], self.deadline)
        path = self.target / '.klokast-stage-result.json'
        receipt = json.loads(path.read_text())
        receipt['kind'] = 'klokast.vm-retained-stage-result.v1'
        receipt['receipt_sha256'] = d.digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
        path.write_bytes(d.canonical(receipt))
        with self.assertRaisesRegex(d.CopyError, 'exact operation'):
            d.finalize(self.request, receipt['receipt_sha256'], self.deadline)
        self.assertFalse((self.target / '.klokast-final-pending').exists())


if __name__ == '__main__':
    unittest.main()
