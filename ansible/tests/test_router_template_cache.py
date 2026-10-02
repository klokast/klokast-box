"""Controller cache retirement uses real file identities and durable intents."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import router_template_cache as cache
import router_update_controller as transport
from platform_updates import UpdateError,digest


class TemplateCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.cache,self.state=root/'cache',root/'state'
        self.operation='a'*24;self.box='boxa'
        self.work,self.result=self.cache/('build-'+self.operation),self.state/self.operation
        for p in (self.cache,self.state,self.work,self.result,self.work/'boot',self.work/'transfer',
                  self.work/'transfer/capsule',self.work/'transfer/kernel',self.work/'transfer/initramfs'):
            p.mkdir(mode=0o700)
        artifacts={};parts=[]
        for name in ('capsule','kernel','initramfs'):
            data=(name+'-payload').encode()
            artifacts[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            path=self.work/('capsule.tar' if name=='capsule' else 'boot/'+name)
            path.write_bytes(data)
            (self.work/'transfer'/name/'part-0000').write_bytes(data)
            parts.append({'artifact':name,'name':'part-0000',**artifacts[name]})
        self.request={'kind':'klokast.router-template-request.v1','box':self.box,'role':'router',
            'operation_id':self.operation,'inputs_sha256':'b'*64,'capsule':artifacts['capsule'],
            'bootstrap':{k:artifacts[k] for k in ('kernel','initramfs')}}
        transport.write(self.work/'request.json',self.request)
        transport.write(self.work/'personalization.json',{'keep':'metadata'})
        transport.write(self.result/'arguments.json',{'router_template_box':self.box,
            'router_template_operation':self.operation,'router_template_input_dir':str(self.work),
            'router_template_result_dir':str(self.result),'router_template_transfer_parts':parts})
        plan={'kind':'klokast.router-template-cleanup-plan.v1','operation_id':self.operation,
              'cleanup_kind':'obsolete','files':[{'name':name,'identity':{'device':1,'inode':index+1,'bytes':1}}
                    for index,name in enumerate(('os.slot','kernel','initramfs'))]}
        progress={'kind':'klokast.router-template-cleanup-progress.v1','plan_sha256':digest(plan),
                  'removed':['os.slot','kernel','initramfs'],'inflight':None}
        transport.write(self.result/'cleanup-obsolete-plan.json',plan)
        transport.write(self.result/'cleanup-obsolete-progress.json',progress)
        transport.write(self.result/'cleanup-obsolete-complete.json',{
            'kind':'klokast.router-template-cleanup.v2','operation_id':self.operation,'cleanup_kind':'obsolete',
            'status':'selected-files-retired','removed':progress['removed'],
            'bytes_reclaimed':3,
            'plan_sha256':digest(plan),'progress_sha256':digest(progress)})
        self.reference={'kind':'klokast.router-template-reference-check.v1','box':self.box,
            'operation_id':self.operation,'status':'unused-template-reference-verified','removed_now':[],
            'references_sha256':'f'*64,'retained_templates':0}

    def run_cleanup(self):
        return cache.retire(self.cache,self.state,self.box,self.operation,self.reference)

    def test_success_keeps_metadata_and_retry_does_not_repeat_removal(self):
        result=self.run_cleanup()
        self.assertEqual(len(result['removed_now']),6)
        self.assertTrue((self.work/'request.json').is_file())
        self.assertTrue((self.work/'personalization.json').is_file())
        self.assertEqual(self.run_cleanup()['removed_now'],[])
        self.assertEqual(transport.load(self.result/'cache-cleanup-complete.json')['removed'],result['removed'])

    def test_lost_reply_after_unlink_resumes_recorded_intent(self):
        real=cache.sync;lost=True
        def interrupt(path):
            nonlocal lost
            if lost:
                lost=False
                raise KeyboardInterrupt
            real(path)
        with mock.patch.object(cache,'sync',side_effect=interrupt),self.assertRaises(KeyboardInterrupt):
            self.run_cleanup()
        self.assertFalse((self.work/'capsule.tar').exists())
        self.assertEqual(transport.load(self.result/'cache-cleanup-progress.json')['inflight'],'capsule.tar')
        self.assertEqual(len(self.run_cleanup()['removed_now']),5)

    def test_changed_bytes_links_unknown_resources_and_retained_reference_refuse(self):
        path=self.work/'capsule.tar';data=path.read_bytes();path.write_bytes(b'x'*len(data))
        with self.assertRaisesRegex(UpdateError,'bytes changed'):self.run_cleanup()
        path.write_bytes(data)
        path.rename(self.work/'saved');path.symlink_to(self.work/'saved')
        with self.assertRaises(UpdateError):self.run_cleanup()
        path.unlink();(self.work/'saved').rename(path)
        unknown=self.work/'transfer/kernel/unknown';unknown.write_bytes(b'keep')
        with self.assertRaisesRegex(UpdateError,'unknown transfer'):self.run_cleanup()
        unknown.unlink()
        self.reference['operation_id']='c'*24
        with self.assertRaisesRegex(UpdateError,'unused-reference'):self.run_cleanup()
        self.assertTrue(path.exists())

    def test_reappeared_retired_file_and_missing_without_intent_refuse(self):
        self.run_cleanup()
        (self.work/'boot/kernel').write_bytes(b'reappeared')
        with self.assertRaisesRegex(UpdateError,'reappeared'):self.run_cleanup()

    def test_source_change_during_cleanup_preserves_remaining_files(self):
        real=cache.sync
        def change(path):
            real(path)
            transport.write(self.work/'request.json',{**self.request,'inputs_sha256':'f'*64})
        with mock.patch.object(cache,'sync',side_effect=change),self.assertRaisesRegex(UpdateError,'source changed'):
            self.run_cleanup()
        self.assertTrue((self.work/'boot/kernel').exists())

    def test_missing_without_removal_intent_retains_the_plan(self):
        real=cache.sync
        def interrupt(path):
            real(path)
            raise KeyboardInterrupt
        with mock.patch.object(cache,'sync',side_effect=interrupt),self.assertRaises(KeyboardInterrupt):
            self.run_cleanup()
        (self.work/'boot/kernel').unlink()
        with self.assertRaisesRegex(UpdateError,'without removal intent'):self.run_cleanup()
        self.assertFalse((self.result/'cache-cleanup-complete.json').exists())


if __name__=='__main__':unittest.main()
