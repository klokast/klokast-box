"""Native rsync staging and final-sync tests on temporary synthetic files."""
import copy
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_vm_retained_data import d


@unittest.skipUnless(shutil.which('rsync'), 'native rsync is required')
class RetainedStage(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.source.mkdir(); self.target.mkdir()
        self.library = self.source / 'srv/app/library'
        self.library.mkdir(parents=True)
        (self.library / 'file').write_text('before')
        (self.library / 'remove').write_text('remove during final sync')
        self.runtime = {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[200000, 65536]]}
        self.request = {'kind': 'klokast.vm-retained-stage.v1', 'operation_id': 'a' * 24,
                        'source_uuid': '11111111-1111-1111-1111-111111111111',
                        'destination_uuid': '22222222-2222-2222-2222-222222222222',
                        'runtime': self.runtime, 'entries': [{'key': 'library', 'source': 'srv/app/library'}],
                        'source_layout': 'legacy-root'}
        self.deadline = time.monotonic() + 30
        stack = self.enterContext(ExitStack())
        for name, value in [('SOURCE', self.source), ('TARGET', self.target)]:
            stack.enter_context(patch.object(d, name, value))
        stack.enter_context(patch.object(d, 'environment'))
        stack.enter_context(patch.object(d, 'check_mounts'))
        stack.enter_context(patch.object(d, 'runtime_identity', return_value=self.runtime))

    def stage(self):
        self.receipt = d.stage(self.request, self.deadline)
        return self.receipt

    def finalize(self):
        return d.finalize(self.request, self.receipt['receipt_sha256'], self.deadline)

    def test_final_sync_copies_same_size_timestamp_changes_deletions_links_and_metadata(self):
        path = self.library / 'file'
        os.link(path, self.library / 'link')
        os.setxattr(path, 'user.test', b'before')
        self.stage()
        times = path.stat()
        path.write_text('after!')
        os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
        os.setxattr(path, 'user.test', b'after')
        (self.library / 'remove').unlink()
        (self.library / 'new').write_text('new file')
        with (self.library / 'sparse').open('wb') as stream:
            stream.seek(1024 * 1024); stream.write(b'end')
        before = d.tree(self.library, self.deadline)
        final = self.finalize()
        copied = self.target / 'library'
        self.assertEqual(d.tree(copied, self.deadline), before)
        self.assertEqual(d.tree(self.library, self.deadline), before)
        self.assertEqual((copied / 'file').stat().st_ino, (copied / 'link').stat().st_ino)
        self.assertLess((copied / 'sparse').stat().st_blocks * 512, (copied / 'sparse').stat().st_size)
        self.assertFalse(final['adoption_accepted'])
        self.assertTrue(final['copy_verified'])
        with self.assertRaisesRegex(d.CopyError, 'unknown or partial'):
            self.finalize()

    def test_only_exact_request_and_receipt_can_reuse_staging(self):
        self.stage()
        original = copy.deepcopy(self.request)
        for change in (lambda r: r.update(operation_id='b' * 24),
                       lambda r: r['entries'][0].update(source='srv/app/other'),
                       lambda r: r.update(destination_uuid='33333333-3333-3333-3333-333333333333')):
            self.request = copy.deepcopy(original)
            change(self.request)
            with self.assertRaisesRegex(d.CopyError, 'exact operation'):
                self.finalize()
        self.request = original
        with self.assertRaisesRegex(d.CopyError, 'exact operation'):
            d.finalize(self.request, '0' * 64, self.deadline)
        self.assertFalse((self.target / '.klokast-final-pending').exists())

    def test_unknown_files_and_changed_staging_are_never_overwritten(self):
        self.stage()
        unknown = self.target / 'unmapped'
        unknown.write_text('keep')
        with self.assertRaisesRegex(d.CopyError, 'unknown or partial'):
            self.finalize()
        self.assertEqual(unknown.read_text(), 'keep')
        unknown.unlink()
        (self.target / 'library/file').write_text('changed')
        with self.assertRaisesRegex(d.CopyError, 'destination changed'):
            self.finalize()
        self.assertEqual((self.target / 'library/file').read_text(), 'changed')

    def test_interrupted_final_sync_cannot_be_retried(self):
        self.stage()
        with patch.object(d, 'run', side_effect=d.CopyError('interrupted')):
            with self.assertRaisesRegex(d.CopyError, 'interrupted'):
                self.finalize()
        self.assertTrue((self.target / '.klokast-final-pending').exists())
        self.assertFalse((self.target / '.klokast-final-result.json').exists())
        with self.assertRaisesRegex(d.CopyError, 'unknown or partial'):
            self.finalize()

    def test_interrupted_stage_cannot_be_reused(self):
        with patch.object(d, 'run', side_effect=d.CopyError('interrupted')):
            with self.assertRaisesRegex(d.CopyError, 'interrupted'):
                self.stage()
        with self.assertRaisesRegex(d.CopyError, 'unknown or partial'):
            self.stage()

    def test_retained_layout_supports_next_generation_without_old_etc(self):
        self.stage(); self.finalize()
        next_target = self.root / 'next'
        next_target.mkdir()
        request = {**self.request, 'source_layout': 'retained-data', 'operation_id': 'b' * 24,
                   'source_uuid': self.request['destination_uuid'],
                   'destination_uuid': '33333333-3333-3333-3333-333333333333',
                   'entries': [{'key': 'library', 'source': 'library'}]}
        before = d.tree(self.target / 'library', self.deadline)
        with patch.object(d, 'SOURCE', self.target), patch.object(d, 'TARGET', next_target), \
                patch.object(d, 'runtime_identity', side_effect=AssertionError('retained disk has no /etc')):
            staged = d.stage(request, self.deadline)
            final = d.finalize(request, staged['receipt_sha256'], self.deadline)
        self.assertEqual(final['entries']['library'], before)
        self.assertEqual(d.tree(next_target / 'library', self.deadline), before)

    def test_identity_and_record_tampering_block_before_writes(self):
        self.stage()
        path = self.target / '.klokast-stage-result.json'
        original = path.read_bytes()
        path.write_bytes(original.replace(b'false', b'true'))
        with self.assertRaisesRegex(d.CopyError, 'exact operation'):
            self.finalize()
        path.write_bytes(original)
        path.chmod(0o644)
        with self.assertRaisesRegex(d.CopyError, 'unsafe ownership'):
            self.finalize()
        self.assertFalse((self.target / '.klokast-final-pending').exists())

    def test_invalid_layout_contract_or_retained_alias_is_refused(self):
        for change in (lambda r: r.update(source_layout='other'), lambda r: r.update(extra=True),
                       lambda r: r.update(source_layout='retained-data'),
                       lambda r: r['entries'][0].update(key='../other')):
            request = copy.deepcopy(self.request); change(request)
            with self.assertRaises(d.CopyError):
                d.staged_request(request)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_final_capacity_inodes_and_deadline_refuse_before_claim(self):
        self.stage()
        for free_bytes, inodes in ((1, 10000), (10**12, 1)):
            with patch.object(d.os, 'statvfs', return_value=SimpleNamespace(
                    f_bavail=free_bytes, f_frsize=1, f_favail=inodes)):
                with self.assertRaisesRegex(d.CopyError, 'insufficient bytes or inodes'):
                    self.finalize()
            self.assertFalse((self.target / '.klokast-final-pending').exists())
        self.deadline = time.monotonic() - 1
        with self.assertRaisesRegex(d.CopyError, 'deadline'):
            self.finalize()
        self.assertFalse((self.target / '.klokast-final-pending').exists())

    def test_stage_record_symlinks_and_duplicate_fields_are_refused(self):
        self.stage()
        record = self.target / '.klokast-stage-result.json'
        external = self.root / 'outside'
        record.rename(external)
        record.symlink_to(external)
        with self.assertRaisesRegex(d.CopyError, 'unsafe ownership'):
            self.finalize()
        record.unlink()
        d.create_record(record, {})
        record.write_text('{"kind": "first", "kind": "second"}')
        with self.assertRaisesRegex(d.CopyError, 'duplicate fields'):
            self.finalize()

    def test_source_change_during_final_sync_poisoned_destination(self):
        self.stage()
        native = d.run
        def change(argv, deadline):
            native(argv, deadline)
            if argv[0] == 'rsync':
                (self.library / 'file').write_text('unexpected writer')
        with patch.object(d, 'run', side_effect=change):
            with self.assertRaisesRegex(d.CopyError, 'integrity verification'):
                self.finalize()
        self.assertTrue((self.target / '.klokast-final-pending').exists())
        self.assertFalse((self.target / '.klokast-final-result.json').exists())


if __name__ == '__main__':
    unittest.main()
