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
        probe = patch.object(c, 'command', return_value='[{"domid":0}]')
        probe.start(); self.addCleanup(probe.stop)
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

    def test_controller_pending_and_previous_images_are_retained(self):
        legacy = {'origin': 'legacy-adoption'}
        current = {'image': self.ids[0], 'previous': legacy}
        pending = {'image': self.ids[1], 'previous': current}
        self.assertEqual(c.controller_image_references(pending),
                         [str(self.base / 'candidates' / image) for image in (self.ids[1], self.ids[0])])
        for invalid in ({'image': '../elsewhere'}, {'previous': current}, {'image': self.ids[0], 'previous': []}):
            with self.subTest(invalid=invalid), self.assertRaises(c.Refused): c.controller_image_references(invalid)

    def test_incomplete_controller_qualification_retains_its_image(self):
        with patch.object(c, 'BASE', self.base / 'templates'), patch.object(c, 'safe'):
            work = self.base / 'klokast-infrastructure/qualification' / self.ids[0]
            work.mkdir(parents=True)
            request = {'box': 'a', 'operation_id': work.name, 'image': self.ids[1]}
            c.store(work / 'request.json', request)
            self.assertEqual(c.controller_qualification_references(), [str(c.BASE / 'candidates' / self.ids[1])])
            result = {'kind': 'klokast.ops-replacement-qualification.v1', 'image': request['image'],
                      'box': 'a', 'complete': True, 'test_disks_removed': False}
            c.store(work / 'result.json', result)
            self.assertTrue(c.controller_qualification_references())
            (work / 'result.json').write_text(json.dumps(dict(result, test_disks_removed=True)))
            self.assertEqual(c.controller_qualification_references(), [])

    def test_keep_exact_qualified_build_and_preserve_references_not_mtime(self):
        plan = c.plan('a', self.ids[0], {self.ids[1]})
        self.assertEqual(plan['kept_candidates'], self.ids[:2])
        self.assertEqual([v['operation_id'] for v in plan['removals'] if v['retire_candidate']], self.ids[2:])
        self.assertNotIn(self.ids[1], [v['operation_id'] for v in plan['removals']])

    def test_preparation_cleanup_keeps_unused_qualified_candidates_until_selection(self):
        proposal = c.plan('a', self.ids[0], set(), preserve_qualified=True)
        self.assertEqual(proposal['kept_candidates'], self.ids)
        result = c.apply(proposal, proposal['plan_sha256'])
        self.assertEqual(result['retired_candidates'], [])
        for operation in self.ids:
            self.assertTrue((self.base / 'candidates' / operation / 'root').exists())
            self.assertFalse((self.base / 'staging' / operation / 'capsule.tar').exists())
        # Preparation selected an already-built unused image. Normal final
        # cleanup releases the other images, while the installed disk retains its own.
        final = c.plan('a', self.ids[-1], {self.ids[0]})
        self.assertEqual(final['kept_candidates'], [self.ids[0], self.ids[-1]])
        self.assertEqual(c.apply(final, final['plan_sha256'])['retired_candidates'], self.ids[1:-1])

    def test_two_image_qualification_retains_both_until_matching_cleanup_proof(self):
        with patch.object(c, 'BASE', self.base / 'templates'), patch.object(c, 'safe'):
            work = self.base / 'klokast-infrastructure/qualification' / self.ids[0]
            work.mkdir(parents=True)
            request = {'box': 'a', 'operation_id': work.name, 'image': self.ids[1], 'next_image': self.ids[2]}
            c.store(work / 'request.json', request)
            expected = [str(c.BASE / 'candidates' / v) for v in (self.ids[1], self.ids[2])]
            self.assertEqual(c.controller_qualification_references(), expected)
            result = dict(request, kind='klokast.ops-replacement-qualification.v1', complete=True, test_disks_removed=True)
            c.store(work / 'result.json', dict(result, next_image=self.ids[3]))
            self.assertEqual(c.controller_qualification_references(), expected)
            (work / 'result.json').write_text(json.dumps(result))
            self.assertEqual(c.controller_qualification_references(), [])
            (work / 'request.json').write_text(json.dumps(dict(request, next_image='../invalid')))
            with self.assertRaises(c.Refused): c.controller_qualification_references()

    def legacy_copy(self, operation):
        stage = self.base / 'staging' / operation
        for path in (stage / 'request.json', stage / 'candidate.json',
                     self.base / 'candidates' / operation / 'candidate.json'):
            value = c.read(path); value['box'] = 'original-box'
            path.write_bytes(c.canonical(value))
        path = stage / 'release-evidence.json'
        release = c.read(path)
        release['component_sha256']['candidate'] = c.digest(c.read(stage / 'candidate.json'))
        release.pop('release_sha256'); release['release_sha256'] = c.digest(release)
        path.write_bytes(c.canonical(release))

    def test_legacy_copy_can_be_retired_but_not_selected_or_removed_while_referenced(self):
        for operation in self.ids[:2]: self.legacy_copy(operation)
        with self.assertRaisesRegex(c.Refused, 'built on this box'):
            c.plan('a', self.ids[0], set())
        with self.assertRaisesRegex(c.Refused, 'metadata'):
            c.verify('a', self.ids[0])
        retained = self.base / 'candidates' / self.ids[1] / 'candidate.json'
        original = retained.read_bytes()
        plan = c.plan('a', self.ids[-1], {self.ids[1]})
        result = c.apply(plan, plan['plan_sha256'])
        self.assertTrue(result['complete'])
        self.assertIn(self.ids[0], result['retired_candidates'])
        self.assertNotIn(self.ids[1], result['retired_candidates'])
        self.assertEqual(retained.read_bytes(), original)

    def test_legacy_copy_with_conflicting_origin_is_preserved(self):
        self.legacy_copy(self.ids[0])
        path = self.base / 'staging' / self.ids[0] / 'request.json'
        value = c.read(path); value['box'] = 'another-box'
        path.write_bytes(c.canonical(value))
        plan = c.plan('a', self.ids[-1], set())
        result = c.apply(plan, plan['plan_sha256'])
        self.assertFalse(result['complete'])
        self.assertNotIn(self.ids[0], result['retired_candidates'])

    def test_legacy_copy_requires_matching_qualification(self):
        self.legacy_copy(self.ids[0])
        path = self.base / 'staging' / self.ids[0] / 'release-evidence.json'
        release = c.read(path); release['component_sha256']['candidate'] = 'f' * 64
        release.pop('release_sha256'); release['release_sha256'] = c.digest(release)
        path.write_bytes(c.canonical(release))
        plan = c.plan('a', self.ids[-1], set())
        self.assertIn(self.ids[0], {v['operation_id'] for v in plan['unknown_unchanged']})
        self.assertNotIn(self.ids[0], {v['operation_id'] for v in plan['removals']})

    def test_legacy_copy_requires_unchanged_artifact_bytes(self):
        self.legacy_copy(self.ids[0])
        (self.base / 'candidates' / self.ids[0] / 'root').write_bytes(b'corrupt')
        plan = c.plan('a', self.ids[-1], set())
        result = c.apply(plan, plan['plan_sha256'])
        self.assertFalse(result['complete'])
        self.assertNotIn(self.ids[0], result['retired_candidates'])

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
            if path.name == 'initramfs' and self.ids[0] in str(path): raise OSError('interrupted')
            return original(path, expected)
        with patch.object(c, 'checked_file', side_effect=interrupt), self.assertRaises(OSError):
            c.apply(plan, plan['plan_sha256'])
        next_plan = c.plan('a', self.ids[-1], set())
        self.assertIn(self.ids[0], {v['operation_id'] for v in next_plan['unknown_unchanged']})
        self.assertTrue((self.base / 'candidates' / self.ids[-1] / 'root').exists())
        saved = c.pending_plan('a', self.ids[-1])
        self.assertEqual(saved, plan)
        self.assertTrue(c.apply(saved, saved['plan_sha256'])['complete'])
        self.assertTrue(c.apply(saved, saved['plan_sha256'])['complete'])

    def test_new_reference_blocks_partial_plan_replay(self):
        plan = c.plan('a', self.ids[-1], set())
        with patch.object(c, 'references', return_value={self.ids[0]}), self.assertRaisesRegex(c.Refused, 'reference'):
            c.apply(plan, plan['plan_sha256'])
        self.assertTrue((self.base / 'candidates' / self.ids[0] / 'root').exists())

    def test_interruption_during_plan_publication_deletes_no_image(self):
        proposal = c.plan('a', self.ids[-1], set())
        original = c.os.replace
        def fail(source, destination):
            if destination.name == 'plan.json': raise OSError('publication interrupted')
            return original(source, destination)
        with patch.object(c.os, 'replace', side_effect=fail), self.assertRaises(OSError):
            c.apply(proposal, proposal['plan_sha256'])
        self.assertTrue(all((self.base / 'candidates' / image / 'root').exists() for image in self.ids))
        self.assertIsNone(c.pending_plan('a', self.ids[-1]))
        self.assertTrue(c.apply(proposal, proposal['plan_sha256'])['complete'])

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
            exists = Path.exists
            with patch.object(Path, 'exists', lambda path: str(path) == record['archived_lv'] or exists(path)):
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
