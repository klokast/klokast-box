"""Bounded transport preserves the exact frozen router capsule."""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import router_template_inputs
from platform_updates import UpdateError


def assembler():
    path = REPO / 'ansible/roles/router-alpine-rootfs/files/router-template-assemble-dom0'
    loader = importlib.machinery.SourceFileLoader('router_template_assemble_test', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    result = importlib.util.module_from_spec(spec)
    loader.exec_module(result)
    return result


class CapsuleTransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.operation = 'a' * 24
        self.controller = self.base / 'controller'
        self.controller.mkdir(mode=0o700)
        self.payloads = {'capsule': b'A' * (2 * 1024 * 1024) + b'B' * 37,
                         'kernel': b'K' * 29, 'initramfs': b'I' * 31}
        self.sources = {}
        self.expected = {}
        self.parts = []
        (self.controller / 'transfer').mkdir(mode=0o700)
        for artifact, data in self.payloads.items():
            source = self.controller / (artifact + '.bin')
            source.write_bytes(data)
            source.chmod(0o600)
            self.sources[artifact] = source
            self.expected[artifact] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
            self.parts.extend({'artifact': artifact, **part} for part in
                router_template_inputs.split_payload(source, self.controller / 'transfer' / artifact,
                                                    self.expected[artifact]))
        self.work = self.base / self.operation
        self.work.mkdir(mode=0o700)
        shutil.copytree(self.controller / 'transfer', self.work / 'parts')
        self.write('parts.json', self.parts)
        self.write('request.json', {'kind': 'klokast.router-template-request.v1',
            'box': 'boxa', 'role': 'router', 'operation_id': self.operation,
            'inputs_sha256': 'c' * 64, 'capsule': self.expected['capsule'],
            'bootstrap': {key: self.expected[key] for key in ('kernel', 'initramfs')}})
        self.module = assembler()

    def write(self, name, value):
        path = self.work / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def test_exact_chunks_assemble_once_without_changing_source(self):
        self.assertEqual([item['bytes'] for item in self.parts], [2 * 1024 * 1024, 37, 29, 31])
        self.assertEqual(self.module.assemble(
            self.work, 'boxa', self.operation, owner=os.geteuid())['assembled'], True)
        for artifact, output in (('capsule', 'capsule.tar'), ('kernel', 'bootstrap-kernel'),
                                 ('initramfs', 'bootstrap-initramfs')):
            self.assertEqual((self.work / output).read_bytes(), self.payloads[artifact])
        self.assertEqual(self.module.assemble(
            self.work, 'boxa', self.operation, owner=os.geteuid())['assembled'], False)
        self.assertEqual(self.sources['capsule'].read_bytes(), self.payloads['capsule'])

    def test_changed_or_extra_part_cannot_create_capsule(self):
        part = self.work / 'parts' / 'capsule' / 'part-0001'
        part.write_bytes(b'X' * 37)
        with self.assertRaisesRegex(RuntimeError, 'part bytes changed'):
            self.module.assemble(self.work, 'boxa', self.operation, owner=os.geteuid())
        self.assertFalse((self.work / 'capsule.tar').exists())
        part.write_bytes(b'B' * 37)
        (self.work / 'parts' / 'capsule' / 'extra').write_bytes(b'X')
        with self.assertRaisesRegex(RuntimeError, 'parts differ'):
            self.module.assemble(self.work, 'boxa', self.operation, owner=os.geteuid())

    def test_wrong_target_and_unsafe_source_fail_before_assembly(self):
        with self.assertRaisesRegex(RuntimeError, 'another operation'):
            self.module.assemble(self.work, 'boxb', self.operation, owner=os.geteuid())
        other = self.controller / 'other.tar'
        other.symlink_to(self.sources['capsule'])
        with self.assertRaisesRegex(UpdateError, 'unsafe metadata'):
            router_template_inputs.split_payload(other, self.controller / 'other-parts', self.expected['capsule'])
        with self.assertRaisesRegex(UpdateError, 'frozen build request'):
            router_template_inputs.split_payload(self.sources['capsule'], self.controller / 'other-parts',
                {**self.expected['capsule'], 'sha256': '0' * 64})

    def test_initial_preparation_reuses_only_the_two_bounded_boot_artifacts(self):
        shutil.rmtree(self.work / 'parts/capsule')
        self.write('parts.json', [part for part in self.parts if part['artifact'] != 'capsule'])
        request = {'bootstrap':{name:self.expected[name] for name in ('kernel', 'initramfs')}}
        self.assertTrue(self.module.assemble_boot(self.work, request, owner=os.geteuid()))
        self.assertFalse(self.module.assemble_boot(self.work, request, owner=os.geteuid()))
        self.assertEqual((self.work / 'bootstrap-kernel').read_bytes(), self.payloads['kernel'])
        self.assertEqual((self.work / 'bootstrap-initramfs').read_bytes(), self.payloads['initramfs'])
        self.assertFalse((self.work / 'capsule.tar').exists())
        (self.work / 'parts/capsule').mkdir(mode=0o700)
        with self.assertRaisesRegex(RuntimeError, 'exact manifest'):
            self.module.assemble_boot(self.work, request, owner=os.geteuid())


if __name__ == '__main__':
    unittest.main()
