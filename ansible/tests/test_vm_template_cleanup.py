"""Verify disposable cleanup retention, integrity, and audit boundaries."""
import hashlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

loader = SourceFileLoader('template_cleanup', str(Path(__file__).resolve().parents[1] /
    'roles/vm-template-cleanup/files/vm-template-cleanup'))
c = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(c)


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
            inputs = {'kind': 'klokast.vm-template-inputs.v1', 'profile': 'shared-alpine-v1',
                      'architecture': 'x86_64'}
            inputs['inputs_sha256'] = c.digest(inputs)
            request = {'kind': 'klokast.vm-template-build-request.v1', 'box': 'a',
                       'operation_id': operation, 'inputs_sha256': inputs['inputs_sha256'],
                       'capsule': artifact(stage / 'capsule.tar'),
                       'bootstrap': {name: artifact(stage / ('bootstrap-' + name)) for name in ('kernel', 'initramfs')}}
            record = {'kind': 'klokast.vm-template-candidate.v1', 'accepted': False, 'success': True,
                      'validation': 'base-boot-tested', 'box': 'a', 'operation_id': operation,
                      'inputs_sha256': inputs['inputs_sha256'],
                      'artifacts': {name: artifact(candidate / name) for name in ('root', 'kernel', 'initramfs')}}
            release = {'kind': 'klokast.vm-release.v2', 'profile': inputs['profile'],
                       'architecture': inputs['architecture'], 'inputs_sha256': inputs['inputs_sha256'],
                       'artifacts': record['artifacts'], 'component_sha256': {'candidate': c.digest(record)}}
            release['release_sha256'] = c.digest(release)
            for path, value in ((candidate / 'candidate.json', record), (stage / 'candidate.json', record),
                                (stage / 'inputs.json', inputs), (stage / 'release-evidence.json', release),
                                (stage / 'request.json', request),
                                (stage / 'lifecycle.json', {'stage': 'cleaned', 'domain': 'vm-build-' + operation}),
                                (stage / 'test-lifecycle.json', {'stage': 'cleaned', 'domain': 'vm-test-' + operation})):
                c.store(path, value)
            os.utime(candidate / 'candidate.json', ns=(i + 1, i + 1))

    def test_keep_exact_qualified_build_and_preserve_references_not_mtime(self):
        plan = c.plan('a', self.ids[0], {self.ids[1]})
        self.assertEqual(plan['kept_candidates'], self.ids[:2])
        self.assertEqual([v['operation_id'] for v in plan['removals'] if v['retire_candidate']], self.ids[2:])
        self.assertNotIn(self.ids[1], [v['operation_id'] for v in plan['removals']])

    def test_verify_reads_exact_candidate_without_deletion_or_reference_inspection(self):
        before = sorted(str(path.relative_to(self.base)) for path in self.base.rglob('*'))
        with patch.object(c, 'references', side_effect=AssertionError('not a cleanup operation')):
            result = c.verify('a', self.ids[0])
        self.assertTrue(result['verified'])
        self.assertEqual(result['current'], self.ids[0])
        self.assertEqual(result['candidate_sha256'], c.digest(c.read(self.base / 'candidates' / self.ids[0] / 'candidate.json')))
        self.assertEqual(before, sorted(str(path.relative_to(self.base)) for path in self.base.rglob('*')))

    def test_verify_missing_and_checksum_damaged_bytes_request_rebuild(self):
        self.assertEqual(c.verify('a', 'f' * 24)['reason'], 'missing')
        path = self.base / 'candidates' / self.ids[0] / 'root'
        original = path.read_bytes()
        path.write_bytes(b'x' * len(original))
        self.assertEqual(c.verify('a', self.ids[0])['reason'], 'damaged')
        self.assertTrue(path.exists())
        path.unlink()
        self.assertEqual(c.verify('a', self.ids[0])['reason'], 'missing')

    def test_verify_refuses_unsafe_paths_invalid_metadata_and_unknown_files(self):
        path = self.base / 'candidates' / self.ids[0] / 'root'
        path.unlink(); path.symlink_to(self.base / 'candidates' / self.ids[1] / 'root')
        with self.assertRaisesRegex(c.Refused, 'unsafe'): c.verify('a', self.ids[0])
        with self.assertRaisesRegex(c.Refused, 'metadata'): c.verify('other-box', self.ids[1])
        (self.base / 'candidates' / self.ids[2] / 'unknown').touch()
        with self.assertRaisesRegex(c.Refused, 'unknown'): c.verify('a', self.ids[2])
        with self.assertRaises(c.Refused): c.verify('a', '../escape')

    def test_apply_keeps_audit_and_one_generation(self):
        plan = c.plan('a', self.ids[-1], set())
        result = c.apply(plan, plan['plan_sha256'])
        self.assertTrue(result['complete'])
        self.assertEqual(sorted(p.name for p in (self.base / 'candidates').iterdir()), self.ids[-1:])
        self.assertEqual(result['retired_candidates'], self.ids[:-1])
        for operation in self.ids:
            self.assertTrue((self.base / 'staging' / operation / 'request.json').exists())
            self.assertTrue((self.base / 'cleanup' / plan['plan_sha256'] / (operation + '-candidate.json')).exists())
            self.assertFalse((self.base / 'staging' / operation / 'capsule.tar').exists())

    def test_unknown_or_corrupt_artifacts_remain(self):
        (self.base / 'candidates' / self.ids[0] / 'root').write_bytes(b'corrupt')
        (self.base / 'candidates' / self.ids[1] / 'unknown').touch()
        plan = c.plan('a', self.ids[-1], set())
        self.assertEqual({v['operation_id'] for v in plan['unknown_unchanged']}, set(self.ids[:2]))
        result = c.apply(plan, plan['plan_sha256'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['retired_candidates'], [self.ids[2]])

    def test_incomplete_lifecycle_is_not_cleanup_authority(self):
        path = self.base / 'staging' / self.ids[0] / 'lifecycle.json'
        path.write_text(json.dumps({'stage': 'allocated', 'domain': 'vm-build-' + self.ids[0]}))
        plan = c.plan('a', self.ids[-1], set())
        self.assertNotIn(self.ids[0], [v['operation_id'] for v in plan['removals']])
        with self.assertRaises(c.Refused): c.plan('a', self.ids[0], set())

    def test_controller_receipts_match_after_successful_staging_removal(self):
        evidence = self.base / 'cleanup-evidence'; evidence.mkdir()
        stage = self.base / 'staging' / self.ids[0]
        for name in ('capsule.tar', 'bootstrap-kernel', 'bootstrap-initramfs'): (stage / name).unlink()
        stage.rename(evidence / self.ids[0])
        plan = c.plan('a', self.ids[-1], set())
        self.assertFalse(plan['unknown_unchanged'])
        self.assertTrue(next(v for v in plan['removals'] if v['operation_id'] == self.ids[0])['retire_candidate'])

    def test_changed_plan_or_artifact_refuses_deletion(self):
        plan = c.plan('a', self.ids[-1], set())
        with self.assertRaises(c.Refused): c.apply(plan, 'f' * 64)
        root = self.base / 'candidates' / self.ids[0] / 'root'
        root.write_bytes(b'changed')
        with self.assertRaises(c.Refused): c.apply(plan, plan['plan_sha256'])
        self.assertTrue(root.exists())
        self.assertTrue((self.base / 'cleanup' / plan['plan_sha256'] / 'plan.json').exists())

    def test_bad_or_missing_replacement_does_not_retire_good_images(self):
        stage = self.base / 'staging' / self.ids[-1]
        (stage / 'release-evidence.json').unlink()
        with self.assertRaises(OSError): c.plan('a', self.ids[-1], set())
        self.assertEqual(len(list((self.base / 'candidates').iterdir())), 4)
        (self.base / 'candidates' / self.ids[0] / 'kernel').write_bytes(b'corrupt')
        with self.assertRaisesRegex(c.Refused, 'differs'):
            c.plan('a', self.ids[0], set())

    def test_other_profile_and_architecture_are_untouched(self):
        for operation, change in ((self.ids[0], {'profile': 'other-v1'}),
                                  (self.ids[1], {'architecture': 'aarch64'})):
            stage = self.base / 'staging' / operation
            inputs = c.read(stage / 'inputs.json')
            inputs.update(change); inputs.pop('inputs_sha256')
            inputs['inputs_sha256'] = c.digest(inputs)
            (stage / 'inputs.json').write_bytes(c.canonical(inputs))
            for path in (stage / 'request.json', stage / 'candidate.json',
                         self.base / 'candidates' / operation / 'candidate.json'):
                value = c.read(path); value['inputs_sha256'] = inputs['inputs_sha256']
                path.write_bytes(c.canonical(value))
        plan = c.plan('a', self.ids[-1], set())
        self.assertEqual(plan['kept_candidates'], [self.ids[0], self.ids[1], self.ids[-1]])
        self.assertFalse(set(self.ids[:2]) & {v['operation_id'] for v in plan['removals']})

    def test_diagnostic_candidate_cannot_retire_normal_image(self):
        path = self.base / 'staging' / self.ids[-1] / 'request.json'
        value = c.read(path); value['app_test'] = {}
        path.write_bytes(c.canonical(value))
        with self.assertRaisesRegex(c.Refused, 'qualification'):
            c.plan('a', self.ids[-1], set())

    def test_interrupted_cleanup_keeps_current_image_and_reports_partial_old_image(self):
        plan = c.plan('a', self.ids[-1], set())
        original = c.checked_file
        def interrupt(path, expected):
            if path.name == 'initramfs': raise OSError('interrupted')
            return original(path, expected)
        with patch.object(c, 'checked_file', side_effect=interrupt), self.assertRaises(OSError):
            c.apply(plan, plan['plan_sha256'])
        next_plan = c.plan('a', self.ids[-1], set())
        self.assertIn(self.ids[0], {v['operation_id'] for v in next_plan['unknown_unchanged']})
        self.assertTrue((self.base / 'candidates' / self.ids[-1] / 'root').exists())

    def test_unknown_transaction_refuses_cleanup(self):
        updates = self.base / 'production'; (updates / 'operations').mkdir(parents=True)
        (updates / 'operations' / 'operation').mkdir()
        with patch.object(c, 'UPDATES', updates):
            with self.assertRaisesRegex(c.Refused, 'unknown VM transaction'): c.references()

    def recovery_fixture(self):
        updates = self.base / 'recovery'
        work = updates / 'operations' / self.ids[0]
        work.mkdir(parents=True); (updates / 'active').mkdir()
        request = {'kind': 'klokast.vm-switch.v2', 'role': 'dmz',
                   'operation_id': self.ids[0],
                   'new_artifacts': {str(self.base / 'candidates' / self.ids[1] / 'kernel'): {}}}
        c.store(work / 'request.json', request)
        c.store(work / 'journal.json', {'stage': 'complete', 'request_sha256': c.digest(request)})
        c.store(updates / 'active/dmz.json', {'operation_id': self.ids[0], 'request_sha256': c.digest(request)})
        return updates, work

    def test_completed_recovery_keeps_referenced_candidate_and_records(self):
        updates, work = self.recovery_fixture()
        before = {p: p.read_bytes() for p in updates.rglob('*.json')}
        with patch.object(c, 'UPDATES', updates), patch.object(c, 'trial_cleanup'), \
                patch.object(c, 'command', return_value='[{"domid":0}]'):
            referenced = c.references()
        self.assertIn(self.ids[1], referenced)
        result = c.apply(c.plan('a', self.ids[-1], referenced), c.plan('a', self.ids[-1], referenced)['plan_sha256'])
        self.assertNotIn(self.ids[1], result['retired_candidates'])
        self.assertEqual(before, {p: p.read_bytes() for p in updates.rglob('*.json')})

    def test_pending_or_changed_recovery_refuses_cleanup(self):
        updates, work = self.recovery_fixture()
        journal = c.read(work / 'journal.json')
        for change in ({'stage': 'accepted'}, {'request_sha256': 'f' * 64}, {'old_shutdown_pending': True}):
            (work / 'journal.json').write_bytes(c.canonical(dict(journal, **change)))
            with patch.object(c, 'UPDATES', updates), self.assertRaisesRegex(c.Refused, 'pending or invalid'):
                c.recovery_references()

    def test_changed_active_pointer_refuses_cleanup(self):
        updates, work = self.recovery_fixture()
        (updates / 'active/dmz.json').write_text(json.dumps({'operation_id': self.ids[2]}))
        with patch.object(c, 'UPDATES', updates), self.assertRaisesRegex(c.Refused, 'active VM assignment'):
            c.recovery_references()

    def test_retired_infrastructure_requires_absent_disk_and_boot_config(self):
        work = self.base / 'klokast-infrastructure/failed-air-test'; work.mkdir(parents=True)
        record = {'kind': 'klokast.infrastructure-assignment.v1', 'stage': 'retired-failed',
                  'role': 'air', 'operation_id': self.ids[0],
                  'archived_lv': '/dev/vg0/lv_air_failed_' + 'f' * 32}
        c.store(work / 'assignment.json', record)
        with patch.object(c, 'BASE', self.base / 'templates'), patch.object(c, 'recovery_references', return_value=[]), \
                patch.object(c, 'trial_cleanup'), patch.object(c, 'command', return_value='[{"domid":0}]'):
            self.assertNotIn(self.ids[0], c.references())
            (work / 'air.cfg').symlink_to(work / 'missing.cfg')
            with self.assertRaisesRegex(c.Refused, 'still has resources'): c.references()
            (work / 'air.cfg').unlink()
            with patch.object(Path, 'exists', return_value=True):
                with self.assertRaisesRegex(c.Refused, 'still has resources'): c.references()

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
