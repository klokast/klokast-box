"""Versioned legacy partition selection and block-layer refusal checks."""
import copy
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_vm_retained_data import d
import test_vm_retained_stage as stages


class PartitionContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.sys = root / 'class'; self.sys.mkdir()
        self.disk = root / 'devices/xvdc'; self.disk.mkdir(parents=True)
        self.part = self.disk / 'xvdc3'; self.part.mkdir()
        (self.sys / 'xvdc').symlink_to(self.disk)
        (self.sys / 'xvdc3').symlink_to(self.part)
        (self.disk / 'ro').write_text('1\n'); (self.part / 'ro').write_text('1\n')
        (self.part / 'partition').write_text('3\n')
        self.enterContext(patch.object(d, 'BLOCK_SYS', self.sys))
        self.request = {'kind': 'klokast.vm-retained-stage.v3', 'operation_id': 'a' * 24,
                        'source_uuid': '11111111-1111-1111-1111-111111111111',
                        'destination_uuid': '22222222-2222-2222-2222-222222222222',
                        'runtime': {'uid': 1000, 'gid': 1000, 'subuid': [[100000, 65536]], 'subgid': [[100000, 65536]]},
                        'entries': [{'key': 'platform-tailscale-state', 'source': 'var/lib/tailscale/tailscaled.state',
                                     'type': 'identity-file'}], 'source_layout': 'legacy-root', 'source_partition': 3}

    def test_exact_partition_and_raw_disk_selection(self):
        d.staged_request(self.request)
        self.assertEqual(d.source_device(self.request), '/dev/xvdc3')
        self.request['source_partition'] = 0
        d.staged_request(self.request)
        self.assertEqual(d.source_device(self.request), '/dev/xvdc')

    def test_unsupported_missing_or_unrecorded_layout_is_refused(self):
        for value in (True, 1, 2, 4, '3', None):
            request = {**self.request, 'source_partition': value}
            with self.subTest(value=value), self.assertRaises(d.CopyError):
                d.staged_request(request)
        request = copy.deepcopy(self.request); del request['source_partition']
        with self.assertRaises(d.CopyError): d.staged_request(request)
        for version in ('v1', 'v2'):
            request = {**self.request, 'kind': 'klokast.vm-retained-stage.' + version}
            with self.assertRaises(d.CopyError): d.staged_request(request)
        self.request['source_layout'] = 'retained-data'
        with self.assertRaises(d.CopyError): d.staged_request(self.request)

    def test_writable_parent_or_partition_refused(self):
        for root in (self.disk, self.part):
            (root / 'ro').write_text('0\n')
            with self.assertRaisesRegex(d.CopyError, 'block layer'): d.source_device(self.request)
            (root / 'ro').write_text('1\n')

    def test_wrong_partition_number_and_parent_refused(self):
        (self.part / 'partition').write_text('2\n')
        with self.assertRaisesRegex(d.CopyError, 'does not belong'): d.source_device(self.request)
        (self.part / 'partition').write_text('3\n')
        foreign = self.disk.parent / 'xvdd3'
        self.part.rename(foreign)
        (self.sys / 'xvdc3').unlink(); (self.sys / 'xvdc3').symlink_to(foreign)
        with self.assertRaisesRegex(d.CopyError, 'does not belong'): d.source_device(self.request)

    def test_other_mounted_partition_refused_before_destination_access(self):
        for source in ('/dev/xvdc', '/dev/xvdc1', '/dev/xvdc3'):
            with patch.object(d, 'mount_records', return_value=[{'source': source, 'path': '/other'}]):
                with self.assertRaisesRegex(d.CopyError, 'other mounted'): d.check_mounts(self.request)


@unittest.skipUnless(shutil.which('rsync'), 'native rsync is required')
class PartitionReceipt(unittest.TestCase):
    stage = stages.RetainedStage.stage
    finalize = stages.RetainedStage.finalize

    def setUp(self):
        stages.RetainedStage.setUp(self)
        self.request.update(kind='klokast.vm-retained-stage.v3', source_partition=3)
        self.request['entries'][0]['type'] = 'directory'

    def test_partition_change_cannot_reuse_staged_data(self):
        receipt = self.stage()
        self.assertEqual(receipt['kind'], 'klokast.vm-retained-stage-result.v3')
        self.request['source_partition'] = 0
        with self.assertRaisesRegex(d.CopyError, 'exact operation'): self.finalize()


if __name__ == '__main__':
    unittest.main()
