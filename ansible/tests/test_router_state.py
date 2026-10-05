"""Exercise the fixed copy set with synthetic secrets only."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ansible/lib'))
import router_state as r


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / 'source'
        self.target = Path(self.temporary.name) / 'target'
        self.source.mkdir()
        self.target.mkdir()
        for relative in {**r.PATHS, **r.IDENTITY}:
            for root in (self.source, self.target):
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
        self.uid = os.geteuid()
        # In fixtures the current unprivileged UID stands in for root. The
        # production function still checks native root ownership.
        original = r.os.fstat
        def ownership(fd):
            value = original(fd)
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=value.st_mode, st_nlink=value.st_nlink, st_size=value.st_size,
                                   st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns,
                                   st_atime_ns=value.st_atime_ns, st_uid=0, st_gid=0)
        self.owner = patch.object(r.os, 'fstat', side_effect=ownership)
        self.owner.start()
        self.addCleanup(self.owner.stop)
        self.chown = patch.object(r.os, 'fchown')
        self.chown.start()
        self.addCleanup(self.chown.stop)
        for relative in r.REQUIRED:
            self.put(relative, b'synthetic-state')
        for kind in r.KEY_TYPES:
            self.put('etc/ssh/ssh_host_' + kind + '_key', b'synthetic-key-' + kind.encode())

    def put(self, relative, content, root=None):
        path = (root or self.source) / relative
        path.write_bytes(content)
        path.chmod(0o600)
        os.utime(path, ns=(1600000000000000000, 1600000000000000000))
        return path

    def copy(self, **kwargs):
        return r.copy_state(self.source, self.target, source_id='source-uuid', destination_id='target-uuid', **kwargs)

    def test_preserves_bytes_modes_and_lease_age(self):
        lease = 'var/lib/dhcpcd/eth0.lease'
        self.put(lease, b'synthetic-lease')
        record = self.copy()
        self.assertTrue(record['complete'])
        for relative in record['files']:
            self.assertEqual((self.source / relative).read_bytes(), (self.target / relative).read_bytes())
            self.assertEqual((self.source / relative).stat().st_mtime_ns, (self.target / relative).stat().st_mtime_ns)
            self.assertEqual((self.target / relative).stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(record['files']), set(r.REQUIRED))
        self.assertEqual(record['kind'], 'klokast.router-state-copy.v2')
        self.assertEqual(record['wan_cache_absent'], list(r.WAN_CACHE))
        self.assertFalse((self.target / lease).exists())
        self.assertEqual((self.source / lease).read_bytes(), b'synthetic-lease')
        self.assertNotIn('synthetic-state', str(record))

    def test_required_identity_missing_and_empty(self):
        for path in ('var/lib/dhcpcd/duid', 'var/lib/dhcpcd/secret'):
            original = (self.source / path).read_bytes()
            (self.source / path).unlink()
            with self.assertRaises(r.StateError):
                self.copy()
            self.put(path, b'')
            with self.assertRaises(r.StateError):
                self.copy()
            self.put(path, original)
        self.put('var/lib/misc/dnsmasq.leases', b'')
        self.assertTrue(self.copy()['complete'])

    def test_native_duid_and_secret_modes(self):
        (self.source / 'var/lib/dhcpcd/duid').chmod(0o640)
        (self.source / 'var/lib/dhcpcd/secret').chmod(0o400)
        self.assertTrue(self.copy()['complete'])
        self.assertEqual((self.target / 'var/lib/dhcpcd/duid').stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.target / 'var/lib/dhcpcd/secret').stat().st_mode & 0o777, 0o400)

    def test_world_readable_secret_is_rejected_before_target_changes(self):
        (self.source / 'var/lib/dhcpcd/secret').chmod(0o644)
        with self.assertRaisesRegex(r.StateError, 'must be private'):
            self.copy()
        self.assertFalse((self.target / 'var/lib/tailscale/tailscaled.state').exists())

    def test_symlink_hardlink_fifo_and_oversized_source(self):
        relative = 'var/lib/dhcpcd/duid'
        path = self.source / relative
        for form in ('symlink', 'hardlink', 'fifo', 'oversized'):
            with self.subTest(form=form):
                path.unlink()
                external = self.source / 'external'
                external.write_bytes(b'synthetic')
                external.chmod(0o600)
                if form == 'symlink': path.symlink_to(external)
                elif form == 'hardlink': os.link(external, path)
                elif form == 'fifo': os.mkfifo(path, 0o600)
                else: self.put(relative, b'x' * 4097)
                with self.assertRaises((r.StateError, OSError)):
                    self.copy()
                path.unlink()
                external.unlink()
                self.put(relative, b'synthetic')

    def test_parent_symlink_escape_and_destination_collision(self):
        folder = self.target / 'var/lib/dhcpcd'
        folder.rmdir()
        folder.symlink_to(self.source / 'var/lib/dhcpcd')
        with self.assertRaises(OSError):
            self.copy()
        folder.unlink()
        folder.mkdir()
        cache = self.put('var/lib/dhcpcd/eth0.lease6', b'old-WAN-cache', root=self.target)
        cache.chmod(0o666)
        with self.assertRaisesRegex(r.StateError, 'unsafe'):
            self.copy()
        self.assertFalse((self.target / 'var/lib/dhcpcd/duid').exists())
        self.assertTrue(cache.exists())

    def test_forward_and_reverse_preserve_separate_generation_identity(self):
        for root, generation in ((self.source, b'A'), (self.target, b'B')):
            for path in r.IDENTITY:
                self.put(path, generation + path.encode(), root=root)
        before = {str(root): {path: (root/path).read_bytes() for path in r.IDENTITY}
                  for root in (self.source, self.target)}
        self.copy()
        self.put('var/lib/misc/dnsmasq.leases', b'latest-client-lease', root=self.target)
        r.copy_state(self.target, self.source, source_id='B', destination_id='A')
        for root in (self.source, self.target):
            self.assertEqual({path: (root/path).read_bytes() for path in r.IDENTITY}, before[str(root)])
        self.assertEqual((self.source/'var/lib/misc/dnsmasq.leases').read_bytes(), b'latest-client-lease')

    def test_wan_cache_removal_is_bounded_and_resumable(self):
        for path in r.WAN_CACHE:
            self.put(path, b'old-lease', root=self.target)
        unknown = self.put('var/lib/dhcpcd/other.lease', b'not-owned', root=self.target)
        class Interrupted(Exception): pass
        def interrupt(stage):
            if stage == 'removed:var/lib/dhcpcd/eth0.lease':
                raise Interrupted()
        with self.assertRaises(Interrupted):
            self.copy(checkpoint=interrupt)
        self.assertTrue(self.copy()['complete'])
        self.assertTrue(all(not (self.target/path).exists() for path in r.WAN_CACHE))
        self.assertEqual(unknown.read_bytes(), b'not-owned')

    def test_linked_wan_cache_cannot_delete_another_file(self):
        cache = self.target/'var/lib/dhcpcd/eth0.lease'
        secret = self.put('var/lib/dhcpcd/secret', b'keep', root=self.target)
        cache.symlink_to(secret)
        with self.assertRaises(OSError):
            self.copy()
        self.assertEqual(secret.read_bytes(), b'keep')
        self.assertFalse((self.target/'var/lib/dhcpcd/duid').exists())

    def test_interrupted_copy_restarts_complete_set_and_reverse_uses_latest(self):
        class Interrupted(Exception): pass
        def interruption(stage):
            if stage == 'installed:var/lib/dhcpcd/duid':
                raise Interrupted()
        with self.assertRaises(Interrupted):
            self.copy(checkpoint=interruption)
        self.assertTrue(self.copy()['complete'])
        path = 'var/lib/dhcpcd/duid'
        self.put(path, b'latest-DUID', root=self.target)
        reverse = r.copy_state(self.target, self.source, source_id='target-uuid', destination_id='source-uuid')
        self.assertTrue(reverse['complete'])
        self.assertEqual((self.source / path).read_bytes(), b'latest-DUID')
        self.put(path, b'', root=self.target)
        with self.assertRaises(r.StateError):
            r.copy_state(self.target, self.source, source_id='target-uuid', destination_id='source-uuid')
        self.assertEqual((self.source / path).read_bytes(), b'latest-DUID')






if __name__ == '__main__':
    unittest.main()
