"""Upstream archive selection and byte-contract checks without network or guests."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from platform_updates import UpdateError
import router_tailscale as source


def archive(path, *, extra=False, symlink=False):
    prefix = 'tailscale_1.102.4_amd64'
    with tarfile.open(path, 'w:gz') as output:
        for name in (prefix, prefix + '/systemd'):
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            output.addfile(member)
        for name in ('tailscale', 'tailscaled'):
            member = tarfile.TarInfo(prefix + '/' + name)
            if symlink and name == 'tailscaled':
                member.type = tarfile.SYMTYPE
                member.linkname = '/etc/shadow'
                output.addfile(member)
            else:
                payload = b'\x7fELF' + name.encode()
                member.size = len(payload)
                output.addfile(member, io.BytesIO(payload))
        for name in ('tailscaled.service', 'tailscaled.defaults', 'tailscale-online.target',
                     'tailscale-wait-online.service'):
            payload = name.encode()
            member = tarfile.TarInfo(prefix + '/systemd/' + name)
            member.size = len(payload)
            output.addfile(member, io.BytesIO(payload))
        if extra:
            member = tarfile.TarInfo(prefix + '/unexpected')
            member.size = 1
            output.addfile(member, io.BytesIO(b'x'))


class TailscaleSourceTests(unittest.TestCase):
    def test_latest_stable_metadata_requires_matching_static_archive(self):
        valid = {'Version': '1.102.4', 'TarballsVersion': '1.102.4',
                 'Tarballs': {'amd64': 'tailscale_1.102.4_amd64.tgz'}}
        version, name, checksum = source.select(json.dumps(valid).encode())
        self.assertEqual((version, name), ('1.102.4', 'tailscale_1.102.4_amd64.tgz'))
        self.assertEqual(len(checksum), 64)
        for mutation in (
                lambda v: v.update(Version='1.102.5'),
                lambda v: v.update(Version='1.103.0-rc1', TarballsVersion='1.103.0-rc1'),
                lambda v: v['Tarballs'].update(amd64='tailscale_1.102.4_arm64.tgz')):
            changed = json.loads(json.dumps(valid))
            mutation(changed)
            with self.assertRaises(UpdateError):
                source.select(json.dumps(changed).encode())
        with self.assertRaises(UpdateError):
            source.select(b'{"Version":"1.102.4","Version":"1.102.5"}')

    def test_signed_archive_shape_and_frozen_binary_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            components = root / 'components'
            components.mkdir()
            path = components / 'tailscale_1.102.4_amd64.tgz'
            archive(path)
            service = components / 'tailscale-openrc'
            service.write_bytes(source.OPENRC.read_bytes())
            binaries = source.inspect_archive(path, '1.102.4')
            self.assertEqual(binaries['tailscale_sha256'], hashlib.sha256(b'\x7fELFtailscale').hexdigest())
            record = {'kind': 'klokast.router-tailscale-input.v1', 'version': '1.102.4',
                      'file': 'components/' + path.name, 'bytes': path.stat().st_size,
                      'sha256': source.sha256(path), **binaries,
                      'openrc_file': 'components/tailscale-openrc',
                      'openrc_sha256': source.sha256(service),
                      'metadata_sha256': 'a' * 64, 'verifier_source_tree': 'b' * 40,
                      'verifier_sha256': 'c' * 64, 'signature_verified': True}
            self.assertEqual(source.verify(root, record), record)
            record['tailscaled_sha256'] = 'd' * 64
            with self.assertRaises(UpdateError):
                source.verify(root, record)
            record['tailscaled_sha256'] = binaries['tailscaled_sha256']
            service.write_text('changed service\n')
            with self.assertRaises(UpdateError):
                source.verify(root, record)
            service.write_bytes(source.OPENRC.read_bytes())
            for mutation in ({'extra': True}, {'symlink': True}):
                archive(path, **mutation)
                with self.assertRaises(UpdateError):
                    source.inspect_archive(path, '1.102.4')


if __name__ == '__main__':
    unittest.main()
