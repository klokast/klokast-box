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

    def test_retired_vpn_operation_releases_image_references_but_retains_audit(self):
        work=self.base/'retired';work.mkdir()
        root='/dev/vg0/vmupd_'+'a'*24+'_root'
        record={'kind':'klokast.vm-service-update.v1','role':'vpn-egress','new_disks':{root:{}}}
        request={'operation_id':'a'*24}
        c.store(work/'request.json',request)
        proof={'kind':'klokast.vpn-generation-retired.v1','record_sha256':c.digest(record),
               'request_sha256':c.digest(request),'plan_sha256':'b'*64,'root_lv':root}
        c.store(work/'retired.json',proof)
        self.assertTrue(c.service_retired(work,record))
        self.assertTrue((work/'request.json').exists())
        (work/'kernel').write_text('unexpected boot file')
        with self.assertRaisesRegex(c.Refused,'boot files remain'):c.service_retired(work,record)
        (work/'kernel').unlink()
        proof['record_sha256']='c'*64;(work/'retired.json').write_text(json.dumps(proof))
        with self.assertRaisesRegex(c.Refused,'proof differs'):c.service_retired(work,record)

    def test_retired_dmz_releases_image_while_persistent_data_remains(self):
        for role,char in (('bak','d'),('dmz','e'),('iot','f')):
            with self.subTest(role=role):
                operation=char*24;work=self.base/operation;work.mkdir()
                root='/dev/vg0/vmupd_'+operation+'_root'
                data=self.base/('persistent-'+role+'-data');data.write_text('kept')
                record={'kind':'klokast.vm-service-update.v1','role':role,'new_disks':{root:{},str(data):{}}}
                request={'operation_id':operation}
                c.store(work/'request.json',request)
                c.store(work/'retired.json',{'kind':'klokast.'+role+'-generation-retired.v1',
                    'record_sha256':c.digest(record),'request_sha256':c.digest(request),
                    'plan_sha256':'b'*64,'root_lv':root})
                self.assertTrue(c.service_retired(work,record))
                self.assertEqual(data.read_text(),'kept')

    def retired_candidate(self, role='ops'):
        updates=self.base/('retired-'+role)
        work=updates/'operations'/('e'*24)
        work.mkdir(parents=True);(updates/'active').mkdir()
        root='/dev/vg0/vmupd_'+work.name+'_root'
        config=work/'new.cfg';config.write_text('checked candidate audit')
        checksum=hashlib.sha256(config.read_bytes()).hexdigest()
        request={'kind':'klokast.vm-switch.v5','operation_id':work.name,'box':'a','role':role,
                 'new_config_sha256':checksum,'old_uuid':'accepted-source','old_disks':{'source-root':{}},
                 'old_artifacts':{'source-kernel':{}}}
        record={'kind':'klokast.vm-service-update.v1','operation_id':work.name,'box':'a','role':role,
                'stage':'abandoned','image':self.ids[0],'new_disks':{root:{}}}
        proof={'kind':'klokast.'+role+'-generation-retired.v1','record_sha256':c.digest(record),
               'request_sha256':c.digest(request),'plan_sha256':'b'*64,'root_lv':root,
               'files':{},'audit_files':{'new.cfg':{'sha256':checksum,'bytes':config.stat().st_size}}}
        c.store(work/'request.json',request);c.store(work/'service.json',record)
        c.store(work/'journal.json',{'stage':'recovered','request_sha256':c.digest(request),
                'previous_assignment':{'profile':role+'-alpine-v1','release_sha256':'c'*64}})
        c.store(work/'retired.json',proof)
        c.store(updates/'active'/(role+'.json'),{'operation_id':work.name,'request_sha256':c.digest(request)})
        return updates,work,record,request,proof

    def test_retired_infrastructure_candidate_keeps_checked_recovery_audit(self):
        for role in ('ops','air'):
            with self.subTest(role=role):
                _,work,record,_,_=self.retired_candidate(role)
                before={p:p.read_bytes() for p in work.iterdir()}
                self.assertTrue(c.service_retired(work,record))
                self.assertEqual(before,{p:p.read_bytes() for p in work.iterdir()})

    def test_retired_candidate_audit_rejects_changes_symlinks_and_accepted_generation(self):
        _,work,record,_,proof=self.retired_candidate()
        config=work/'new.cfg';original=config.read_bytes()
        config.write_text('changed audit')
        with self.assertRaisesRegex(c.Refused,'audit configuration changed'):c.service_retired(work,record)
        config.unlink();other=work/'other';other.write_bytes(original);config.symlink_to(other)
        with self.assertRaisesRegex(c.Refused,'unsafe artifact'):c.service_retired(work,record)
        config.unlink();config.write_bytes(original)
        accepted=dict(record,stage='complete')
        (work/'retired.json').write_bytes(c.canonical(dict(proof,record_sha256=c.digest(accepted))))
        with self.assertRaisesRegex(c.Refused,'audit configuration differs'):c.service_retired(work,accepted)
        (work/'retired.json').write_bytes(c.canonical(proof))
        journal=c.read(work/'journal.json');journal['stage']='booted'
        (work/'journal.json').write_bytes(c.canonical(journal))
        with self.assertRaisesRegex(c.Refused,'matching recovery evidence'):c.service_retired(work,record)

    def test_recovered_retired_pointer_keeps_its_exact_accepted_source_image(self):
        for role in ('ops','air'):
            with self.subTest(role=role):
                updates,_,_,request,_=self.retired_candidate(role)
                source=updates/'operations'/('f'*24);source.mkdir()
                previous={'kind':'klokast.vm-switch.v5','operation_id':source.name,'box':'a','role':role,
                          'new_uuid':request['old_uuid'],'new_disks':request['old_disks'],
                          'new_artifacts':request['old_artifacts'],'release_sha256':'c'*64,
                          'image_profile':role+'-alpine-v1'}
                c.store(source/'request.json',previous)
                c.store(source/'journal.json',{'stage':'complete','request_sha256':c.digest(previous)})
                c.store(source/'service.json',{'operation_id':source.name,'box':'a','role':role,
                        'image':self.ids[1],'reboot_verification':{'status':'complete'}})
                with patch.object(c,'UPDATES',updates):
                    references=c.recovery_references()
                    self.assertIn(str(self.base/'candidates'/self.ids[1]),references)
                    self.assertNotIn(str(self.base/'candidates'/self.ids[0]),references)
                    previous['new_disks']={'another-source':{}}
                    (source/'request.json').write_bytes(c.canonical(previous))
                    (source/'journal.json').write_bytes(c.canonical({'stage':'complete','request_sha256':c.digest(previous)}))
                    with self.assertRaisesRegex(c.Refused,'one accepted source'):c.recovery_references()

    def test_active_retired_accepted_generation_still_refuses_image_cleanup(self):
        updates,work,record,request,proof=self.retired_candidate()
        record['stage']='complete';proof['record_sha256']=c.digest(record);proof['audit_files']={}
        (work/'new.cfg').unlink()
        (work/'service.json').write_bytes(c.canonical(record))
        (work/'retired.json').write_bytes(c.canonical(proof))
        (work/'journal.json').write_bytes(c.canonical({'stage':'complete','request_sha256':c.digest(request)}))
        with patch.object(c,'UPDATES',updates),self.assertRaisesRegex(c.Refused,'current VM assignment'):
            c.recovery_references()

    def test_initial_dmz_requires_matching_retirement_proof_before_release(self):
        for role in ('bak','dmz','iot'):
            with self.subTest(role=role):
                work=self.base/('initial-'+role);work.mkdir()
                record={'kind':'klokast.infrastructure-assignment.v1','role':role}
                self.assertFalse(c.service_retired(work,record))
                c.store(work/'retired.json',{'kind':'klokast.'+role+'-generation-retired.v1',
                    'record_sha256':c.digest(record),'request_sha256':None,
                    'plan_sha256':'b'*64,'root_lv':'/dev/vg0/lv_'+role})
                self.assertTrue(c.service_retired(work,record))
                (work/(role+'.cfg')).write_text('still referenced')
                with self.assertRaisesRegex(c.Refused,'boot files remain'):c.service_retired(work,record)

    def test_live_backend_creation_keeps_its_image_reference_until_retired(self):
        work = self.base / 'klokast-infrastructure/bak'
        work.mkdir(parents=True)
        record = {'kind': 'klokast.infrastructure-assignment.v1', 'role': 'bak',
                  'stage': 'ready', 'operation_id': self.ids[0]}
        c.store(work/'assignment.json', record)
        with patch.object(c, 'BASE', self.base/'templates'), \
             patch.object(c, 'recovery_references', side_effect=lambda: []), \
             patch.object(c, 'controller_qualification_references', side_effect=lambda: []):
            self.assertIn(self.ids[0], c.references())
            c.store(work/'retired.json', {
                'kind': 'klokast.bak-generation-retired.v1', 'record_sha256': c.digest(record),
                'request_sha256': None, 'plan_sha256': 'a'*64, 'root_lv': '/dev/vg0/lv_bak'})
            self.assertNotIn(self.ids[0], c.references())

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
