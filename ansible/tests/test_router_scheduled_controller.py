"""Cron must use signed router proof, serialize work, and retain failures."""
import copy
import datetime as dt
from contextlib import ExitStack, nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test_router_daily_cutover as cutover_tests


class ScheduleStatusTests(unittest.TestCase):
    def setUp(self):
        self.module=cutover_tests.cli()
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.state=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.engine='a'*40
        self.policy={'enabled':True,'targets':{'boxa':['router'],'boxb':['dmz','router']},
            'exclusions':[],'check-frequency':'daily','check-time':'00:10',
            'replacement-minutes':15,'recovery-minutes':15,
            'maintenance-window':{'start':'02:00','last-start':'03:00','end':'04:00'}}
        self.schedule={'kind':'klokast.vm-update-schedule.v1','policy':self.policy,
                       'enabled':True,'replacement_ready':False}
        self.paused=False
        for owner,name,value in ((self.module,'STATE',self.state),
                (self.module.transport,'require_controller',mock.Mock()),
                (self.module.transport,'installation_lock',mock.Mock(side_effect=nullcontext)),
                (self.module.transport,'approved_engine',mock.Mock(return_value=self.engine)),
                (self.module.transport,'command',mock.Mock(side_effect=lambda args,**kw:self.engine if 'rev-parse' in args else '')),
                (self.module,'schedule_source',mock.Mock(side_effect=lambda **kw:copy.deepcopy(self.schedule))),
                (self.module,'check_policy_at',mock.Mock(side_effect=self.context)),
                (self.module,'rollout_evidence_at',mock.Mock(side_effect=self.native))):
            self.stack.enter_context(mock.patch.object(owner,name,value))

    def normalized(self):
        value=copy.deepcopy(self.policy)
        value['targets']={box:sorted(roles) for box,roles in value['targets'].items()}
        value['exclusions']=sorted(value['exclusions'],key=lambda row:(row['box'],row['role']))
        return value

    def context(self,*args):
        policy=self.normalized()
        return copy.deepcopy(self.schedule),{'policy':policy,'paused':self.paused}, \
            {**policy,'enabled':policy['enabled'] and not self.paused},self.module.router_updates.digest(policy)

    def native(self,box,engine,*args):
        return {'kind':'klokast.router-rollout-status.v1','box':box,'engine_commit':engine,
            'ready':True,'policy_sha256':self.module.router_updates.digest(self.normalized()),
            'readiness_sha256':'b'*64,'reason':'qualified'}

    def test_all_native_router_proofs_required_independent_of_shared_vm_readiness(self):
        result=self.module.schedule_status()
        self.assertTrue(result['ready'])
        self.assertEqual(result['targets'],['boxa','boxb'])
        self.assertEqual(set(result['readiness']),{'boxa','boxb'})
        self.assertEqual((result['check_time'],result['cutover_time']),('00:10','02:00'))
        self.assertTrue((Path(result['evidence_directory'])/'schedule-status.json').exists())
        self.module.rollout_evidence_at.side_effect=lambda *args:{**self.native(*args),'ready':False}
        self.assertFalse(self.module.schedule_status()['ready'])

    def test_absent_disabled_unactivated_and_paused_policy_never_reads_native_or_enables(self):
        for mode in ('absent','disabled','unactivated','paused'):
            with self.subTest(mode=mode):
                saved=copy.deepcopy(self.schedule)
                if mode=='absent': self.schedule['policy']=None
                elif mode=='disabled': self.schedule['policy']['enabled']=False
                elif mode=='unactivated': self.schedule['enabled']=False
                else: self.paused=True
                self.assertFalse(self.module.schedule_status()['ready'])
                self.schedule=saved; self.policy=self.schedule['policy']; self.paused=False
        self.module.rollout_evidence_at.assert_not_called()
        self.assertEqual(list(self.state.iterdir()),[])

    def test_exclusions_and_signed_source_normalization_match_instance_projection(self):
        self.policy['targets']['boxb']=['router','dmz']
        self.policy['exclusions']=[{'box':'boxb','role':'router','reason':'maintenance'}]
        result=self.module.schedule_status()
        self.assertTrue(result['ready']); self.assertEqual(result['targets'],['boxa'])
        self.assertEqual(result['policy_sha256'],self.module.router_updates.digest(self.normalized()))

    def test_changed_engine_or_policy_and_foreign_proof_cannot_enable(self):
        self.module.rollout_evidence_at.side_effect=lambda *args:{**self.native(*args),'policy_sha256':'f'*64}
        self.assertFalse(self.module.schedule_status()['ready'])
        self.module.rollout_evidence_at.side_effect=self.native
        self.module.transport.approved_engine.side_effect=[self.engine,'f'*40]
        with self.assertRaisesRegex(self.module.UpdateError,'authority changed'):
            self.module.schedule_status()
        self.module.transport.approved_engine.side_effect=None
        self.module.check_policy_at.side_effect=lambda *args:(copy.deepcopy(self.schedule),
            {'policy':self.normalized(),'paused':False},self.normalized(),'f'*64)
        with self.assertRaisesRegex(self.module.UpdateError,'exact Instance policy'):
            self.module.schedule_status()

    def test_invalid_or_inside_window_preparation_time_refuses_before_native(self):
        for value in ('02:10','04:00','24:00','02:00+00:00','2'):
            with self.subTest(value=value):
                self.policy['check-time']=value
                with self.assertRaises(self.module.UpdateError): self.module.schedule_status()
        self.module.rollout_evidence_at.assert_not_called()

    def test_shared_policy_can_reserve_more_time_than_the_router_native_deadline(self):
        self.policy.update({'replacement-minutes':120,'recovery-minutes':120})
        self.policy['maintenance-window']['end']='08:00'
        self.assertTrue(self.module.schedule_status()['ready'])


class ScheduledDispatchTests(unittest.TestCase):
    def setUp(self):
        self.case=cutover_tests.DailyCutoverTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.module=self.case.module; self.stack=self.case.stack
        self.status={'kind':'klokast.router-schedule-status.v1','ready':True,'reason':'qualified',
            'engine_commit':'a'*40,'schedule_sha256':'b'*64,'targets':['k001'],
            'cutover_time':'02:00','window_end':'04:00','evidence_directory':str(self.case.state)}
        self.stack.enter_context(mock.patch.object(self.module,'schedule_status',return_value=self.status))
        self.prepare=self.stack.enter_context(mock.patch.object(self.module,'daily_prepare_locked',
            return_value={'preparation_status':'no-eligible-update'}))
        self.clean=self.stack.enter_context(mock.patch.object(self.module,'cleanup_daily_locked',side_effect=self.cleanup))
        self.now=dt.datetime(2026,10,2,0,10,tzinfo=dt.timezone.utc)
        owner=self
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None): return owner.now
        self.stack.enter_context(mock.patch.object(self.module.dt,'datetime',Clock))

    def cleanup(self):
        record=self.case.saved()
        self.assertEqual(record['status'],'accepted-needs-cleanup')
        self.case.pointer.unlink()
        return {'status':'reconciled','outcome':'accepted'}

    def test_no_ready_proof_or_maintenance_window_never_starts_preparation(self):
        self.status['ready']=False
        self.assertEqual(self.module.scheduled('prepare')['status'],'deferred')
        self.status['ready']=True; self.now=self.now.replace(hour=2)
        self.assertEqual(self.module.scheduled('prepare')['reason'],'preparation-start-is-inside-maintenance-window')
        self.prepare.assert_not_called(); self.case.launcher.assert_not_called()

    def test_unchanged_checks_repeat_without_cutover_or_new_pointer(self):
        self.case.pointer.unlink()
        for _ in range(2):
            self.assertEqual(self.module.scheduled('prepare')['result']['preparation_status'],'no-eligible-update')
        self.assertFalse(self.case.pointer.exists()); self.case.launcher.assert_not_called()

    def test_cutover_then_accepted_cleanup_never_launches_second_router_in_same_pass(self):
        self.assertEqual(self.module.scheduled('cutover')['result']['status'],'accepted-needs-cleanup')
        self.assertEqual(self.module.scheduled('prepare')['reason'],'accepted-operation-cleaned')
        self.assertFalse(self.case.pointer.exists())
        self.case.launcher.assert_called_once(); self.clean.assert_called_once(); self.prepare.assert_not_called()

    def test_controller_loss_reconciles_native_acceptance_without_relaunch(self):
        self.case.launcher.side_effect=KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt): self.module.scheduled('cutover')
        self.assertEqual(self.case.saved()['phase'],'cutover-running')
        self.assertEqual(self.module.scheduled('prepare')['status'],'reconciled')
        self.case.launcher.assert_called_once(); self.prepare.assert_not_called()

    def test_failed_accepted_cleanup_keeps_barrier_and_does_not_prepare(self):
        self.module.scheduled('cutover')
        self.clean.side_effect=self.module.UpdateError('persistence incomplete')
        with self.assertRaisesRegex(self.module.UpdateError,'persistence incomplete'):
            self.module.scheduled('prepare')
        self.assertEqual(self.case.saved()['status'],'accepted-needs-cleanup')
        self.case.launcher.assert_called_once(); self.prepare.assert_not_called()

    def test_rollback_stops_rollout_and_never_automatically_cleans_failure(self):
        self.case.outcome='rolled-back'
        with self.assertRaisesRegex(self.module.UpdateError,'rolled back'): self.module.scheduled('cutover')
        with self.assertRaisesRegex(self.module.UpdateError,'explicit reconciliation'): self.module.scheduled('prepare')
        self.assertEqual(self.case.saved()['status'],'rolled-back-needs-cleanup')
        self.clean.assert_not_called(); self.prepare.assert_not_called()

    def test_failed_preparation_stale_authority_and_concurrent_manual_command_refuse(self):
        self.module.transport.write(self.case.pointer,{**self.case.record,'phase':'template','status':'failed'})
        with self.assertRaisesRegex(self.module.UpdateError,'explicit reconciliation'): self.module.scheduled('prepare')
        self.module.transport.write(self.case.pointer,self.case.record)
        self.status['schedule_sha256']='f'*64
        with self.assertRaisesRegex(self.module.UpdateError,'current scheduling authority'): self.module.scheduled('cutover')
        with self.module.transport.router_driver_lock(self.case.state):
            with self.assertRaisesRegex(self.module.UpdateError,'driver lock'): self.module.scheduled('prepare')
        self.prepare.assert_not_called(); self.case.launcher.assert_not_called(); self.clean.assert_not_called()


if __name__=='__main__': unittest.main()
