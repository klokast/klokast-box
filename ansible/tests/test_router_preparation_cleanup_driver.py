"""Daily failed preparation releases its barrier only after checked native cleanup."""
import copy
import json
from pathlib import Path
from contextlib import nullcontext
import unittest
from unittest import mock

import test_router_preparation_cleanup as native_tests
from test_router_template_cli import load_cli
import router_generations as generations
import router_preparation_cleanup as native_cleanup
import router_records as records
from platform_updates import UpdateError


class FailedPreparationDriverTests(unittest.TestCase):
    def setUp(self):
        native_tests.PreparationCleanupTests.setUp(self)
        self.module = load_cli()
        self.state,self.cache = self.base/'controller',self.base/'cache'
        self.state.mkdir(mode=0o700); self.cache.mkdir(mode=0o700)
        self.directory,self.cached = self.state/self.operation,self.cache/('replacement-'+self.operation)
        self.directory.mkdir(mode=0o700); self.cached.mkdir(mode=0o700)
        self.module.transport.write(self.cached/'request.json',self.source)
        self.record = {'kind':'klokast.router-daily-preparation.v1','box':'boxa','engine_commit':self.engine,
            'operation_id':self.operation,'check_operation':'b'*24,'source_operation':'d'*24,
            'template_operation':'a'*24,'compatibility_operation':'c'*24,
            'accepted_generation_sha256':self.old['record_sha256'],'report_sha256':'e'*64,
            'schedule_sha256':'f'*64,'phase':'preparation','status':'failed',
            'results':{'template':{'status':'generic-template-qualified'},'compatibility':{'success':True}}}
        self.pointer = self.state/'daily-preparation.json'
        self.module.transport.write(self.pointer,self.record)
        self.accepted = {'assignment':self.records.accepted(),'generation':self.old}
        self.events = []
        self.fail_service = self.fail_lbu = self.fail_reply = False
        self.api = {'devices':[]}
        for owner,name,value in ((self.module,'STATE',self.state),(self.module,'CACHE',self.cache),
            (self.module.transport,'require_controller',mock.Mock()),
            (self.module.transport,'installation_lock',lambda:nullcontext()),
            (self.module.transport,'approved_engine',mock.Mock(return_value=self.engine)),
            (self.module,'accepted_source_at',mock.Mock(return_value=self.accepted)),
            (self.module.transport,'command',mock.Mock(side_effect=self.dispatch))):
            patch = mock.patch.object(owner,name,value); patch.start(); self.addCleanup(patch.stop)

    def command(self,argv,*args,**kwargs):
        return native_tests.PreparationCleanupTests.command(self,argv,*args,**kwargs)

    def dispatch(self,argv,**kwargs):
        text = [str(item) for item in argv]
        if text[0] == 'git':
            return self.engine if 'rev-parse' in text else ''
        if 'ts-devices-list' in text[-1]:
            self.events.append('api')
            return json.dumps(self.api)
        if any(item.endswith('/platform-check') for item in text):
            self.events.append('health')
            if self.fail_service:
                raise UpdateError('accepted service failed')
            return ''
        if any('74-router-accepted-verification.yml' in item for item in text):
            self.events.append('accepted')
            return ''
        if any('74-router-diagnostic-lbu-commit.yml' in item for item in text):
            self.events.append('persist')
            if self.fail_lbu:
                raise UpdateError('unrelated LBU change')
            return ''
        arguments = self.module.transport.load(Path(text[-1][1:]))
        token = arguments['router_cleanup_token']
        if any('74-router-preparation-cleanup-plan.yml' in item for item in text):
            self.events.append('plan')
            result = native_cleanup.plan(self.records,self.operation,self.engine,verify=self.verify)
            action,destination = 'preparation-cleanup-plan','preparation-plan-'+token+'.json'
        elif any('74-router-preparation-cleanup-retire.yml' in item for item in text):
            self.events.append('retire')
            records.write(self.work/('preparation-cleanup-grant-'+token+'.json'),arguments['router_cleanup_grant'])
            result = native_cleanup.retire(self.records,self.operation,self.engine,token,verify=self.verify)
            if self.fail_reply:
                self.fail_reply = False
                raise UpdateError('native reply lost after retirement')
            action,destination = 'abort-preparation','preparation-retired-'+token+'.json'
        else:
            raise AssertionError('unexpected cleanup transport: '+repr(text))
        self.module.transport.write(self.directory/destination,{'kind':'klokast.router-command-result.v1',
            'box':'boxa','engine_commit':self.engine,'action':action,'result':result})
        return ''

    def test_service_and_absence_proof_precede_native_retirement_and_lbu_precedes_release(self):
        result = self.module.cleanup_daily()
        self.assertEqual(result['outcome'],'preparation-aborted')
        self.assertEqual(self.events,['plan','health','health','accepted','api','retire','persist'])
        self.assertFalse(self.pointer.exists())
        archive = self.module.transport.load(self.directory/'daily-preparation-complete.json')
        self.assertEqual(archive['status'],'reconciled')
        self.assertEqual(archive['results']['cleanup']['cleanup']['status'],'unused-resources-retired')
        self.assertEqual(len(self.commands),1)

    def test_failed_service_or_registered_candidate_keeps_disk_and_barrier(self):
        self.fail_service = True
        with self.assertRaisesRegex(UpdateError,'service failed'):
            self.module.cleanup_daily()
        self.fail_service = False
        self.api['devices'] = [{'hostname':generations.tailnet_hostname('boxa',self.operation)}]
        with self.assertRaisesRegex(UpdateError,'candidate is registered'):
            self.module.cleanup_daily()
        self.assertTrue(self.pointer.exists())
        self.assertEqual(self.commands,[])

    def test_lost_native_reply_and_failed_lbu_retry_the_same_cleanup_without_another_lvremove(self):
        self.fail_reply = True
        with self.assertRaisesRegex(UpdateError,'reply lost'):
            self.module.cleanup_daily()
        self.assertTrue(self.pointer.exists())
        self.fail_lbu = True
        with self.assertRaisesRegex(UpdateError,'LBU change'):
            self.module.cleanup_daily()
        self.assertTrue(self.pointer.exists())
        self.fail_lbu = False
        self.module.cleanup_daily()
        self.assertFalse(self.pointer.exists())
        self.assertEqual(len(self.commands),1)

    def test_early_builder_failure_and_retained_launch_cannot_release_the_barrier(self):
        self.record['phase'] = 'compatibility'
        self.module.transport.write(self.pointer,self.record)
        with self.assertRaisesRegex(UpdateError,'template/compatibility reconciliation'):
            self.module.cleanup_daily()
        self.record['phase'] = 'preparation'
        self.module.transport.write(self.pointer,self.record)
        self.module.transport.write(self.directory/'worker.json',{'launched':True})
        with self.assertRaisesRegex(UpdateError,'retained launch'):
            self.module.cleanup_daily()
        self.assertTrue(self.pointer.exists())
        self.assertEqual(self.events,[])

    def test_archive_conflict_retains_reconciled_pointer_and_repeated_native_receipt(self):
        self.module.transport.write(self.directory/'daily-preparation-complete.json',{'foreign':True})
        with self.assertRaisesRegex(UpdateError,'archive changed'):
            self.module.cleanup_daily()
        self.assertEqual(self.module.transport.load(self.pointer)['status'],'reconciled')
        with self.assertRaisesRegex(UpdateError,'archive changed'):
            self.module.cleanup_daily()
        self.assertEqual(len(self.commands),1)


if __name__ == '__main__':
    unittest.main()
