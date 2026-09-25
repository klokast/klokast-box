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
        for relative in r.ALLOWLIST:
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
        self.assertNotIn('var/lib/dhcpcd/eth0.lease6', record['files'])
        self.assertNotIn('synthetic-state', str(record))

    def test_required_identity_missing_and_empty(self):
        for path in ('var/lib/tailscale/tailscaled.state', 'var/lib/dhcpcd/duid', 'var/lib/dhcpcd/secret'):
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

    def test_native_duid_mode_and_tailscale_service_group(self):
        (self.source / 'var/lib/dhcpcd/duid').chmod(0o640)
        (self.source / 'var/lib/dhcpcd/secret').chmod(0o400)
        previous = r.os.fstat.side_effect
        def service_group(fd):
            value = previous(fd)
            if os.readlink('/proc/self/fd/' + str(fd)) == str(self.source / 'var/lib/tailscale/tailscaled.state'):
                value.st_gid = 103
            return value
        r.os.fstat.side_effect = service_group
        with self.assertRaisesRegex(r.StateError, 'ownership'):
            self.copy()
        self.assertTrue(self.copy(tailscale_gid=103)['complete'])
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
        self.put('var/lib/dhcpcd/eth0.lease6', b'unknown-state', root=self.target)
        with self.assertRaisesRegex(r.StateError, 'conflicting'):
            self.copy()

    def test_effective_ssh_source_and_candidate_key_conflict(self):
        system = 'etc/ssh/ssh_host_ed25519_key'
        fallback = 'var/lib/tailscale/ssh/ssh_host_ed25519_key'
        (self.source / system).unlink()
        self.put(fallback, b'fallback-key')
        self.put(system, b'candidate-key', root=self.target)
        with self.assertRaisesRegex(r.StateError, 'conflicting'):
            self.copy()
        (self.target / system).unlink()
        result = self.copy()
        self.assertIn(fallback, result['files'])
        self.assertNotIn(system, result['files'])

    def test_interrupted_copy_restarts_complete_set_and_reverse_uses_latest(self):
        class Interrupted(Exception): pass
        def interruption(stage):
            if stage == 'installed:var/lib/tailscale/tailscaled.state':
                raise Interrupted()
        with self.assertRaises(Interrupted):
            self.copy(checkpoint=interruption)
        self.assertTrue(self.copy()['complete'])
        path = 'var/lib/tailscale/tailscaled.state'
        self.put(path, b'new-key-state', root=self.target)
        reverse = r.copy_state(self.target, self.source, source_id='target-uuid', destination_id='source-uuid')
        self.assertTrue(reverse['complete'])
        self.assertEqual((self.source / path).read_bytes(), b'new-key-state')
        self.put(path, b'', root=self.target)
        with self.assertRaises(r.StateError):
            r.copy_state(self.target, self.source, source_id='target-uuid', destination_id='source-uuid')
        self.assertEqual((self.source / path).read_bytes(), b'new-key-state')

    def test_generic_template_refuses_identity_and_links(self):
        self.assertTrue(r.generic_absence(self.target))
        key = self.target / 'etc/ssh/ssh_host_ecdsa_key'
        key.write_bytes(b'synthetic')
        with self.assertRaises(r.StateError):
            r.generic_absence(self.target)
        key.unlink()
        key.symlink_to('/does-not-exist')
        with self.assertRaises(r.StateError):
            r.generic_absence(self.target)

    def test_generic_template_refuses_network_personalization(self):
        path = self.target / 'etc/network/interfaces'
        path.parent.mkdir()
        path.write_text('auto lo\niface lo inet loopback\n')
        self.assertTrue(r.generic_absence(self.target))
        path.write_text('auto eth3\niface eth3 inet static\n address 192.0.2.1/24\n')
        with self.assertRaisesRegex(r.StateError, 'network personalization'):
            r.generic_absence(self.target)

    def test_generic_template_refuses_network_directory_escape(self):
        outside = Path(self.temporary.name) / 'outside'
        outside.mkdir()
        (outside / 'interfaces').write_text('auto lo\niface lo inet loopback\n')
        (self.target / 'etc/network').symlink_to(outside)
        with self.assertRaisesRegex(r.StateError, 'symlink'):
            r.generic_absence(self.target)


if __name__ == '__main__':
    unittest.main()
