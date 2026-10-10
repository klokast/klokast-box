"""Exercise real disk transaction code against isolated LVM/Xen fixtures."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from test_infrastructure_guest import load, ROOT


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.m = load('replacement_fixture', ROOT / 'ansible/roles/infrastructure-guest/files/ops-controller-replacement')
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.m.BASE = self.root / 'ops'; self.m.BASE.mkdir()
        self.m.XEN = self.root / 'xen'; (self.m.XEN / 'auto').mkdir(parents=True)
        self.m.IMAGES = self.root / 'images'
        self.old_work = self.m.BASE / 'legacy'; self.old_work.mkdir()
        (self.old_work / 'ops.cfg').write_text('name = "ops"\n')
        self.old = dict(kind='klokast.infrastructure-assignment.v1', box='boxa', role='ops', stage='ready',
                        root_lv='/dev/vg0/lv_ops', root_partition='3', lv_uuid='old-lv', uuid='old-uuid', work=str(self.old_work))
        self.m.write(self.m.BASE / 'assignment.json', self.old)
        self.live = self.guest(self.old)
        self.lvs = {self.old['root_lv']: {'lv_uuid': 'old-lv', 'lv_tags': ''}}
        self.events = []
        self.images = {}
        for op in ('a' * 24, 'b' * 24):
            directory = self.m.IMAGES / op; directory.mkdir(parents=True)
            artifacts = {}
            for name in ('root', 'kernel', 'initramfs'):
                content = (op + name).encode(); (directory / name).write_bytes(content)
                artifacts[name] = dict(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
            self.images[op] = {'artifacts': artifacts}
        self.config = self.root / 'configuration.json'
        self.config.write_text(json.dumps({'box': 'boxa', 'role': 'ops', 'active_box': 'boxb',
                                          'engine_commit': 'c' * 40, 'instance_commit': 'd' * 40}))
        self.receipt = self.root / 'receipt.json'; self.receipt.write_text('{}')
        for name, fn in (
            ('safe_file', lambda *_: None), ('safe_directory', lambda *_: None),
            ('qualified', lambda box, op, receipt: self.images[op]),
            ('domain', lambda name: self.live if name == 'ops' else None),
            ('require_identity', lambda record, identity: self.assertEqual(record['config']['c_info']['uuid'], identity)),
            ('run', self.run_command), ('finalize', self.finalize), ('install_boot', lambda work: self.events.append('install-boot'))):
            p = patch.object(self.m, name, fn); p.start(); self.addCleanup(p.stop)
        for name, fn in (('lv_info', lambda path: self.lvs.get(str(path))),
                         ('capacity', lambda size: self.events.append('capacity'))):
            p = patch.object(self.m.infra, name, fn); p.start(); self.addCleanup(p.stop)

    def guest(self, state):
        return {'domid': 8, 'config': {'c_info': {'uuid': state['uuid']}, 'disks': [{'pdev_path': state['root_lv']}]}}

    def run_command(self, argv, **kwargs):
        self.events.append(str(argv[0]))
        if argv[0] == 'lvcreate':
            path = '/dev/vg0/' + argv[argv.index('--name') + 1]
            self.lvs[path] = dict(lv_uuid=path + '-uuid', lv_tags=argv[argv.index('--addtag') + 1])
        if argv[:2] == ['xl', 'shutdown']: self.live = None
        if argv[:2] == ['xl', 'create']:
            state = self.state()
            self.live = self.guest(state if state['stage'] == 'boot-requested' else state['previous'])
        return SimpleNamespace(stdout='', returncode=0)

    def finalize(self, work, state):
        self.assertIsNone(self.live)
        self.assertEqual(state['previous']['lv_uuid'], self.lvs[state['previous']['root_lv']]['lv_uuid'])
        self.events.append('preserve-private-state')
        (work / 'private-state-proof').write_text('same-controller-identity')

    def state(self): return json.loads((self.m.BASE / 'replacement.json').read_text())
    def replace(self, image='a' * 24): return self.m.replace('boxa', image, self.config, self.receipt)
    def resume(self): return self.m.replace('boxa', operation=self.state()['operation_id'], action='resume')
    def accept(self): return self.m.replace('boxa', operation=self.state()['operation_id'], action='accept')
    def rollback(self): return self.m.replace('boxa', operation=self.state()['operation_id'], action='rollback')

    def test_two_successive_generations_retain_old_disks_and_replay_without_restart(self):
        for image in ('a' * 24, 'b' * 24):
            self.assertEqual(self.replace(image)['stage'], 'booted')
            self.assertEqual(self.accept()['stage'], 'accepted')
            events = list(self.events)
            self.assertFalse(self.replace(image)['changed'])
            self.assertEqual(self.events, events)
        self.assertEqual(len(self.lvs), 3)
        self.assertNotIn('lvremove', self.events)

    def test_each_durable_stage_resumes_same_image_without_duplicate_identity(self):
        for stage in ('allocated', 'copied', 'stopped', 'finalized', 'boot-requested', 'booted', 'accepted'):
            with self.subTest(stage=stage):
                # A distinct fixture avoids stage or mock state from prior cases.
                fixture = ReplacementTests(); fixture.setUp()
                try:
                    original = fixture.m.write
                    interrupted = False
                    def write(path, value):
                        nonlocal interrupted
                        original(path, value)
                        if path.name == 'replacement.json' and value['stage'] == stage and not interrupted:
                            interrupted = True
                            raise RuntimeError('terminal lost')
                    with patch.object(fixture.m, 'write', side_effect=write):
                        with self.assertRaisesRegex(RuntimeError, 'terminal lost'):
                            fixture.replace()
                            fixture.accept()
                    result = fixture.resume()
                    self.assertIn(result['stage'], ('booted', 'accepted'))
                    fixture.accept()
                    self.assertEqual(fixture.state()['image'], 'a' * 24)
                    self.assertEqual(len(fixture.lvs), 2)
                    self.assertNotIn('lvremove', fixture.events)
                finally: fixture.doCleanups()

    def test_allocation_interruption_recovers_exact_tagged_disk(self):
        original = self.run_command
        def interrupt(argv, **kwargs):
            value = original(argv, **kwargs)
            if argv[0] == 'lvcreate': raise RuntimeError('lost allocation reply')
            return value
        with patch.object(self.m, 'run', side_effect=interrupt), self.assertRaises(RuntimeError): self.replace()
        self.assertEqual(self.resume()['stage'], 'booted')
        self.assertEqual(len(self.lvs), 2)

    def test_pending_operation_refuses_new_image(self):
        with patch.object(self.m, 'finalize', side_effect=RuntimeError('interrupted')), self.assertRaises(RuntimeError): self.replace()
        with self.assertRaisesRegex(RuntimeError, 'another controller replacement'): self.replace('b' * 24)
        self.assertEqual(self.resume()['stage'], 'booted')

    def test_capacity_and_unknown_disk_refuse_before_shutdown(self):
        with patch.object(self.m.infra, 'capacity', side_effect=RuntimeError('insufficient capacity')), self.assertRaisesRegex(RuntimeError, 'capacity'):
            self.replace()
        self.assertFalse((self.m.BASE / 'replacement.json').exists())
        self.lvs[self.old['root_lv']]['lv_uuid'] = 'different'
        with self.assertRaisesRegex(RuntimeError, 'disk UUID differs'): self.replace()
        self.assertIsNotNone(self.live)

    def test_preboot_rollback_retains_both_disks(self):
        with patch.object(self.m, 'finalize', side_effect=RuntimeError('interrupted')), self.assertRaises(RuntimeError): self.replace()
        self.assertEqual(self.rollback()['stage'], 'rolled-back')
        self.assertEqual(self.live['config']['c_info']['uuid'], self.old['uuid'])
        self.assertEqual(len(self.lvs), 2)
        with self.assertRaisesRegex(RuntimeError, 'rolled back'): self.resume()

    def test_postboot_rollback_refuses_newer_private_state_without_stopping_guest(self):
        self.replace(); current = copy.deepcopy(self.live)
        with self.assertRaisesRegex(RuntimeError, 'newer private state'): self.rollback()
        self.assertEqual(self.live, current)
        self.assertEqual(len(self.lvs), 2)

    def test_replaced_disk_identity_blocks_acceptance(self):
        self.replace(); self.lvs[self.state()['root_lv']]['lv_uuid'] = 'foreign'
        with self.assertRaisesRegex(RuntimeError, 'disk UUID differs'): self.accept()
        self.assertEqual(self.state()['stage'], 'booted')


class StateCopyTests(unittest.TestCase):
    def test_copy_preserves_private_bytes_and_refuses_links(self):
        module = load('replacement_copy', ROOT / 'ansible/roles/vm-template-builder/files/vm-infrastructure-finalize')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); source = base / 'source'; source.mkdir()
            (source / 'journal').write_text('new private state'); (source / 'journal').chmod(0o600)
            destination = base / 'replacement'
            with patch.object(module.os, 'chown'):
                module.copy_state(source, destination)
                self.assertEqual((destination / 'journal').read_text(), 'new private state')
                self.assertEqual((destination / 'journal').stat().st_mode & 0o777, 0o600)
                (source / 'escape').symlink_to('/etc')
                with self.assertRaisesRegex(RuntimeError, 'symlink'): module.copy_state(source, destination)
        self.assertNotIn('etc/klokast/controller-ha.json', module.PRESERVE)
        self.assertNotIn('etc/klokast/tailscale-policy.env', module.PRESERVE)
        self.assertNotIn('home/agent', module.PRESERVE)


if __name__ == '__main__': unittest.main()
