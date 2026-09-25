"""Fault tests for the guest boundary; these do not claim a native Xen proof."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
loader = importlib.machinery.SourceFileLoader('router_copy_guest', str(REPO / 'ansible/roles/router-state-copy/files/router-state-copy-guest'))
spec = importlib.util.spec_from_loader(loader.name, loader)
guest = importlib.util.module_from_spec(spec)
loader.exec_module(guest)


class CopyGuestTests(unittest.TestCase):
    def test_refuses_non_root_before_opening_disks(self):
        with patch.object(guest.os, 'geteuid', return_value=1000), self.assertRaisesRegex(RuntimeError, 'networkless Xen'):
            guest.guard()

    def test_duplicate_request_fields_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            json.loads('{"role":"router","role":"dmz"}', object_pairs_hook=guest.unique)

    def test_journal_recovery_never_writes_original_and_failure_has_no_complete_receipt(self):
        for recover, fail in ((False, False), (True, False), (True, True)):
            with self.subTest(recover=recover, fail=fail), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                result = root / 'result'
                result.write_bytes(b'old receipt')
                calls = []
                def run(argv, deadline, allowed=(0,)):
                    calls.append([str(a) for a in argv])
                    if str(argv[0]) == '/sbin/e2fsck' and str(argv[-1]) == str(guest.SOURCE):
                        return 4 if recover else 0
                    if str(argv[0]) == '/bin/mount':
                        mount = Path(argv[-1])
                        (mount / 'etc').mkdir()
                        (mount / 'etc/hostname').write_text('boxa-router\n')
                    return 0
                def copy(*args, **kwargs):
                    if fail:
                        raise RuntimeError('synthetic interruption')
                    return {'kind': 'klokast.router-state-copy.v1', 'complete': True, 'receipt_sha256': 'old'}
                request = {'box': 'boxa', 'operation': 'a' * 24, 'source_id': 'old-uuid',
                           'destination_id': 'new-uuid', 'seconds': 120, 'request_sha256': 'b' * 64,
                           'source_accounts': {'dnsmasq_uid': 103, 'dnsmasq_gid': 104, 'tailscale_gid': 103},
                           'destination_accounts': {'dnsmasq_uid': 105, 'dnsmasq_gid': 106, 'tailscale_gid': 107}}
                with patch.object(guest, 'Path', side_effect=lambda name: root / str(name).lstrip('/')), \
                     patch.object(guest, 'RESULT', result), patch.object(guest, 'run', side_effect=run), \
                     patch.object(guest, 'copy_state', side_effect=copy):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'synthetic interruption'):
                            guest.execute(request)
                    else:
                        guest.execute(request)
                self.assertEqual(calls[0], ['/sbin/e2fsck', '-fn', '/dev/xvda3'])
                self.assertNotIn(['/sbin/e2fsck', '-p', '/dev/xvda3'], calls)
                source_mount = next(call for call in calls if call[0] == '/bin/mount')
                self.assertIn('ro,noload,nodev,nosuid,noexec', source_mount)
                if recover:
                    self.assertIn(['/sbin/e2fsck', '-p', '/dev/xvdc'], calls)
                    self.assertEqual(source_mount[-2], '/dev/xvdc')
                else:
                    self.assertEqual(source_mount[-2], '/dev/xvda3')
                self.assertEqual(len([c for c in calls if c[0] == '/bin/umount']), 2)
                if fail:
                    self.assertEqual(result.read_bytes(), b'\0' * 65536)
                else:
                    receipt = json.loads(result.read_bytes().rstrip(b'\0'))
                    self.assertTrue(receipt['complete'])
                    self.assertEqual(receipt['recovered_clone'], recover)


if __name__ == '__main__':
    unittest.main()
