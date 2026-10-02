"""Failed template cleanup leaves unknown and live Xen resources untouched."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]


def helper():
    path = REPO / 'ansible/roles/router-alpine-rootfs/files/router-template-cleanup-dom0'
    loader = importlib.machinery.SourceFileLoader('router_template_cleanup_dom0', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    value = importlib.util.module_from_spec(spec)
    loader.exec_module(value)
    return value


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.module = helper()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.operation = 'a' * 24
        self.work = self.base / self.operation
        self.work.mkdir(mode=0o700)
        self.record = {'stage': 'detached', 'operation_id': self.operation,
                       'domains': {mode: 'router-' + mode + '-' + self.operation
                                   for mode in ('build', 'test', 'openrc')},
                       'uuids': {mode: '11111111-1111-4111-8111-111111111111'
                                 for mode in ('build', 'test', 'openrc')}}
        (self.work / 'lifecycle.json').write_text(json.dumps(self.record))
        for name in ('os.slot', 'test.slot', 'kernel.slot', 'result.slot'):
            (self.work / name).write_bytes(b'opaque')
        self.base_patch = patch.object(self.module, 'BASE', self.base)
        self.safe_patch = patch.object(self.module, 'safe_dir')
        self.file_patch = patch.object(self.module, 'safe_file', side_effect=lambda path: path.lstat())
        self.domain_patch = patch.object(self.module, 'domains', return_value={('Domain-0', None)})
        self.loop_patch = patch.object(self.module, 'attached', return_value=[])
        for patcher in (self.base_patch, self.safe_patch, self.file_patch,
                        self.domain_patch, self.loop_patch):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_exact_failed_operation_reclaims_only_its_disks(self):
        marker = self.work / 'diagnostic.log'
        marker.write_text('keep')
        other = self.base / ('b' * 24)
        other.mkdir()
        (other / 'os.slot').write_text('keep')
        result = self.module.reclaim(self.work, self.operation)
        self.assertEqual(result['bytes_reclaimed'], 6 * 4)
        self.assertEqual(result['removed'], ['os.slot', 'test.slot', 'kernel.slot', 'result.slot'])
        self.assertEqual(marker.read_text(), 'keep')
        self.assertEqual((other / 'os.slot').read_text(), 'keep')
        self.assertEqual(json.loads((self.work / 'lifecycle.json').read_text())['stage'], 'storage-reclaimed')
        self.assertEqual(self.module.reclaim(self.work, self.operation)['removed_now'], [])

    def staged(self):
        (self.work / 'lifecycle.json').unlink()
        for path in self.work.glob('*.slot'):
            path.unlink()
        (self.work / 'request.json').write_text(json.dumps({
            'kind': 'klokast.router-template-request.v1', 'box': 'k001',
            'role': 'router', 'operation_id': self.operation,
            'inputs_sha256': 'b' * 64, 'capsule': {'bytes':7, 'sha256':'c'*64},
            'bootstrap': {name:{'bytes':7,'sha256':'d'*64} for name in ('kernel','initramfs')}}))
        (self.work / 'async').mkdir()
        for name in self.module.STAGED_INPUTS:
            (self.work / name).write_bytes(b'partial')

    def test_empty_staged_cleanup_persists_complete_receipt_chain(self):
        self.staged()
        for name in self.module.STAGED_INPUTS:
            (self.work/name).unlink()
        result = self.module.reclaim(self.work,self.operation,'staged','k001')
        progress = json.loads((self.work/'cleanup-staged-progress.json').read_text())
        self.assertEqual(result['progress_sha256'],self.module.digest(progress))
        self.assertEqual(result['removed'],[])
        self.assertEqual(result['bytes_reclaimed'],0)
        self.assertEqual(self.module.reclaim(self.work,self.operation,'staged','k001'),result)

    def test_staged_cleanup_reclaims_only_exact_inactive_inputs(self):
        self.staged()
        other = self.base / ('b' * 24)
        other.mkdir()
        (other / 'capsule.tar').write_bytes(b'keep')
        result = self.module.reclaim(self.work, self.operation, 'staged', 'k001')
        self.assertEqual(result['removed'], list(self.module.STAGED_INPUTS))
        self.assertEqual(result['bytes_reclaimed'], 7 * len(self.module.STAGED_INPUTS))
        self.assertTrue((self.work / 'request.json').exists())
        self.assertEqual((other / 'capsule.tar').read_bytes(), b'keep')
        self.assertEqual(self.module.reclaim(self.work, self.operation, 'staged', 'k001')['removed_now'], [])

    def test_staged_cleanup_refuses_started_or_unknown_target(self):
        self.staged()
        with self.assertRaisesRegex(RuntimeError, 'exact target'):
            self.module.reclaim(self.work, self.operation, 'staged', 'k002')
        with patch.object(self.module, 'domains', return_value={('router-build-' + self.operation, 'uuid')}):
            with self.assertRaisesRegex(RuntimeError, 'domain still exists'):
                self.module.reclaim(self.work, self.operation, 'staged', 'k001')
        (self.work / 'lifecycle.json').write_text(json.dumps(self.record))
        with self.assertRaisesRegex(RuntimeError, 'started build'):
            self.module.reclaim(self.work, self.operation, 'staged', 'k001')
        self.assertTrue((self.work / 'capsule.tar').exists())

    def test_staged_cleanup_reclaims_only_recorded_capsule_parts(self):
        self.staged()
        request = json.loads((self.work / 'request.json').read_text())
        request['capsule'] = {'sha256': 'c' * 64, 'bytes': 3}
        request['bootstrap'] = {'kernel': {'sha256': 'e' * 64, 'bytes': 2},
                                'initramfs': {'sha256': 'f' * 64, 'bytes': 4}}
        (self.work / 'request.json').write_text(json.dumps(request))
        for name, count in (('capsule.tar',3),('bootstrap-kernel',2),('bootstrap-initramfs',4)):
            (self.work/name).write_bytes(bytes(count))
            (self.work/('.'+name+'.assembling')).write_bytes(bytes(count))
        (self.work / 'parts').mkdir(mode=0o700)
        for artifact in ('capsule', 'kernel', 'initramfs'):
            (self.work / 'parts' / artifact).mkdir(mode=0o700)
        (self.work / 'parts.json').write_text(json.dumps([
            {'artifact': 'capsule', 'name': 'part-0000', 'bytes': 3, 'sha256': 'd' * 64},
            {'artifact': 'kernel', 'name': 'part-0000', 'bytes': 2, 'sha256': 'e' * 64},
            {'artifact': 'initramfs', 'name': 'part-0000', 'bytes': 4, 'sha256': 'f' * 64}]))
        (self.work / 'parts' / 'capsule' / 'part-0000').write_bytes(b'abc')
        (self.work / 'parts' / 'kernel' / 'part-0000').write_bytes(b'kk')
        (self.work / 'parts' / 'initramfs' / 'part-0000').write_bytes(b'iiii')
        (self.work / 'parts' / 'capsule' / 'unexpected').write_bytes(b'x')
        with self.assertRaisesRegex(RuntimeError, 'differs from its request'):
            self.module.reclaim(self.work, self.operation, 'staged', 'k001')
        self.assertTrue((self.work / 'capsule.tar').exists())
        (self.work / 'parts' / 'capsule' / 'unexpected').unlink()
        result = self.module.reclaim(self.work, self.operation, 'staged', 'k001')
        self.assertIn('parts/capsule/part-0000', result['removed'])
        self.assertFalse((self.work / 'parts' / 'capsule' / 'part-0000').exists())
        self.assertTrue((self.work / 'parts.json').exists())

    def test_candidate_or_running_guest_blocks_every_unlink(self):
        (self.work / 'candidate.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'qualified'):
            self.module.reclaim(self.work, self.operation)
        (self.work / 'candidate.json').unlink()
        with patch.object(self.module, 'domains', return_value={(self.record['domains']['test'],
                    self.record['uuids']['test'])}):
            with self.assertRaisesRegex(RuntimeError, 'domain still exists'):
                self.module.reclaim(self.work, self.operation)
        self.assertTrue((self.work / 'os.slot').exists())

    def test_attached_disk_or_changed_record_blocks_every_unlink(self):
        with patch.object(self.module, 'attached', return_value=['/dev/loop7']):
            with self.assertRaisesRegex(RuntimeError, 'loop attachment'):
                self.module.reclaim(self.work, self.operation)
        self.record['stage'] = 'allocated'
        (self.work / 'lifecycle.json').write_text(json.dumps(self.record))
        with self.assertRaisesRegex(RuntimeError, 'detached operation'):
            self.module.reclaim(self.work, self.operation)
        self.assertTrue((self.work / 'os.slot').exists())

    def test_symlink_blob_is_unsafe(self):
        (self.work / 'os.slot').unlink()
        (self.work / 'os.slot').symlink_to(self.work / 'test.slot')
        self.file_patch.stop()
        with self.assertRaisesRegex(RuntimeError, 'unsafe'):
            self.module.reclaim(self.work, self.operation)
        self.assertTrue((self.work / 'test.slot').exists())

    def qualify(self):
        candidate = {'kind': 'klokast.router-template-candidate.v1',
                     'role': 'router', 'operation_id': self.operation,
                     'inputs_sha256': 'b' * 64, 'replacement_authorized': False,
                     'generic_tests': {'identity_absent': True, 'exact_packages': True,
                                       'upstream_tailscale': True, 'kernel_modules': True, 'openrc': True}}
        (self.work / 'candidate.json').write_text(json.dumps(candidate))
        for mode, tests in (('test', {'identity_absent': True, 'exact_packages': True,
                                     'upstream_tailscale': True, 'kernel_modules': True, 'service_syntax': True}),
                            ('openrc', {'kernel_modules': True, 'openrc': True,
                                        'service_syntax': True})):
            (self.work / (mode + '.json')).write_text(json.dumps({
                'kind': 'klokast.router-template-test.v1', 'success': True,
                'operation_id': self.operation, 'inputs_sha256': 'b' * 64,
                'tests': tests}))

    def test_qualified_cleanup_keeps_generic_os_and_evidence(self):
        self.qualify()
        result = self.module.reclaim(self.work, self.operation, 'scratch')
        self.assertEqual(result['bytes_reclaimed'], 6 * 3)
        self.assertEqual(result['removed'], ['test.slot', 'kernel.slot', 'result.slot'])
        self.assertTrue((self.work / 'os.slot').exists())
        self.assertTrue((self.work / 'candidate.json').exists())
        self.assertEqual(json.loads((self.work / 'lifecycle.json').read_text())['stage'], 'scratch-reclaimed')
        self.assertEqual(self.module.reclaim(self.work, self.operation, 'scratch')['removed_now'], [])
        with self.assertRaisesRegex(RuntimeError, 'detached operation'):
            self.module.reclaim(self.work, self.operation, 'failed')

    def test_scratch_cleanup_refuses_failed_boot_or_attached_os(self):
        self.qualify()
        test = self.work / 'openrc.json'
        value = json.loads(test.read_text())
        value['success'] = False
        test.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError, 'boot evidence'):
            self.module.reclaim(self.work, self.operation, 'scratch')
        value['success'] = True
        test.write_text(json.dumps(value))
        with patch.object(self.module, 'attached', side_effect=lambda path: ['/dev/loop1'] if path.name == 'os.slot' else []):
            with self.assertRaisesRegex(RuntimeError, 'remains attached'):
                self.module.reclaim(self.work, self.operation, 'scratch')
        self.assertTrue((self.work / 'test.slot').exists())


    def test_personalization_domain_and_evidence_are_required_for_new_builds(self):
        self.qualify()
        self.record['domains']['personalize'] = 'router-personalize-' + self.operation
        self.record['uuids']['personalize'] = '22222222-2222-4222-8222-222222222222'
        (self.work / 'lifecycle.json').write_text(json.dumps(self.record))
        with self.assertRaises(FileNotFoundError):
            self.module.reclaim(self.work, self.operation, 'scratch')
        result = {'kind': 'klokast.router-template-test.v1', 'success': True,
                  'operation_id': self.operation, 'inputs_sha256': 'b' * 64,
                  'tests': dict.fromkeys(('personalization', 'exact_packages', 'identity_absent', 'service_syntax'), True)}
        (self.work / 'personalize.json').write_text(json.dumps(result))
        with self.assertRaisesRegex(RuntimeError, 'different personalization'):
            self.module.reclaim(self.work, self.operation, 'scratch')
        candidate = json.loads((self.work / 'candidate.json').read_text())
        candidate['personalization_test'] = result
        (self.work / 'candidate.json').write_text(json.dumps(candidate))
        with patch.object(self.module, 'domains', return_value={
                (self.record['domains']['personalize'], self.record['uuids']['personalize'])}):
            with self.assertRaisesRegex(RuntimeError, 'domain still exists'):
                self.module.reclaim(self.work, self.operation, 'scratch')
        self.assertTrue((self.work / 'test.slot').exists())
        self.assertEqual(self.module.reclaim(self.work, self.operation, 'scratch')['bytes_reclaimed'], 18)


    def test_lost_unlink_reply_preserves_intent_and_stable_completion(self):
        unlink = Path.unlink
        def lost(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            if path == self.work/'os.slot':
                raise OSError('unlink reply lost')
        with patch.object(Path, 'unlink', lost), self.assertRaisesRegex(OSError, 'reply lost'):
            self.module.reclaim(self.work, self.operation)
        progress = json.loads((self.work/'cleanup-failed-progress.json').read_text())
        self.assertEqual(progress['inflight'], 'os.slot')
        self.assertFalse((self.work/'cleanup-failed-complete.json').exists())
        result = self.module.reclaim(self.work, self.operation)
        self.assertEqual(result['bytes_reclaimed'], 24)
        self.assertEqual(result['removed_now'], ['test.slot','kernel.slot','result.slot'])
        again = self.module.reclaim(self.work, self.operation)
        self.assertEqual(again['removed_now'], [])
        self.assertEqual({k:v for k,v in result.items() if k != 'removed_now'},
                         {k:v for k,v in again.items() if k != 'removed_now'})

    def test_cached_plan_rejects_changed_inode_missing_without_intent_and_escape(self):
        write = self.module.write
        def stop(path, value):
            write(path, value)
            if path.name == 'cleanup-failed-plan.json':
                raise OSError('plan reply lost')
        with patch.object(self.module, 'write', side_effect=stop), self.assertRaises(OSError):
            self.module.reclaim(self.work, self.operation)
        disk = self.work/'os.slot'; kept = self.work/'kept'
        disk.replace(kept); disk.write_bytes(b'opaque')
        with self.assertRaisesRegex(RuntimeError, 'changed before retirement'):
            self.module.reclaim(self.work, self.operation)
        disk.unlink()
        with self.assertRaisesRegex(RuntimeError, 'without removal intent'):
            self.module.reclaim(self.work, self.operation)
        kept.replace(disk)
        plan_path = self.work/'cleanup-failed-plan.json'
        plan = json.loads(plan_path.read_text()); plan['files'][0]['name'] = '../protected'
        write(plan_path, plan)
        with self.assertRaisesRegex(RuntimeError, 'invalid selected identity'):
            self.module.reclaim(self.work, self.operation)
        self.assertTrue(disk.exists())

    def test_renamed_recorded_uuid_and_fresh_guest_appearance_block_removal(self):
        with patch.object(self.module, 'domains', return_value={('renamed',self.record['uuids']['build'])}), \
                self.assertRaisesRegex(RuntimeError, 'domain still exists'):
            self.module.reclaim(self.work, self.operation)
        self.assertTrue((self.work/'os.slot').exists())
        absent = {('Domain-0',None)}
        appeared = {('renamed',self.record['uuids']['build'])}
        with patch.object(self.module, 'domains', side_effect=[absent,absent,appeared]), \
                self.assertRaisesRegex(RuntimeError, 'domain still exists'):
            self.module.reclaim(self.work, self.operation)
        self.assertTrue((self.work/'os.slot').exists())
        self.assertFalse((self.work/'cleanup-failed-complete.json').exists())

    def test_reappearance_unknown_disk_and_changed_authority_refuse(self):
        self.module.reclaim(self.work, self.operation)
        (self.work/'test.slot').write_bytes(b'opaque')
        with self.assertRaisesRegex(RuntimeError, 'reappeared after retirement'):
            self.module.reclaim(self.work, self.operation)
        (self.work/'test.slot').unlink()
        (self.work/'unknown.slot').write_bytes(b'opaque')
        with self.assertRaisesRegex(RuntimeError, 'unknown disk'):
            self.module.reclaim(self.work, self.operation)
        (self.work/'unknown.slot').unlink()
        self.record['stage'] = 'storage-reclaimed'
        self.record['uuids']['build'] = '22222222-2222-4222-8222-222222222222'
        (self.work/'lifecycle.json').write_text(json.dumps(self.record))
        with self.assertRaisesRegex(RuntimeError, 'plan changed'):
            self.module.reclaim(self.work, self.operation)

    def test_lost_lifecycle_write_does_not_repeat_file_removal(self):
        write = self.module.write
        def lost(path, value):
            if path.name == 'lifecycle.json':
                raise OSError('lifecycle update lost')
            return write(path,value)
        (self.work/'lifecycle.cleanup').write_text('older temporary')
        with patch.object(self.module, 'write', side_effect=lost), self.assertRaises(OSError):
            self.module.reclaim(self.work,self.operation)
        self.assertTrue((self.work/'cleanup-failed-complete.json').exists())
        result = self.module.reclaim(self.work,self.operation)
        self.assertEqual(result['removed_now'], [])
        self.assertEqual(result['bytes_reclaimed'], 24)
        self.assertEqual((self.work/'lifecycle.cleanup').read_text(),'older temporary')

    def test_scratch_collects_raw_inputs_but_keeps_template_boot_and_os(self):
        self.staged()
        (self.work/'lifecycle.json').write_text(json.dumps(self.record))
        (self.work/'os.slot').write_bytes(b'opaque')
        (self.work/'test.slot').write_bytes(b'opaque')
        self.qualify()
        for name in ('kernel','initramfs'):
            (self.work/name).write_bytes(b'keep')
        result = self.module.reclaim(self.work,self.operation,'scratch')
        self.assertEqual(result['bytes_reclaimed'],48)
        self.assertTrue((self.work/'os.slot').exists())
        for name in ('kernel','initramfs'):
            self.assertEqual((self.work/name).read_bytes(),b'keep')
        self.assertFalse((self.work/'capsule.tar').exists())
        self.assertEqual(self.module.reclaim(self.work,self.operation,'scratch')['removed_now'],[])

    def test_failed_collects_partial_boot_outputs_and_bounds_inputs(self):
        for name in ('kernel','initramfs'):
            (self.work/name).write_bytes(b'partial')
        result = self.module.reclaim(self.work,self.operation)
        self.assertEqual(result['bytes_reclaimed'],38)
        self.assertFalse((self.work/'kernel').exists())
        self.staged()
        (self.work/'capsule.tar').write_bytes(b'too many bytes')
        with self.assertRaisesRegex(RuntimeError,'file changed'):
            self.module.reclaim(self.work,self.operation,'staged','k001')
        self.assertTrue((self.work/'bootstrap-kernel').exists())

    def test_deleted_loop_backing_and_live_boot_inventory_are_detected(self):
        self.loop_patch.stop()
        backing = self.work/'backing_file'; backing.write_text(str(self.work/'os.slot')+' (deleted)\n')
        glob = Path.glob
        def listing(path, pattern):
            return [backing] if str(path) == '/sys/block' else glob(path,pattern)
        with patch.object(Path,'glob',listing):
            self.assertTrue(self.module.attached(self.work/'os.slot'))
        self.domain_patch.stop()
        class Result:
            returncode = 0
            stdout = json.dumps([
                {'domid':0,'config':{'c_info':{'name':'Domain-0'}}},
                {'domid':3,'config':{'c_info':{'name':'another','uuid':self.record['uuids']['build']},
                                    'b_info':{'kernel':str(self.work/'kernel')}}}])
        with patch.object(self.module.subprocess,'run',return_value=Result()), \
                self.assertRaisesRegex(RuntimeError,'boot file belongs'):
            self.module.domains({str(self.work/'kernel')})


    def test_timeout_keeps_resources_and_legacy_reappearance_refuses(self):
        with patch.object(self.module.time,'monotonic',side_effect=[0,901]), \
                self.assertRaisesRegex(RuntimeError,'time limit reached'):
            self.module.reclaim(self.work,self.operation)
        self.assertTrue((self.work/'os.slot').exists())
        self.assertFalse((self.work/'cleanup-failed-plan.json').exists())
        self.record['stage'] = 'storage-reclaimed'
        (self.work/'lifecycle.json').write_text(json.dumps(self.record))
        with self.assertRaisesRegex(RuntimeError,'reappeared after recorded cleanup'):
            self.module.reclaim(self.work,self.operation)

    def test_legacy_scratch_can_collect_inputs_not_selected_by_old_cleanup(self):
        self.staged(); self.qualify()
        self.record['stage'] = 'scratch-reclaimed'
        (self.work/'lifecycle.json').write_text(json.dumps(self.record))
        (self.work/'os.slot').write_bytes(b'opaque')
        result = self.module.reclaim(self.work,self.operation,'scratch')
        self.assertEqual(result['bytes_reclaimed'],42)
        self.assertTrue((self.work/'os.slot').exists())
        self.assertFalse((self.work/'bootstrap-kernel').exists())


if __name__ == '__main__':
    unittest.main()
