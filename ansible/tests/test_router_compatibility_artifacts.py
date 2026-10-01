"""Boot copy/retirement interruptions must preserve exact ownership and source."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

import test_router_compatibility as compatibility_tests
from test_router_copy_qualification import module


class CompatibilityArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.case=compatibility_tests.PartialCleanupTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.artifacts=self.case.host.boot_artifacts
        self.work=self.case.work
        self.value=self.case.value
        self.source=self.work.parent/'original-kernel'; self.source.write_bytes(b'old')
        self.expected={'bytes':3,'sha256':hashlib.sha256(b'old').hexdigest()}
        self.value['source']['boot_artifacts']['kernel']['path']=str(self.source)

    def stage(self):
        self.artifacts.stage(self.work,'old-kernel',self.source,self.expected)

    def test_real_boot_copy_and_complete_receipt_preserve_originals(self):
        self.stage()
        self.assertEqual((self.work/'old-kernel').read_bytes(),self.source.read_bytes())
        self.assertEqual(self.case.cleanup(),9)
        complete=json.loads((self.work/'cleanup-complete.v2.json').read_text())
        artifact=json.loads((self.work/'artifact-cleanup-complete.json').read_text())
        self.assertEqual(complete['artifact_cleanup_sha256'],self.artifacts.generations.digest(artifact))
        self.assertEqual(complete['status'],'unused-resources-retired')
        self.assertEqual(self.case.cleanup(),9)
        self.assertEqual(self.source.read_bytes(),b'old')
        self.assertTrue((self.case.templates/('c'*24)/'candidate.json').exists())
        self.assertFalse((self.work/'old-kernel').exists())

    def test_interrupted_boot_copy_keeps_inode_and_can_retire_partial_bytes(self):
        original_open=Path.open; incoming_opens=0
        class InterruptedInput:
            def __init__(self,stream):
                self.stream=stream; self.called=False
            def __enter__(self):
                return self
            def __exit__(self,*args):
                self.stream.close()
            def read(self,count):
                if self.called:
                    raise OSError('copy read failed')
                self.called=True
                return self.stream.read(1)
        def opening(path,*args,**kwargs):
            nonlocal incoming_opens
            stream=original_open(path,*args,**kwargs)
            if path==self.source and args==('rb',):
                incoming_opens+=1
                if incoming_opens==2:
                    return InterruptedInput(stream)
            return stream
        with mock.patch.object(Path,'open',opening),self.assertRaisesRegex(OSError,'read failed'):
            self.stage()
        intent=json.loads((self.work/'boot-copy-old-kernel.json').read_text())
        self.assertEqual(intent['stage'],'copying')
        self.assertEqual((self.work/'old-kernel').read_bytes(),b'o')
        self.assertEqual(intent['identity']['inode'],(self.work/'old-kernel').stat().st_ino)
        self.assertEqual(self.case.cleanup(),7)

    def test_lost_completed_copy_record_can_be_reconciled_from_copying_intent(self):
        original=self.artifacts.write
        def lost(path,value):
            if path.name=='boot-copy-old-kernel.json' and value['stage']=='complete':
                raise OSError('copy completion lost')
            return original(path,value)
        with mock.patch.object(self.artifacts,'write',side_effect=lost),self.assertRaisesRegex(OSError,'completion lost'):
            self.stage()
        self.assertEqual((self.work/'old-kernel').read_bytes(),b'old')
        self.assertEqual(self.case.cleanup(),9)

    def test_lost_artifact_unlink_retries_after_raw_boot_is_absent(self):
        unlink=Path.unlink
        def lost(path,*args,**kwargs):
            unlink(path,*args,**kwargs)
            if path==self.work/'kernel':
                raise OSError('boot unlink reply lost')
        with mock.patch.object(Path,'unlink',lost),self.assertRaisesRegex(OSError,'reply lost'):
            self.case.cleanup()
        self.assertEqual(json.loads((self.work/'artifact-cleanup-progress.json').read_text())['inflight'],'kernel')
        self.assertFalse((self.work/'cleanup-complete.v2.json').exists())
        self.assertEqual(self.case.cleanup(),6)
        self.assertEqual(self.case.cleanup(),6)

    def test_changed_inode_and_cached_plan_escape_preserve_boot_files(self):
        original=self.artifacts.write
        def lost(path,value):
            original(path,value)
            if path.name=='artifact-cleanup-plan.json':
                raise OSError('artifact plan reply lost')
        with mock.patch.object(self.artifacts,'write',side_effect=lost),self.assertRaises(OSError):
            self.case.cleanup()
        target=self.work/'kernel'; original_kernel=self.work/'original-kernel'
        target.replace(original_kernel); target.write_bytes(b'ker')
        with self.assertRaisesRegex(RuntimeError,'changed before retirement'):
            self.case.cleanup()
        target.unlink(); original_kernel.replace(target)
        plan=json.loads((self.work/'artifact-cleanup-plan.json').read_text())
        plan['files'][0]['name']='../../protected-kernel'
        self.artifacts.write(self.work/'artifact-cleanup-plan.json',plan)
        with self.assertRaisesRegex(RuntimeError,'invalid selected identity'):
            self.case.cleanup()
        self.assertTrue(target.exists())

    def test_live_boot_use_and_deleted_loop_block_complete_receipt(self):
        self.case.native.inventory.return_value=[{'domid':8,'config':{
            'c_info':{'uuid':'22222222-2222-4222-8222-222222222222'},
            'b_info':{'kernel':str(self.work/'kernel')}}}]
        with self.assertRaisesRegex(RuntimeError,'live guest'):
            self.case.cleanup()
        self.assertFalse((self.work/'cleanup-complete.v2.json').exists())
        self.case.native.inventory.return_value=[]
        self.case.cleanup()
        with mock.patch.object(self.artifacts,'loop_devices',return_value=['/dev/loop7']), \
                self.assertRaisesRegex(RuntimeError,'loop mapping'):
            self.case.cleanup()
        (self.work/'new-kernel').write_bytes(b'reappeared')
        with self.assertRaisesRegex(RuntimeError,'outside its cleanup plan'):
            self.case.cleanup()

    def test_request_parser_allows_only_metadata_read_after_retirement(self):
        self.value['guest']={'kind':'klokast.router-compatibility-request.v2',
            'operation_id':self.value['operation_id'],'inputs_sha256':self.value['inputs_sha256'],
            'box':self.value['box'],'source_packages':{},'source_files':{},'source_kernel_release':'6.1-virt',
            'fixture':{},'manifest':{'inputs_sha256':self.value['inputs_sha256']},
            'runtime_packages':{},'kernel_release':'6.1-virt'}
        self.value['kind']='klokast.router-compatibility-host.v2'; self.value['role']='router'
        self.case.host.write(self.work/'request.json',self.value)
        host=module('router-compatibility-dom0')
        with mock.patch.object(host,'safe_file'):
            self.assertEqual(host.request(self.work,'boxa',self.value['operation_id']),self.value)
            self.case.cleanup()
            self.assertEqual(host.request(self.work,'boxa',self.value['operation_id'],verify_boot=False),self.value)
            with self.assertRaises(FileNotFoundError):
                host.request(self.work,'boxa',self.value['operation_id'])

    def test_historical_cleaned_disks_require_both_retired_allocation_records(self):
        disk=self.case.allocate(); disk.unlink()
        self.case.host.write(self.work/'lifecycle.json',{**self.case.record,'stage':'cleaned'})
        snapshot={'operation_id':self.case.operation,'stage':'retired',
            'path':'/dev/vg0/routercompat_'+self.case.operation,'tag':'routercompat_'+self.case.operation,
            'origin_uuid':'source','uuid':'snapshot'}
        self.case.host.write(self.work/'snapshot.json',snapshot)
        allocation={'stage':'retired','uuid':'candidate'}
        (self.work/'candidate-disk.json').write_text(json.dumps(allocation))
        with mock.patch.object(self.case.host.router_candidate_disk,'record',return_value=allocation), \
                mock.patch.object(self.case.host.router_candidate_disk,'retire',return_value=0):
            with mock.patch.object(self.case.host.router_candidate_disk,'record',return_value={'stage':'planned'}), \
                    self.assertRaisesRegex(RuntimeError,'both exact retired'):
                self.case.cleanup()
            self.assertTrue((self.work/'kernel').exists())
            disk.write_bytes(b'reappeared')
            with self.assertRaisesRegex(RuntimeError,'reappeared'):
                self.case.cleanup()
            disk.unlink()
            self.assertEqual(self.case.cleanup(),6)
            self.assertFalse((self.work/'kernel').exists())


if __name__=='__main__':
    unittest.main()
