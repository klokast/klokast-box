"""Verify disposable cleanup retention, integrity, and audit boundaries."""
import hashlib
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

c = SourceFileLoader('template_cleanup', str(Path(__file__).resolve().parents[1] /
    'roles/vm-template-cleanup/files/vm-template-cleanup')).load_module()


class Cleanup(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        for name in ('candidates', 'staging', 'cleanup'): (self.base / name).mkdir()
        mock = patch.object(c, 'BASE', self.base); mock.start(); self.addCleanup(mock.stop)
        self.ids = [format(i, '024x') for i in range(1, 5)]
        for i, operation in enumerate(self.ids):
            candidate = self.base / 'candidates' / operation; candidate.mkdir()
            stage = self.base / 'staging' / operation; stage.mkdir()
            def artifact(path):
                path.write_bytes(b'public fixture ' + path.name.encode())
                return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
            request = {'kind': 'klokast.vm-template-build-request.v1', 'box': 'a',
                       'operation_id': operation, 'inputs_sha256': 'a' * 64,
                       'capsule': artifact(stage / 'capsule.tar'),
                       'bootstrap': {name: artifact(stage / ('bootstrap-' + name)) for name in ('kernel', 'initramfs')}}
            record = {'kind': 'klokast.vm-template-candidate.v1', 'accepted': False, 'success': True,
                      'validation': 'base-boot-tested', 'box': 'a', 'operation_id': operation,
                      'inputs_sha256': 'a' * 64,
                      'artifacts': {name: artifact(candidate / name) for name in ('root', 'kernel', 'initramfs')}}
            for path, value in ((candidate / 'candidate.json', record), (stage / 'candidate.json', record),
                                (stage / 'request.json', request),
                                (stage / 'lifecycle.json', {'stage': 'cleaned', 'domain': 'vm-build-' + operation}),
                                (stage / 'test-lifecycle.json', {'stage': 'cleaned', 'domain': 'vm-test-' + operation})):
                c.store(path, value)
            os.utime(candidate / 'candidate.json', ns=(i + 1, i + 1))

    def test_keep_two_newest_and_preserve_references_and_pins(self):
        plan = c.plan('a', [self.ids[0]], {self.ids[1]})
        self.assertEqual(plan['kept_candidates'], self.ids)
        self.assertTrue(all(not v['retire_candidate'] for v in plan['removals']))
        self.assertNotIn(self.ids[1], [v['operation_id'] for v in plan['removals']])

    def test_apply_keeps_audit_and_two_generations(self):
        plan = c.plan('a', [], set())
        result = c.apply(plan, plan['plan_sha256'])
        self.assertTrue(result['complete'])
        self.assertEqual(sorted(p.name for p in (self.base / 'candidates').iterdir()), self.ids[2:])
        for operation in self.ids:
            self.assertTrue((self.base / 'staging' / operation / 'request.json').exists())
            self.assertTrue((self.base / 'cleanup' / plan['plan_sha256'] / (operation + '-candidate.json')).exists())
            self.assertFalse((self.base / 'staging' / operation / 'capsule.tar').exists())

    def test_unknown_or_corrupt_artifacts_remain(self):
        (self.base / 'candidates' / self.ids[0] / 'root').write_bytes(b'corrupt')
        (self.base / 'candidates' / self.ids[1] / 'unknown').touch()
        plan = c.plan('a', [], set())
        self.assertEqual({v['operation_id'] for v in plan['unknown_unchanged']}, set(self.ids[:2]))
        self.assertFalse(any(v['retire_candidate'] for v in plan['removals']))

    def test_incomplete_lifecycle_is_not_cleanup_authority(self):
        path = self.base / 'staging' / self.ids[0] / 'lifecycle.json'
        path.write_text(json.dumps({'stage': 'allocated', 'domain': 'vm-build-' + self.ids[0]}))
        plan = c.plan('a', [], set())
        self.assertNotIn(self.ids[0], [v['operation_id'] for v in plan['removals']])
        with self.assertRaises(c.Refused): c.plan('a', [self.ids[0]], set())

    def test_changed_plan_or_artifact_refuses_deletion(self):
        plan = c.plan('a', [], set())
        with self.assertRaises(c.Refused): c.apply(plan, 'f' * 64)
        root = self.base / 'candidates' / self.ids[0] / 'root'
        root.write_bytes(b'changed')
        with self.assertRaises(c.Refused): c.apply(plan, plan['plan_sha256'])
        self.assertTrue(root.exists())
        self.assertTrue((self.base / 'cleanup' / plan['plan_sha256'] / 'plan.json').exists())

    def test_production_records_refuse_setup_cleanup(self):
        updates = self.base / 'production'; (updates / 'operations').mkdir(parents=True)
        (updates / 'operations' / 'operation').mkdir()
        with patch.object(c, 'UPDATES', updates):
            with self.assertRaisesRegex(c.Refused, 'production'): c.references()

    def test_empty_preflight_trial_requires_no_operations_or_disks(self):
        trials = self.base / 'trials'; trial = trials / self.ids[0]
        for name in ('active', 'operations'): (trial / 'state' / name).mkdir(parents=True)
        with patch.object(c, 'TRIALS', trials), patch.object(c, 'command', return_value='{"report":[{"lv":[]}]}') as command:
            c.trial_cleanup()
            command.return_value = json.dumps({'report': [{'lv': [{'lv_name': 'vmupdtest_' + self.ids[0] + '_old_os'}]}]})
            with self.assertRaisesRegex(c.Refused, 'still has disks'): c.trial_cleanup()
            command.return_value = '{"report":[{"lv":[]}]}'
            (trial / 'state/operations' / self.ids[1]).mkdir()
            with self.assertRaisesRegex(c.Refused, 'unfinished'): c.trial_cleanup()


if __name__ == '__main__': unittest.main()
