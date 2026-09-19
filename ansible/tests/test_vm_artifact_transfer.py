"""Transfer must publish only the exact tested base template bytes."""
import hashlib
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import vm_artifact_transfer as transfer
from platform_updates import UpdateError


class ArtifactTransferTests(unittest.TestCase):
    def candidate(self):
        identity = {'sha256': hashlib.sha256(b'x').hexdigest(), 'bytes': 1}
        return {'kind': 'klokast.vm-template-candidate.v1',
                'operation_id': 'a' * 24, 'box': 'k001', 'success': True,
                'accepted': False, 'artifacts': dict.fromkeys(transfer.LIMITS, identity)}

    def test_rejects_wrong_source_identity_and_unsafe_artifacts(self):
        value = self.candidate()
        transfer.validate(value, 'a' * 24, 'k001')
        for change in ({'box': 'k002'}, {'accepted': True}, {'success': False},
                       {'operation_id': 'b' * 24}):
            changed = dict(value, **change)
            with self.subTest(change=change), self.assertRaises(UpdateError):
                transfer.validate(changed, 'a' * 24, 'k001')
        changed = self.candidate()
        changed['artifacts']['root'] = {'sha256': 'bad', 'bytes': 1}
        with self.assertRaises(UpdateError):
            transfer.validate(changed, 'a' * 24, 'k001')

    def test_exact_bytes_reach_only_the_selected_other_box(self):
        value = self.candidate()
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / 'candidate.json'
            manifest.write_text(json.dumps(value, sort_keys=True) + '\n')
            calls = []
            def remote(host, *command, **kwargs):
                calls.append((host, command))
                if command[:2] == ('doas', 'cat'):
                    return manifest.read_bytes()
                return b''
            def source_file(host, path, local, expected):
                self.assertEqual(host, 'k001')
                self.assertEqual(expected['sha256'], hashlib.sha256(b'x').hexdigest())
                with gzip.open(local, 'wb') as stream:
                    stream.write(b'x')
            def target_file(host, local, path, expected):
                self.assertEqual(host, 'k002')
                with gzip.open(local, 'rb') as stream:
                    payload = stream.read()
                self.assertEqual(hashlib.sha256(payload).hexdigest(),
                                 expected['sha256'])
            with patch.object(transfer, 'remote', side_effect=remote), \
                    patch.object(transfer, 'source_file', side_effect=source_file), \
                    patch.object(transfer, 'target_file', side_effect=target_file):
                receipt = transfer.transfer(value, 'a' * 24, 'k001', 'k002', manifest, temporary)
            self.assertEqual(receipt['accepted'], False)
            self.assertEqual(receipt['target_box'], 'k002')
            self.assertEqual(len([call for call in calls if call[1][:2] == ('doas', 'cat')]), 2)

    def test_changed_builder_manifest_never_creates_target_stage(self):
        value = self.candidate()
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / 'candidate.json'
            manifest.write_text(json.dumps(value, sort_keys=True) + '\n')
            with patch.object(transfer, 'remote', return_value=b'changed') as remote:
                with self.assertRaisesRegex(UpdateError, 'builder candidate manifest'):
                    transfer.transfer(value, 'a' * 24, 'k001', 'k002', manifest, temporary)
            self.assertEqual(remote.call_count, 1)

    def test_reuse_refuses_a_changed_published_artifact(self):
        value = self.candidate()
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / 'candidate.json'
            manifest.write_text(json.dumps(value, sort_keys=True) + '\n')
            def remote(host, *command, **kwargs):
                self.assertEqual(host, 'k002')
                if command[:2] == ('doas', 'cat'):
                    return manifest.read_bytes()
                if command[:2] == ('doas', 'sha256sum'):
                    return ('0' * 64 + '  ' + command[2] + '\n').encode()
                return b'1\n'
            with patch.object(transfer, 'remote', side_effect=remote):
                with self.assertRaisesRegex(UpdateError, 'artifact differs'):
                    transfer.verify_published(value, 'a' * 24, 'k001', 'k002', manifest)


if __name__ == '__main__':
    unittest.main()
