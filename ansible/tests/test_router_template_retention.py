"""Obsolete template collection must preserve protected and staged consumers."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import router_template_retention as retention
import router_generations as generations
import router_records as records
from router_transaction import TransactionError
import test_router_records as record_tests
import test_router_template_cleanup as cleanup_tests


class TemplateRetentionTests(unittest.TestCase):
    def setUp(self):
        self.case = record_tests.RecordsTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.storage = self.case.records
        self.compatibility = self.case.base/'compatibility'; self.compatibility.mkdir(mode=0o700)
        for target,name,value in ((retention,'COMPATIBILITY',self.compatibility),(records,'BASE',self.case.base)):
            context = mock.patch.object(target,name,value); context.start(); self.addCleanup(context.stop)

    def request(self,operation='d'*24,template='c'*24,mode='replacement'):
        work = self.storage.base/'operations'/operation; work.mkdir(mode=0o700,exist_ok=True)
        request = {'kind':'klokast.router-'+('initial' if mode == 'initial-install' else 'replacement')+'-preparation.v1',
            'box':'boxa','operation_id':operation,'mode':mode,'engine_commit':'9'*40,
            'template_operation':template,'template_sha256':'a'*64,'release_sha256':'b'*64}
        records.write(work/'request.json',request)
        return work,request

    def accepted(self,current,previous=None):
        value = self.storage.accepted(); value.pop('record_sha256')
        value.update(current_sha256=current['record_sha256'],
                     previous_sha256=previous['record_sha256'] if previous else None)
        for generation in (current,previous):
            if generation:
                records.write(self.storage.base/'records'/(generation['record_sha256']+'.json'),generation)
        records.write(self.storage.base/'accepted.json',generations.seal(value))

    def test_current_and_previous_templates_are_protected_by_validated_generations(self):
        previous = copy.deepcopy(self.case.new); previous.pop('record_sha256')
        previous.update(generation_id='e'*24,template_operation='f'*24)
        previous['disk']['path'] = '/dev/vg0/routergen_'+previous['generation_id']
        for name in ('kernel','initramfs'):
            previous['boot'][name]['path'] = '/mnt/dom0_data/klokast-router-updates/generations/'+previous['generation_id']+'/'+name
        previous = generations.seal(previous)
        self.accepted(self.case.new,previous)
        with self.storage.lock():
            value = retention.references(self.storage)
        self.assertEqual(value['templates'],['c'*24,'f'*24])
        for operation in value['templates']:
            with self.assertRaisesRegex(TransactionError,'retained by current'):
                with retention.guard('boxa',operation):
                    self.fail('retained template cannot be selected')

    def test_pending_and_unfinished_preparation_retain_their_templates(self):
        self.storage.persist(self.case.pending)
        self.request(template='e'*24)
        with self.storage.lock():
            value = retention.references(self.storage)
        self.assertEqual(value['templates'],['c'*24,'e'*24])

    def test_planned_first_install_and_missing_reference_refuse(self):
        work,request = self.request(mode='initial-install')
        installation = generations.seal({'kind':'klokast.router-initial-installation.v1','box':'boxa','role':'router',
            'operation_id':work.name,'engine_commit':request['engine_commit'],'selection_sha256':'0'*64,
            'release_sha256':request['release_sha256'],
            'disk':{'path':'/dev/vg0/routergen_'+work.name,'uuid':None,'bytes':2147483648},
            'stage':'planned','preparation_sha256':None,'enrollment_sha256':None,'machine_id':None,'generation_sha256':None})
        (self.storage.base/'accepted.json').unlink()
        self.storage.record_installation(installation)
        with self.storage.lock():
            self.assertEqual(retention.references(self.storage)['templates'],['c'*24])
        (work/'request.json').unlink()
        with self.assertRaises(FileNotFoundError):
            with retention.guard('boxa','a'*24):
                self.fail('missing installation reference cannot authorize collection')

    def test_malformed_preparation_and_unattributed_resources_refuse(self):
        work,request = self.request(); request['template_operation'] = '../escape'
        records.write(work/'request.json',request)
        with self.assertRaisesRegex(TransactionError,'reference is invalid'):
            retention.references(self.storage)
        (work/'request.json').unlink(); records.write(work/'candidate-disk.json',{'stage':'planned'})
        with self.assertRaisesRegex(TransactionError,'lack their template reference'):
            retention.references(self.storage)

    def test_only_complete_bound_preparation_cleanup_releases_reference(self):
        work,request = self.request()
        plan = generations.seal({'kind':'klokast.router-preparation-cleanup-plan.v1',
            'box':'boxa','operation_id':work.name,'engine_commit':request['engine_commit'],
            'request_sha256':generations.digest(request),'assignment_sha256':'e'*64,'files':[]})
        progress = generations.seal({'kind':'klokast.router-preparation-cleanup-progress.v1',
            'plan_sha256':plan['record_sha256'],'phase':'complete','removed':[],'inflight':None})
        result = generations.seal({'kind':'klokast.router-preparation-cleanup-complete.v1',
            'box':'boxa','operation_id':work.name,'engine_commit':request['engine_commit'],
            'plan_sha256':plan['record_sha256'],'assignment_sha256':plan['assignment_sha256'],
            'progress_sha256':progress['record_sha256'],'status':'unused-resources-retired'})
        for name,value in (('plan',plan),('progress',progress),('complete',result)):
            records.write(work/('preparation-cleanup-'+name+'.json'),value)
        self.assertEqual(retention.references(self.storage)['templates'],[])
        progress.pop('record_sha256'); progress['phase'] = 'files-retiring'
        records.write(work/'preparation-cleanup-progress.json',generations.seal(progress))
        with self.assertRaisesRegex(TransactionError,'cleanup proof changed'):
            retention.references(self.storage)

    def test_compatibility_reservation_and_changed_receipt_are_protected(self):
        work = self.compatibility/('d'*24); work.mkdir(mode=0o700)
        request = {'kind':'klokast.router-compatibility-host.v2','box':'boxa','operation_id':work.name,
            'engine_commit':'9'*40,'template':{'operation':'e'*24,'sha256':'a'*64}}
        records.write(work/'request.json',request)
        self.assertEqual(retention.references(self.storage)['templates'],['e'*24])
        fixed = {key:request[key] for key in ('box','operation_id','engine_commit')}
        disk = {**fixed,'kind':'klokast.router-compatibility-cleanup.v1','status':'unused-disks-retired'}
        artifact = {**fixed,'kind':'klokast.router-compatibility-artifact-cleanup.v1','status':'boot-files-retired'}
        complete = {'kind':'klokast.router-compatibility-cleanup.v2','box':'boxa','operation_id':work.name,
            'engine_commit':request['engine_commit'],'status':'unused-resources-retired',
            'disk_cleanup_sha256':generations.digest(disk),'artifact_cleanup_sha256':generations.digest(artifact)}
        for name,value in (('cleanup-complete.json',disk),('artifact-cleanup-complete.json',artifact),
                           ('cleanup-complete.v2.json',complete)):
            records.write(work/name,value)
        self.assertEqual(retention.references(self.storage)['templates'],[])
        disk['status'] = 'incomplete'; records.write(work/'cleanup-complete.json',disk)
        with self.assertRaisesRegex(TransactionError,'cleanup proof changed'):
            retention.references(self.storage)

    def test_reference_change_is_detected_before_collection_and_locks_exclude_workers(self):
        with retention.guard('boxa','a'*24) as (value,fresh):
            self.assertEqual(value['templates'],[])
            with self.assertRaises(BlockingIOError):
                with retention.guard('boxa','b'*24):
                    self.fail('diagnostic lock must exclude another collector')
            self.request(template='a'*24)
            with self.assertRaisesRegex(TransactionError,'references changed'):
                fresh()

    def template(self):
        case = cleanup_tests.CleanupTests(); case.setUp(); self.addCleanup(case.doCleanups)
        case.qualify()
        candidate = json.loads((case.work/'candidate.json').read_text()); candidate['box'] = 'boxa'
        for name in ('kernel','initramfs'):
            (case.work/name).write_bytes(b'boot')
        candidate['artifacts'] = {name:{'bytes':(case.work/path).stat().st_size,
            'sha256':hashlib.sha256((case.work/path).read_bytes()).hexdigest()}
            for name,path in (('os','os.slot'),('kernel','kernel'),('initramfs','initramfs'))}
        (case.work/'candidate.json').write_text(json.dumps(candidate))
        return case

    def retire(self,case):
        with retention.guard('boxa',case.operation) as (value,fresh):
            return case.module.reclaim(case.work,case.operation,'obsolete','boxa',references=value,authorize=fresh)

    def test_obsolete_collection_retries_lost_unlink_and_keeps_metadata_and_other_templates(self):
        case = self.template()
        other = case.base/('b'*24); other.mkdir(); (other/'os.slot').write_bytes(b'keep')
        unlink = Path.unlink
        def lost(path,*args,**kwargs):
            unlink(path,*args,**kwargs)
            if path == case.work/'os.slot':
                raise OSError('obsolete unlink reply lost')
        with mock.patch.object(Path,'unlink',lost),self.assertRaisesRegex(OSError,'reply lost'):
            self.retire(case)
        self.assertFalse((case.work/'cleanup-obsolete-complete.json').exists())
        self.assertEqual(self.retire(case)['bytes_reclaimed'],14)
        self.assertEqual(self.retire(case)['removed_now'],[])
        self.assertEqual((other/'os.slot').read_bytes(),b'keep')
        self.assertTrue((case.work/'candidate.json').exists())
        self.assertTrue((case.work/'cleanup-scratch-complete.json').exists())
        (case.work/'os.slot').write_bytes(b'opaque')
        with self.assertRaisesRegex(RuntimeError,'reappeared after retirement'):
            self.retire(case)

    def test_obsolete_rejects_retained_request_and_changed_artifact_before_unlink(self):
        case = self.template(); self.request(template=case.operation)
        with self.assertRaisesRegex(TransactionError,'retained by current'):
            self.retire(case)
        self.assertTrue((case.work/'test.slot').exists())
        (self.storage.base/'operations'/('d'*24)/'request.json').unlink()
        (case.work/'kernel').write_bytes(b'bad!')
        with self.assertRaisesRegex(RuntimeError,'qualified artifacts'):
            self.retire(case)
        self.assertTrue((case.work/'os.slot').exists())
        self.assertTrue((case.work/'initramfs').exists())


    def test_verified_initial_reference_does_not_pin_an_older_template_forever(self):
        accepted = self.storage.accepted()
        (self.storage.base/'accepted.json').unlink()
        installation = self.case.record_verified_installation()
        records.write(self.storage.base/'accepted.json',accepted)
        work,request = self.request(operation=installation['operation_id'],template=self.case.new['template_operation'],mode='initial-install')
        request['engine_commit'] = installation['engine_commit']; records.write(work/'request.json',request)
        self.assertEqual(retention.references(self.storage)['templates'],[])
        self.accepted(self.case.new)
        self.assertEqual(retention.references(self.storage)['templates'],['c'*24])

    def test_cold_fence_and_reserved_initial_template_are_protected(self):
        marker = generations.seal({'kind':'klokast.router-cold-test.v1','box':'boxa',
            'operation_id':'e'*24,'engine_commit':'9'*40,'metadata_sha256':'a'*64,
            'generation_sha256':self.case.old['record_sha256'],'initial_operation':'f'*24,
            'armed_at':1,'expires_at':2,'phase':'armed'})
        records.write(self.storage.base/'cold-test.json',marker)
        with self.assertRaisesRegex(TransactionError,'supervised cold test'):
            with retention.guard('boxa','a'*24):
                self.fail('cold fence prevents template retirement')
        (self.storage.base/'cold-test.json').unlink()
        directory = self.storage.base/'cold-backups'; directory.mkdir(mode=0o700)
        work = directory/('e'*24); work.mkdir(mode=0o700)
        pointer = generations.seal({'kind':'klokast.router-initial-provision-pointer.v1','box':'boxa',
            'engine_commit':'9'*40,'operation_id':'f'*24,'source_operation':'d'*24,
            'template_operation':'c'*24,'selection_sha256':'a'*64,'release_sha256':'b'*64})
        value = generations.seal({'kind':'klokast.router-cold-supervised-request.v1','box':'boxa',
            'operation_id':work.name,'engine_commit':'9'*40,'initial_provision':pointer})
        records.write(work/'supervised-request.json',value)
        self.assertEqual(retention.references(self.storage)['templates'],['c'*24])
        intent = generations.seal({'kind':'klokast.router-cold-prepared-abort-intent.v1','box':'boxa',
            'operation_id':work.name,'source_engine_commit':'9'*40})
        complete = generations.seal({'kind':'klokast.router-cold-prepared-abort.v1','box':'boxa',
            'operation_id':work.name,'source_engine_commit':'9'*40,'intent_sha256':intent['record_sha256'],
            'status':'retired'})
        records.write(work/'prepared-abort-intent.json',intent)
        records.write(work/'prepared-abort-completion.json',complete)
        self.assertEqual(retention.references(self.storage)['templates'],[])

    def test_cutover_cleanup_requires_complete_phase_and_exact_copy_retirement(self):
        work,request = self.request(operation=self.case.request['operation_id'])
        transaction_request = self.case.request
        records.write(work/'transaction-request.json',transaction_request)
        complete = {**self.case.pending,'phase':'accepted','candidate_started':True}
        for name in ('complete.json','latest.json'):
            records.write(work/name,complete)
        plan = generations.seal({'kind':'klokast.router-cleanup-plan.v1','box':'boxa',
            'operation_id':work.name,'engine_commit':request['engine_commit'],'assignment_sha256':'e'*64})
        copy_directory = work/'copy'; copy_directory.mkdir(mode=0o700)
        copy = generations.seal({'kind':'klokast.router-copy-retirement.v1',
            'request_sha256':generations.digest(transaction_request),'phase':'retired',
            'inflight':None,'files':{},'removed':[]})
        progress = {'kind':'klokast.router-cleanup-progress.v1','plan_sha256':plan['record_sha256'],'phase':'complete'}
        result = generations.seal({'kind':'klokast.router-cleanup-complete.v2','box':'boxa',
            'operation_id':work.name,'engine_commit':request['engine_commit'],
            'plan_sha256':plan['record_sha256'],'assignment_sha256':plan['assignment_sha256'],
            'copy_retirement_sha256':copy['record_sha256'],'status':'exact-resources-retired'})
        for name,value in (('cleanup-plan.json',plan),('cleanup-progress.json',progress),
                           ('cleanup-complete.json',result),('copy/retirement.json',copy)):
            records.write(work/name,value)
        # Both exact requests must name the same engine before any reference is released.
        self.assertNotEqual(request['engine_commit'],transaction_request['engine_commit'])
        with self.assertRaisesRegex(TransactionError,'cleanup proof changed'):
            retention.references(self.storage)
        request['engine_commit'] = transaction_request['engine_commit']
        records.write(work/'request.json',request)
        for path in ('cleanup-plan.json','cleanup-complete.json'):
            value = records.read(work/path); value.pop('record_sha256')
            value['engine_commit'] = request['engine_commit']; value = generations.seal(value)
            if path == 'cleanup-plan.json':
                plan = value
            else:
                value.pop('record_sha256'); value['plan_sha256'] = plan['record_sha256']; value = generations.seal(value)
            records.write(work/path,value)
        progress['plan_sha256'] = plan['record_sha256']; records.write(work/'cleanup-progress.json',progress)
        self.assertEqual(retention.references(self.storage)['templates'],[])
        progress['phase'] = 'boot-removing'; records.write(work/'cleanup-progress.json',progress)
        with self.assertRaisesRegex(TransactionError,'cleanup proof changed'):
            retention.references(self.storage)


if __name__=='__main__':
    unittest.main()
