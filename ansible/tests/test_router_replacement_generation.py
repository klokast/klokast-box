"""A proposed generation binds the retained clone without cutover authority."""
from contextlib import nullcontext
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_router_candidate_generation as candidate_fixture
import router_generations as generations
import router_records as records
import router_replacement_generation as staged
from router_transaction import TransactionError


class ReplacementGenerationTests(unittest.TestCase):
    def setUp(self):
        args = candidate_fixture.CandidateGenerationTests().fixture()
        self.args = args
        self.operation = args['operation']
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.work = self.base/'operations'/self.operation
        self.work.mkdir(parents=True,mode=0o700)
        for target,name,value in (
                (records,'ROOT_UID',os.geteuid()),
                (records,'parents',lambda path:None),
                (staged.replacement,'selection',Mock()),
                (staged.replacement,'accepted_runtime',Mock(return_value=(
                    {'record_sha256':'a'*64},args['old']))),
                (staged.preparation,'validate_result',Mock()),
                (staged.native,'Native',Mock()),
                (staged.disks,'verify',Mock(return_value={
                    'path':args['disk_record']['path'],'uuid':args['disk_record']['uuid'],
                    'bytes':2147483648})),
                (staged,'boot_files',Mock(return_value=args['boot'])),
                (staged.time,'time',Mock(return_value=1001))):
            context = patch.object(target,name,value)
            context.start(); self.addCleanup(context.stop)
        self.storage = Mock(box='boxa',base=self.base)
        self.storage.operation.return_value = self.work
        self.storage.lock.return_value = nullcontext()
        self.storage.accepted.return_value = {'record_sha256':'a'*64}
        self.storage.pending.return_value = None
        self.source = {'kind':'klokast.router-replacement-preparation.v1',
            'box':'boxa','operation_id':self.operation,'engine_commit':args['approved_engine'],
            'template_operation':args['template_operation'],
            'accepted_assignment_sha256':'a'*64,'old_sha256':args['old']['record_sha256']}
        self.prepared = {'kind':'klokast.router-candidate-preparation-result.v1',
            'operation_id':self.operation,'success':True,'prepared':args['prepared'],
            'first_contact':args['first_contact']}
        self.disk = args['disk_record']
        self.selection = {'kind':'klokast.router-replacement-generation-selection.v1',
            'box':'boxa','operation_id':self.operation,'engine_commit':args['approved_engine'],
            'source_request_sha256':generations.digest(self.source),
            'preparation_sha256':generations.digest(self.prepared),
            'candidate_disk_sha256':generations.digest(self.disk),
            'accepted_assignment_sha256':'a'*64,
            'old_sha256':args['old']['record_sha256'],
            'release_sha256':args['release']['receipt_sha256'],
            'xen_uuid':args['xen_uuid']}
        self.grant = {'kind':'klokast.router-replacement-generation-grant.v1',
            'engine_commit':args['approved_engine'],
            'selection_sha256':generations.digest(self.selection),
            'granted_at':1000,'expires_at':1300}
        self.result = {'kind':'klokast.router-replacement-preparation-result.v1',
            'box':'boxa','operation_id':self.operation,'status':'replacement-prepared',
            'router_started':False,'old_sha256':args['old']['record_sha256'],
            'preparation_sha256':generations.digest(self.prepared),
            'release_sha256':args['release']['receipt_sha256'],
            'candidate_disk':{'path':self.disk['path'],'uuid':self.disk['uuid'],'bytes':2147483648}}
        for name,value in (
                ('request',self.source),('candidate-job',{}),('release',args['release']),
                ('profile',args['profile']),('candidate-source',{}),('check',{}),
                ('policy',{}),('accepted',{}),('accepted-profile',{}),
                ('replacement-preparation-result',self.result),
                ('preparation-result',self.prepared),('candidate-disk',self.disk),
                ('generation-selection',self.selection),('generation-authorization',self.grant)):
            records.write(self.work/(name+'.json'),value)

    def execute(self):
        return staged.execute(self.storage,self.operation,self.args['approved_engine'])

    def test_generation_is_proposed_without_start_or_cutover_and_exact_retry(self):
        result = self.execute()
        self.assertEqual(result['status'],'proposed-generation-staged')
        self.assertFalse(result['router_started'])
        self.assertFalse(result['cutover_authorized'])
        proposed = records.read(self.work/'proposed-generation.json')
        self.assertEqual(proposed['record_sha256'],result['candidate_sha256'])
        self.assertEqual(proposed['disk']['uuid'],self.disk['uuid'])
        preflight = records.read(self.work/'candidate-preflight.json')
        self.assertEqual(preflight['candidate_sha256'],proposed['record_sha256'])
        self.assertEqual(result['preflight_sha256'],generations.digest(preflight))
        self.assertFalse(preflight['candidate_booted'])
        self.assertEqual(self.execute(),result)

    def test_preflight_partial_publication_recovers_but_changed_record_refuses(self):
        original = records.write
        def interrupted(path,value):
            if path.name == 'candidate-preflight.json':
                raise OSError('synthetic publication failure')
            return original(path,value)
        with patch.object(records,'write',side_effect=interrupted),self.assertRaises(OSError):
            self.execute()
        self.assertTrue((self.work/'proposed-generation.json').exists())
        self.assertFalse((self.work/'candidate-preflight.json').exists())
        self.execute()
        records.write(self.work/'candidate-preflight.json',{'changed':True})
        with self.assertRaisesRegex(TransactionError,'candidate-preflight changed on retry'):
            self.execute()

    def test_expiry_during_boot_artifact_copy_cannot_publish_preflight(self):
        def expire(*args):
            staged.time.time.return_value = self.grant['expires_at']
            return self.args['boot']
        staged.boot_files.side_effect = expire
        with self.assertRaisesRegex(TransactionError,'grant is stale'):
            self.execute()
        self.assertFalse((self.work/'proposed-generation.json').exists())
        self.assertFalse((self.work/'candidate-preflight.json').exists())

    def test_stale_grant_or_changed_proposal_refuses(self):
        records.write(self.work/'generation-authorization.json',{
            **self.grant,'expires_at':1001})
        with self.assertRaisesRegex(TransactionError,'grant is stale'):
            self.execute()
        self.assertFalse((self.work/'proposed-generation.json').exists())
        records.write(self.work/'generation-authorization.json',self.grant)
        self.execute()
        records.write(self.work/'proposed-generation.json',{'changed':True})
        with self.assertRaisesRegex(TransactionError,'changed on retry'):
            self.execute()

    def test_old_router_or_candidate_disk_change_during_boot_copy_refuses_proposal(self):
        original = staged.replacement.accepted_runtime.return_value
        staged.replacement.accepted_runtime.side_effect = [original, (
            {'record_sha256':'b'*64}, self.args['old'])]
        with self.assertRaisesRegex(TransactionError,'accepted router changed'):
            self.execute()
        self.assertFalse((self.work/'proposed-generation.json').exists())
        staged.replacement.accepted_runtime.side_effect = None
        staged.disks.verify.side_effect = [
            {'path':self.disk['path'],'uuid':self.disk['uuid'],'bytes':2147483648},
            {'path':self.disk['path'],'uuid':'changed','bytes':2147483648}]
        with self.assertRaisesRegex(TransactionError,'candidate disk changed'):
            self.execute()
        self.assertFalse((self.work/'proposed-generation.json').exists())


if __name__ == '__main__':
    unittest.main()
