"""Service replacement decisions and evidence, without live infrastructure."""
import contextlib
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from test_infrastructure_guest import load, ROOT
import vm_service_probe as probe
import vm_service_updates as updates
import vm_service_runtime as service
import vm_service_execution as execution
import air_maintenance


class DNSReplacementInputs(unittest.TestCase):
    def test_runtime_resolver_is_not_copied_into_a_new_boot(self):
        module = load('service_dns_finalize_test', ROOT / 'ansible/roles/vm-template-builder/files/vm-service-finalize')
        self.assertNotIn('etc/resolv.conf', module.CONFIG)
        self.assertIn('etc/network/if-up.d/10-klokast-dns', module.CONFIG)
        # The persistent managed source is copied; /run/resolvconf is rebuilt by ifup.
        self.assertIn('etc/klokast', module.CONFIG)
        self.assertNotIn('run', module.STATE)


class InfrastructureAuthority(unittest.TestCase):
    def test_failed_deletion_of_older_roots_is_reported_without_failing_the_update(self):
        with patch.object(updates,'step',side_effect=RuntimeError('service step failed: retire')), \
                patch.object(updates,'progress'):
            result=updates.retire_older('boxa','air')
        self.assertEqual(result['state'],'deferred')
        self.assertEqual(result['retry'],'ansible/bin/platform-update update --box boxa --role air --retire-older')

    def test_only_the_current_standby_and_declared_runners_are_targets(self):
        view={'instance':{'boxes':{'boxa':{},'boxb':{}},'controllers':['boxa','boxb'],'airunners':['boxa-air']},
              'controllers':{'active':'boxa','standby':'boxb'}}
        self.assertEqual(service.target(view,'boxb','ops'),'running')
        self.assertEqual(service.target(view,'boxa','air'),'running')
        for box,role in (('boxa','ops'),('boxb','air')):
            with self.assertRaises(RuntimeError):service.target(view,box,role)
        view['controllers']={'active':'boxb','standby':'boxa'}
        with self.assertRaises(RuntimeError):service.target(view,'boxb','ops')

    def test_recovered_infrastructure_uses_the_verified_old_generation(self):
        import hashlib
        from types import SimpleNamespace
        import vm_infrastructure_source as source
        disks={'/dev/vg0/old-root':{'uuid':'root-id','bytes':8192}}
        tx=SimpleNamespace(journal={'stage':'recovered'},
            request={'old_uuid':'old','old_disks':disks,'new_uuid':'candidate','new_disks':{}},
            assignment_content=lambda side: b'old boot' if side=='old' else b'new boot')
        engine=SimpleNamespace(t=SimpleNamespace(current_assignment=lambda *a,**k: contextlib.nullcontext(tx)))
        boot=hashlib.sha256(b'old boot').hexdigest()
        config={'infrastructure_source':None,'before':{'retained_uuid':None}}
        source.require(engine,'boxa','ops',config,'old',disks,boot)
        for identity, selected, checksum in (('candidate',disks,boot),('old',{},boot),('old',disks,'0'*64)):
            with self.assertRaisesRegex(RuntimeError,'recovered or fenced common assignment'):
                source.require(engine,'boxa','ops',config,identity,selected,checksum)
        tx.journal['stage']='booted'
        with self.assertRaisesRegex(RuntimeError,'recovered or fenced common assignment'):
            source.require(engine,'boxa','ops',config,'old',disks,boot)


class ServiceDecisions(unittest.TestCase):
    def setUp(self):
        self.before = {'identity':{'tailscale':'retained'},'accounts':{'neo':[1000,1000]},'workloads':{'containers':['kept']},
                       'kernel':'6.test','packages_sha256':'f'*64,'retained_uuid':'11111111-2222-3333-4444-555555555555','kernel_modules':True,'online':True,'firewall':True,'service':'podman'}
        self.record = {'box':'boxa','role':'bak','image':'a'*24,'operation_id':'b'*24,'new_uuid':'new',
                       'requested_configuration':{'engine_commit':'old','instance_commit':'old','configuration_sha256':'d'*64,'before':self.before,'source_files':{},'image_evidence':{'kernel_release':'6.test','packages_sha256':'f'*64}}}
        self.after = dict(self.before,xen_uuid='new',copy={'kind':'klokast.vm-service-state.v1','box':'boxa','role':'bak','copy_verified':True,'source_files':{}})
        for target,name,value in ((updates,'authority','running'),(updates.inputs,'revision','c'*40),
                (updates,'step',{'pending':[],'installed':self.record}),(probe,'probe',self.after)):
            p=patch.object(target,name,return_value=value);m=p.start();self.addCleanup(p.stop);setattr(self,name,m)
            if name in ('authority','step'):
                p=patch.object(service,name,m);p.start();self.addCleanup(p.stop)
        p=patch.object(updates.runtime,'vm_update_installation_lock',side_effect=contextlib.nullcontext);p.start();self.addCleanup(p.stop)
        p=patch.object(updates,'finish_frozen',side_effect=updates.finish);p.start();self.addCleanup(p.stop)
        p=patch.object(updates.configuration_identity,'describe',return_value=('d'*64,{'role vm-dns':'e'*64}));self.identity=p.start();self.addCleanup(p.stop)

    def test_runner_busy_skips_before_any_image_or_disk_work(self):
        with patch.object(air_maintenance,'remote',return_value={'idle':False,'busy_pids':[123,456],'hold_seconds':1800}), \
                patch.object(updates.images,'local_action') as image:
            result=updates.execute('boxa','air')
        self.assertEqual(result['state'],'skipped');self.assertEqual(result['busy_processes'],2)
        # Only the read-only dom0 status, which tells if the runner is fenced, precedes the idle check.
        self.assertEqual([call.args[2] for call in self.step.call_args_list],['status'])
        image.assert_not_called();self.revision.assert_not_called()

    def test_idle_runner_preparation_only_probes_the_target(self):
        with patch.object(air_maintenance,'remote',return_value={'idle':True,'maintenance':False}) as remote:
            result=updates.execute('boxa','air',dry_run=True)
        self.assertEqual(result['state'],'planned')
        remote.assert_called_once_with('boxa','image-idle')
        self.assertEqual([call.args[:3] for call in self.step.call_args_list],[('boxa','air','status')])

    def test_runner_becoming_busy_never_stops_even_with_maintenance(self):
        for maintenance in (False, True):
            self.step.reset_mock()
            self.step.return_value={'journal':{'stage':'armed'}}
            with patch.object(air_maintenance,'before_stop',side_effect=RuntimeError('runner busy')):
                with self.assertRaisesRegex(RuntimeError,'runner busy.*resume with'):
                    execution.finish('boxa','air',Path('/frozen'),
                        {'service_configuration':{'maintenance':maintenance},'service_receipt':{}},{},'a'*24,'b'*24,'c'*64)
            # The status read finds no booted candidate, so nothing is fenced.
            self.assertEqual([call.args[2] for call in self.step.call_args_list],['prepare','status'])

    def test_missing_workspace_is_reported_without_failing_the_release(self):
        import io
        for released,expected in (({'cleared':True,'workspace':'not restored: checkpoint missing'},
                                    {'workspace':'not restored: checkpoint missing'}),
                                   ({'cleared':True,'workspace':'restored'},{}),({'cleared':True},{})):
            with self.subTest(expected=expected),patch.object(air_maintenance,'release',return_value=released), \
                    patch('sys.stderr',new_callable=io.StringIO) as err:
                self.assertEqual(execution.release_runner('boxa','a'*24),expected)
            self.assertEqual('codex resume' in err.getvalue(),bool(expected))

    def test_closure_of_an_ended_replacement_opens_the_runner_before_the_new_one(self):
        closed={'idle':False,'busy_pids':[123],'hold_seconds':0,'reservation':{'operation':'a'*24,'owner':'air'}}
        with patch.object(air_maintenance,'remote',return_value=closed), \
                patch.object(air_maintenance,'reopen',return_value=None) as reopen:
            result=updates.execute('boxa','air')
        # The VM journal has no pending replacement, so the closure has ended.
        reopen.assert_called_once_with('boxa','air',closed['reservation'])
        self.assertEqual(result['reason'],updates.RUNNER_BUSY)

    def test_closure_of_a_pending_replacement_names_its_resume(self):
        self.step.return_value={'pending':[{'operation_id':'a'*24,'box':'boxa','role':'air'}],'installed':self.record}
        closed={'idle':True,'reservation':{'operation':'a'*24,'owner':'air'}}
        with patch.object(air_maintenance,'remote',return_value=closed),patch.object(air_maintenance,'reopen') as reopen:
            with self.assertRaisesRegex(RuntimeError,'incomplete service update; resume with'):
                updates.execute('boxa','air')
        reopen.assert_not_called()

    def test_closure_of_a_dom0_update_skips_the_replacement(self):
        closed={'idle':True,'reservation':{'operation':'c'*24,'owner':'dom0'}}
        with patch.object(air_maintenance,'remote',return_value=closed),patch.object(air_maintenance,'release') as release:
            result=updates.execute('boxa','air')
        self.assertEqual((result['state'],result['reason']),
                         ('skipped','runner is closed for dom0 update operation '+'c'*24+'; resume that operation first'))
        release.assert_not_called()
        self.assertEqual([call.args[2] for call in self.step.call_args_list],['status'])

    def test_fenced_runner_is_replaced_from_recorded_evidence_without_runner_access(self):
        failed={'operation_id':'f'*24,'box':'boxa','role':'air','requested_configuration':{'before':self.before}}
        self.step.return_value={'pending':[],'installed':None,'failed':failed,
                                'assignment':{'operation_id':'f'*24,'stage':'failed','selection':'fenced'}}
        with patch.object(air_maintenance,'remote') as remote,patch.object(updates.vm_service_probe,'probe') as live, \
                patch.object(updates.images,'local_action',side_effect=RuntimeError('image build reached')):
            with self.assertRaisesRegex(RuntimeError,'image build reached'):
                updates.execute('boxa','air')
        remote.assert_not_called();live.assert_not_called()
        for options in ({'resume':'f'*24},{'migrate_root_data':True}):
            with self.subTest(options=options),self.assertRaisesRegex(RuntimeError,'is fenced after operation'):
                updates.execute('boxa','air',**options)

    def test_fence_selects_only_a_booted_current_candidate(self):
        operation={'operation_id':'a'*24,'box':'boxa','role':'air'}
        def observed(stage,current='a'*24):
            return {'pending':[operation],'operation':operation,'assignment':{'operation_id':current,'stage':stage}}
        for stage in ('booted','starting','tested'):
            self.step.reset_mock();self.step.side_effect=[observed(stage),{}]
            self.assertEqual(updates.execute('boxa','air',resume='a'*24,fence=True)['state'],'failed')
            self.assertEqual([call.args[2] for call in self.step.call_args_list],['status','fence'])
        self.step.reset_mock();self.step.side_effect=[observed('failed')]
        self.assertEqual(updates.execute('boxa','air',resume='a'*24,fence=True)['state'],'failed')
        for value,message in ((observed('stopped'),'only a booted candidate'),(observed('booted','b'*24),'current operation')):
            self.step.side_effect=[value]
            with self.subTest(message=message),self.assertRaisesRegex(RuntimeError,message):
                updates.execute('boxa','air',resume='a'*24,fence=True)
        self.step.side_effect=None
        with self.assertRaisesRegex(RuntimeError,'runner candidate'):updates.execute('boxa','bak',resume='a'*24,fence=True)
        with self.assertRaisesRegex(RuntimeError,'requires --resume'):updates.execute('boxa','air',fence=True)

    def test_failed_check_after_boot_fences_the_runner_candidate(self):
        booted={'journal':{'stage':'booted'}}
        status={'assignment':{'operation_id':'a'*24,'stage':'booted'}}
        self.step.side_effect=lambda box,role,action,**k:{'prepare':booted,'advance':booted,'status':status,'fence':{}}[action]
        import vm_control_updates
        with patch.object(vm_control_updates,'configure',side_effect=RuntimeError('verification failed')), \
                patch.object(execution,'reconcile_vpn_network'):
            with self.assertRaisesRegex(RuntimeError,'verification failed; the candidate is fenced') as caught:
                execution.finish('boxa','air',Path('/frozen'),{'service_configuration':{},'service_receipt':{}},{},'a'*24,'b'*24,'c'*64)
        self.assertNotIn('--resume',str(caught.exception))
        self.assertIn('fence',[call.args[2] for call in self.step.call_args_list])

    def test_resume_after_boot_fences_instead_of_configuring_again(self):
        booted={'journal':{'stage':'booted'}}
        status={'assignment':{'operation_id':'a'*24,'stage':'booted'}}
        self.step.side_effect=lambda box,role,action,**k:{'prepare':booted,'status':status,'fence':{}}[action]
        import vm_control_updates
        with patch.object(vm_control_updates,'configure') as configure:
            with self.assertRaisesRegex(RuntimeError,'booted before this resume; the candidate is fenced'):
                execution.finish('boxa','air',Path('/frozen'),{'service_configuration':{},'service_receipt':{}},{},'a'*24,'b'*24,'c'*64,resume='a'*24)
        configure.assert_not_called()
        self.assertEqual([call.args[2] for call in self.step.call_args_list],['prepare','status','fence'])

    def test_replacement_of_a_fenced_runner_releases_the_closing_operation(self):
        complete={'journal':{'stage':'complete'}}
        self.step.side_effect=lambda box,role,action,**k:complete
        with patch.object(air_maintenance,'before_stop') as before_stop, \
                patch.object(execution,'release_runner',return_value={}) as release:
            execution.finish('boxa','air',Path('/frozen'),{'service_configuration':{'fenced_operation':'f'*24},'service_receipt':{}},
                             {},'a'*24,'b'*24,'c'*64)
        before_stop.assert_not_called();release.assert_called_once_with('boxa','f'*24)

    def test_unmigrated_controller_requires_explicit_consent_before_preparation(self):
        self.step.return_value={'pending':[],'installed':None}
        with patch.object(updates.images,'local_action') as image:
            with self.assertRaisesRegex(RuntimeError,'migrate-root-data'):
                updates.execute('boxa','ops')
        image.assert_not_called()

    def test_unchanged_image_and_configuration_only_check_health(self):
        # New commits alone do not replace a root; only the two identities decide.
        result=updates.execute('boxa','bak',image='a'*24)
        self.assertEqual(result,{'state':'complete','box':'boxa','role':'bak','image':'a'*24,'replaced':False})
        self.assertEqual([c.args[2] for c in self.step.call_args_list],['status'])
        self.identity.assert_called_once_with('boxa','bak')

    def test_changed_configuration_replaces_the_root_with_the_same_image(self):
        for installed in ('e'*64, None):
            with self.subTest(installed=installed):
                self.record['requested_configuration']['configuration_sha256']=installed
                with patch.object(updates.images,'local_action',side_effect=RuntimeError('receipt reached')) as image, \
                        patch.object(updates,'progress') as progress:
                    with self.assertRaisesRegex(RuntimeError,'receipt reached'):
                        updates.execute('boxa','bak',image='a'*24)
                image.assert_called_once_with('boxa','shared-alpine-v1','c'*40,'image-receipt','a'*24)
                self.assertIn('configuration changed',progress.call_args_list[0].args[0])

    def test_dry_plan_reports_a_configuration_change(self):
        self.identity.return_value=('e'*64,{})
        result=updates.execute('boxa','bak',dry_run=True)
        self.assertEqual(result['configuration'],{'installed_sha256':'d'*64,'declared_sha256':'e'*64,'changed':True})

    def test_accepted_replay_needs_no_old_source_snapshot(self):
        with patch.object(updates.inputs,'recover',side_effect=AssertionError('historical input should not execute')):
            self.assertFalse(updates.execute('boxa','bak',resume='b'*24)['replaced'])

    def test_retired_accepted_replay_does_not_restore_disk_boot_or_image_references(self):
        self.step.return_value={'pending':[],'installed':self.record,'operation':self.record,'operation_retired':True}
        with patch.object(updates.inputs,'recover',side_effect=AssertionError('retired input should not execute')):
            result=updates.execute('boxa','bak',resume=self.record['operation_id'])
        self.assertEqual(result['state'],'retired');self.assertFalse(result['replaced'])
        self.assertEqual([c.args[2] for c in self.step.call_args_list],['status'])
        self.probe.assert_not_called()

    def test_accepted_vpn_convergence_keeps_uid_and_requires_named_primary_group(self):
        self.record['role'] = 'vpn-egress'
        self.after['copy']['role'] = 'vpn-egress'
        self.before['accounts'] = {'neo':[1000,1000], 'vpn-egress':[102,65533]}
        self.after['accounts'] = {'neo':[1000,1000], 'vpn-egress':[102,103]}
        self.after['service_group'] = 103
        probe.verify(self.before,self.after,self.record,unfinished=False)
        with self.assertRaises(RuntimeError): probe.verify(self.before,self.after,self.record)
        for changes in ({'service_group':104}, {'accounts':{'neo':[1000,1000], 'vpn-egress':[103,103]}}):
            with self.assertRaises(RuntimeError):
                probe.verify(self.before,dict(self.after,**changes),self.record,unfinished=False)

    def test_unchanged_image_still_refuses_boot_assignment_drift(self):
        self.step.return_value={'pending':[],'installed':self.record,'assignment':{'stage':'complete','configuration_drift':True,'runtime':'running'}}
        with self.assertRaisesRegex(RuntimeError,'boot assignment'):updates.execute('boxa','bak',image='a'*24)
        self.probe.assert_not_called()

    def test_resume_of_an_abandoned_attempt_never_reports_completion(self):
        record=dict(self.record,stage='abandoned')
        self.step.side_effect=[{'pending':[],'operation':record,'installed':None}]
        with self.assertRaisesRegex(RuntimeError,'abandoned and installed nothing'):
            updates.execute('boxa','bak',resume=record['operation_id'])
        self.probe.assert_not_called()

    def test_explicit_unbooted_abandon_uses_frozen_inputs_without_building(self):
        record=dict(self.record,inputs_sha256='e'*64)
        self.step.side_effect=[{'pending':[record],'installed':None},{'stage':'abandoned'}]
        with patch.object(updates.inputs,'recover',return_value=(Path('/frozen'),{'service_configuration':{}})):
            result=updates.execute('boxa','bak',resume=record['operation_id'],abandon=True)
        self.assertEqual(result['state'],'abandoned')
        self.assertEqual([call.args[2] for call in self.step.call_args_list],['status','abandon'])
        self.probe.assert_not_called()

    def test_stopped_guest_skips_without_inspection_build_or_start(self):
        self.authority.return_value='stopped'
        self.assertEqual(updates.execute('boxa','iot')['state'],'skipped')
        self.step.assert_not_called();self.probe.assert_not_called()

    def test_pending_other_role_blocks_allocation(self):
        self.step.return_value={'pending':[dict(self.record,role='dmz')],'installed':self.record}
        with self.assertRaisesRegex(RuntimeError,'--role dmz --resume'):updates.execute('boxa','bak',image='a'*24)
        self.probe.assert_not_called()

    def test_unrecoverable_pending_inputs_stop_resume(self):
        self.step.return_value={'pending':[self.record],'installed':None}
        with patch.object(updates.inputs,'recover',side_effect=RuntimeError('snapshot missing')):
            with self.assertRaisesRegex(RuntimeError,'snapshot missing'):updates.execute('boxa','bak',resume='b'*24)
        self.probe.assert_not_called()

    def test_prepare_failure_distinguishes_preflight_from_lost_allocation_response(self):
        self.step.return_value={'operation':None}
        self.assertIn('retry with: ansible/bin/platform-update update --box boxa --role bak --image '+ 'a'*24,
                      service.prepare_failure_hint('boxa','bak','b'*24,'a'*24))
        self.step.return_value={'operation':self.record}
        self.assertIn('--resume '+'b'*24,service.prepare_failure_hint('boxa','bak','b'*24,'a'*24))
        self.step.return_value={}
        self.assertIn('--resume '+'b'*24,service.prepare_failure_hint('boxa','bak','b'*24,'a'*24))
        self.step.side_effect=RuntimeError('connection lost')
        self.assertIn('--resume '+'b'*24,service.prepare_failure_hint('boxa','bak','b'*24,'a'*24))

    def test_changed_runtime_intent_blocks_pending_reboot(self):
        record=dict(self.record,inputs_sha256='e'*64)
        after=dict(self.after,boot_id='original-boot')
        state=dict(record,journal={'stage':'booted'},reboot_verification={
            'status':'pending','before':{'boot_id':'original-boot'}})
        self.step.side_effect=[{'pending':[record],'installed':None},state,state]
        self.authority.side_effect=['running','stopped']
        with patch.object(updates.inputs,'recover',return_value=(Path('/frozen'),{'service_configuration':record['requested_configuration'],'service_receipt':{}})), \
             patch.object(service,'wait_probe',return_value=after), \
             patch.object(updates.subprocess,'run') as reboot:
            with self.assertRaisesRegex(RuntimeError,'runtime intent changed before reboot'):
                updates.execute('boxa','bak',resume=record['operation_id'])
            reboot.assert_not_called()

    def test_failed_reboot_command_stops_without_acceptance(self):
        from types import SimpleNamespace
        record = dict(self.record, inputs_sha256='e' * 64)
        after = dict(self.after, boot_id='original-boot')
        state = dict(record, journal={'stage': 'booted'}, reboot_verification={
            'status': 'pending', 'before': {'boot_id': 'original-boot'}})
        self.step.side_effect = [{'pending': [record], 'installed': None}, state, state]
        with patch.object(updates.inputs, 'recover', return_value=(Path('/frozen'), {'service_configuration': record['requested_configuration'], 'service_receipt': {}})), \
             patch.object(service, 'wait_probe', return_value=after) as health, \
             patch.object(updates.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr='doas: permission denied')) as reboot:
            with self.assertRaisesRegex(RuntimeError, 'exit status 1: doas: permission denied; keep the pending reboot verification.*--resume'):
                updates.execute('boxa', 'bak', resume=record['operation_id'])
            self.assertEqual(health.call_count, 1)
            self.assertEqual(reboot.call_args.args[0][-1], '/sbin/reboot')
            # ssh must not share the controller stdio: a dropped session leaves it non-blocking.
            self.assertIs(reboot.call_args.kwargs['stdin'], updates.subprocess.DEVNULL)
            self.assertTrue(reboot.call_args.kwargs['capture_output'])
        self.assertEqual([call.args[2] for call in self.step.call_args_list], ['status', 'prepare', 'advance'])

    def test_ssh_disconnect_is_not_reboot_completion(self):
        from types import SimpleNamespace
        record = dict(self.record, inputs_sha256='e' * 64)
        after = dict(self.after, boot_id='original-boot')
        state = dict(record, journal={'stage': 'booted'}, reboot_verification={
            'status': 'pending', 'before': {'boot_id': 'original-boot'}})
        self.step.side_effect = [{'pending': [record], 'installed': None}, state, state]
        with patch.object(updates.inputs, 'recover', return_value=(Path('/frozen'), {'service_configuration': record['requested_configuration'], 'service_receipt': {}})), \
             patch.object(service, 'wait_probe', return_value=after), \
             patch.object(updates.subprocess, 'run', return_value=SimpleNamespace(returncode=255)), \
             patch.object(execution.time, 'monotonic', side_effect=[0, 301]):
            with self.assertRaisesRegex(RuntimeError, 'boot ID did not change.*--resume'):
                updates.execute('boxa', 'bak', resume=record['operation_id'])
        self.assertEqual([call.args[2] for call in self.step.call_args_list], ['status', 'prepare', 'advance'])

    def test_dry_plan_does_not_build_or_probe_workloads(self):
        with patch.object(updates.images,'local_action') as prepare:
            self.assertEqual(updates.execute('boxa','bak',dry_run=True)['state'],'planned')
            prepare.assert_not_called();self.probe.assert_not_called()

    def test_data_and_identity_changes_refuse_acceptance(self):
        for key,value in [('identity',{}),('workloads',{}),('retained_uuid','other'),('accounts',{}),('xen_uuid','old'),('copy',{}),('firewall',False),('kernel','wrong'),('packages_sha256','wrong')]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                probe.verify(self.before,dict(self.after,**{key:value}),self.record)

    def test_retained_uuid_matches_busybox_and_util_linux_output(self):
        uuid=self.before['retained_uuid']
        legacy=dict(self.before,retained_uuid='/dev/xvdb: UUID="'+uuid+'" TYPE="ext4"')
        probe.verify(legacy,self.after,self.record)
        for value in ('unknown',legacy['retained_uuid']+'\n'+legacy['retained_uuid'],
                      legacy['retained_uuid']+' UUID="'+uuid+'"'):
            with self.subTest(value=value),self.assertRaisesRegex(RuntimeError,'unique UUID'):
                probe.filesystem_uuid(value)

    def test_current_instance_controls_stopped_and_vpn_selection(self):
        view={'instance':{'boxes':{'boxa':{'substrate':{'shared-guests':{'iot':{'runtime-state':'stopped'}}}}}}}
        self.assertEqual(updates.target(view,'boxa','iot'),'stopped')
        self.assertEqual(updates.target(view,'boxa','bak'),'running')
        with self.assertRaisesRegex(RuntimeError,'not declared'):updates.target(view,'boxa','vpn-egress')


class FinalizerInputs(unittest.TestCase):
    def setUp(self):
        self.m=load('service_finalize_test',ROOT/'ansible/roles/vm-template-builder/files/vm-service-finalize')
        self.value={'kind':'klokast.vm-service-finalize.v1','box':'boxa','role':'bak','root_partition':'3',
                    'accounts':{'neo':[1000,1000]},'source_files':{k:'a'*64 for k in
                    ('etc/hostname','etc/passwd','etc/group','etc/shadow','etc/fstab','etc/nftables.nft','etc/network/interfaces')}}

    def test_exact_retained_inputs_and_vpn_account(self):
        self.assertEqual(self.m.validate(self.value),self.value)
        self.value['role']='vpn-egress'
        with self.assertRaises(RuntimeError):self.m.validate(self.value)
        self.value['accounts']['vpn-egress']=[101,101]
        self.m.validate(self.value)

    def test_vpn_retains_its_named_primary_group_and_rejects_conflicts(self):
        import tempfile
        from types import SimpleNamespace as Row
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new'
            (old/'etc').mkdir(parents=True);(new/'etc').mkdir(parents=True)
            users='neo:x:1000:1000::/home/neo:/bin/ash\nvpn-egress:x:102:103::/var/lib/vpn-egress:/sbin/nologin\n'
            for base in (old,new):
                (base/'etc/passwd').write_text(users)
                (base/'etc/shadow').write_text('neo:!:0:0:99999:7:::\nvpn-egress:!:0:0:99999:7:::\n')
            (old/'etc/group').write_text('neo:x:1000:\nvpn-egress:x:103:\n')
            def user(key):
                if key in (102,'vpn-egress'): raise KeyError(key)
                return Row(pw_name='neo',pw_uid=1000,pw_gid=1000)
            def group(key):
                if key in (103,'vpn-egress'): raise KeyError(key)
                if key=='wheel': return Row(gr_name='wheel',gr_gid=10,gr_mem=['neo'])
                return Row(gr_name='neo',gr_gid=1000)
            value={'accounts':{'neo':[1000,1000],'vpn-egress':[102,103]}}
            with patch.object(self.m,'OLD',old),patch.object(self.m,'Path',lambda value:new/str(value).lstrip('/')), \
                 patch.object(self.m.pwd,'getpwuid',side_effect=user),patch.object(self.m.pwd,'getpwnam',side_effect=user), \
                 patch.object(self.m.grp,'getgrgid',side_effect=group),patch.object(self.m.grp,'getgrnam',side_effect=group), \
                 patch.object(self.m,'run') as run:
                self.m.accounts(value)
                self.assertEqual([c.args[0] for c in run.call_args_list], [
                    ['addgroup','-g','103','vpn-egress'],
                    ['adduser','-D','-u','102','-G','vpn-egress','-s','/sbin/nologin','vpn-egress']])
                run.reset_mock()
                for groups in ('neo:x:1000:\nnogroup:x:65533:\nvpn-egress:x:104:\n',
                               'neo:x:1000:\nvpn-egress:x:103:\nother:x:103:\n'):
                    (old/'etc/group').write_text(groups)
                    with self.assertRaises((RuntimeError,KeyError)): self.m.accounts(value)
                    run.assert_not_called()
                (old/'etc/group').write_text('neo:x:1000:\nvpn-egress:x:103:\n')
                with patch.object(self.m.grp,'getgrnam',return_value=Row(gr_name='vpn-egress',gr_gid=104)):
                    with self.assertRaisesRegex(RuntimeError,'group identity'): self.m.accounts(value)
                run.assert_not_called()

    def test_missing_identity_or_unknown_mount_layout_refuses(self):
        for key,value in [('source_files',{}),('accounts',{'neo':[1001,1001]}),('root_partition','../../sda'),('role','ops')]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.m.validate(dict(self.value,**{key:value}))


class NativePreparation(unittest.TestCase):
    def setUp(self):
        import shutil, tempfile
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        for name in ('vm_service_engine.py','vm_service_finalization.py','vm_dmz_creation.py'):
            shutil.copyfile(ROOT/'ansible/roles/vm-service-update/files'/name,self.root/name)
        for name in ('vm_update_engine.py','vm_update_native.py'):
            shutil.copyfile(ROOT/'ansible/roles/vm-update-recovery/files'/name,self.root/name)
        for name in ('vm_service_retirement.py','ops_retirement.py','update_history.py'):
            shutil.copyfile(ROOT/'ansible/lib'/name,self.root/name)
        sys.path.insert(0,str(self.root))
        self.addCleanup(lambda: sys.path.remove(str(self.root)))
        self.m=load('native_service_test',self.root/'vm_service_engine.py')

    def test_resumed_prepare_reports_the_armed_journal(self):
        import io, shutil
        shutil.copyfile(ROOT/'ansible/roles/vm-service-update/files/vm-service-update',self.root/'vm_service_dispatch.py')
        d=load('native_service_dispatch',self.root/'vm_service_dispatch.py')
        work=self.root/'base/operations'/('b'*24);work.mkdir(parents=True)
        (work/'journal.json').write_text('{"stage":"armed"}')
        argv=['vm-service-update','--box','boxa','--role','air','--action','prepare','--operation','b'*24,
              '--inputs-sha256','c'*64,'--config','/c','--receipt','/r']
        with patch.object(d.t,'BASE',self.root/'base'),patch.object(d.engine,'IMAGES',self.root/'images/candidates'), \
             patch.object(d.t,'require_dom0'),patch.object(d.socket,'gethostname',return_value='boxa-dom0'), \
             patch.object(d.t,'budget',side_effect=lambda s:contextlib.nullcontext()), \
             patch.object(d.t,'operation_lock',side_effect=lambda w:contextlib.nullcontext()), \
             patch.object(d.t,'read',side_effect=lambda p:json.loads(p.read_text())), \
             patch.object(d.engine,'prepare',return_value={'stage':'prepared'}),patch.object(sys,'argv',argv), \
             patch('sys.stdout',new_callable=io.StringIO) as out:
            (self.root/'images').mkdir();d.main()
        self.assertEqual(json.loads(out.getvalue())['journal'],{'stage':'armed'})

    def test_xen_capacity_uses_padded_native_field_names(self):
        from types import SimpleNamespace
        with patch.object(self.m,'run',return_value=SimpleNamespace(stdout='total_memory           : 32768\nfree_memory            : 19000\n')):
            self.m.require_memory()
        for output in ('free_memory            : 4096\n','total_memory : 32768\n'):
            with self.subTest(output=output),patch.object(self.m,'run',return_value=SimpleNamespace(stdout=output)):
                with self.assertRaisesRegex(RuntimeError,'insufficient free Xen memory'):
                    self.m.require_memory()

    def test_history_entry_compares_the_receipts_of_the_replaced_and_the_new_generation(self):
        def generation(operation,uuid,image,version,components,sha):
            work=self.root/'base/operations'/operation;work.mkdir(parents=True)
            state={'operation_id':operation,'box':'boxa','role':'bak','image':image,'new_uuid':uuid,'old_uuid':'old',
                   'requested_configuration':{'engine_commit':operation[0]*40,'configuration_sha256':sha,
                                              'configuration_components':components}}
            (work/'service.json').write_text(json.dumps(state))
            (work/'receipt.json').write_text(json.dumps({'files':{'inputs.json':{'tailscale':{'version':version}},
                'release-evidence.json':{'packages':{'openssl':version}}}}))
            return work,state
        generation('a'*24,'u1','a'*24,'1',{'role vm-dns':'1'},'x')
        work,state=generation('b'*24,'u2','b'*24,'2',{'role vm-dns':'2'},'y');state['old_uuid']='u1'
        with patch.object(self.m.t,'BASE',self.root/'base'),patch.object(self.m,'read',side_effect=lambda p:json.loads(p.read_text())):
            entry=self.m.history_entry(work,state)
            self.assertEqual((entry['kind'],entry['image'],entry['configuration']),
                ('image and configuration',{'from':'a'*24,'to':'b'*24},['role vm-dns']))
            self.assertEqual(entry['packages']['changed'],{'openssl':['1','2'],'tailscale':['1','2']})
            state['old_uuid']='unknown'
            self.assertEqual(self.m.history_entry(work,state)['kind'],'first record')

    def test_an_accepted_generation_completes_when_its_history_cannot_be_written(self):
        with patch.object(self.m,'history_entry',side_effect=KeyError('image')), \
             patch.object(self.m.update_history,'ROOT',self.root/'history'):
            self.assertIn('was not written',self.m.record_history(self.root,{'box':'boxa','role':'bak','operation_id':'b'*24}))

    def test_failed_operation_is_finished_and_not_pending(self):
        base=self.root/'base'
        for operation,stage in (('a'*24,'failed'),('b'*24,'booted')):
            work=base/'operations'/operation;work.mkdir(parents=True)
            (work/'service.json').write_text(json.dumps({'box':'boxa','role':'air','operation_id':operation}))
            (work/'journal.json').write_text(json.dumps({'stage':stage}))
        with patch.object(self.m.t,'BASE',base),patch.object(self.m,'read',side_effect=lambda p:json.loads(p.read_text())):
            self.assertEqual([r['operation_id'] for r in self.m.pending('boxa')],['b'*24])

    def test_unarmed_attempt_over_a_fenced_generation_is_abandoned_only_while_it_stays_stopped(self):
        state={'stage':'prepared','role':'air','old_uuid':'fenced','fenced_source':True,'old_disks':{},'new_disks':{}}
        with patch.object(self.m,'domain',return_value={'config':{}}),patch.object(self.m,'fenced_generation',return_value='fenced'), \
                patch.object(self.m.t,'Native'):
            with self.assertRaisesRegex(RuntimeError,'original fenced generation'):
                self.m.abandon(self.root,dict(state))

    def test_abandon_never_rolls_back_a_boot_attempt(self):
        for stage in ('starting','booted','tested','accepted','complete'):
            with self.subTest(stage=stage),patch.object(self.m.Path,'exists',return_value=True), \
                 patch.object(self.m,'read',return_value={'stage':stage}),patch.object(self.m.t,'invoke') as invoke:
                with self.assertRaisesRegex(RuntimeError,'after boot was attempted'):
                    self.m.abandon(self.root,{'stage':'finalized'})
                invoke.assert_not_called()

    def test_abandon_replay_is_safe_and_preserves_all_disks(self):
        with patch.object(self.m.Path,'exists',return_value=True), \
             patch.object(self.m,'read',return_value={'stage':'recovered'}),patch.object(self.m.t,'invoke') as invoke:
            self.assertEqual(self.m.abandon(self.root,{'stage':'abandoned'}),{'stage':'abandoned'})
            invoke.assert_not_called()

    def test_lv_name_reuse_cannot_override_recorded_uuid(self):
        from types import SimpleNamespace
        with patch.object(self.m.t.Native,'lv',return_value={'uuid':'foreign','bytes':8192}):
            with self.assertRaisesRegex(RuntimeError,'replaced or resized'):
                self.m.owned('/dev/vg0/a',{'uuid':'original','bytes':8192})
        with patch.object(self.m.t.Native,'lv',return_value={'uuid':'original','bytes':8192}),patch.object(self.m,'run',return_value=SimpleNamespace(stdout='foreign-tag')):
            with self.assertRaisesRegex(RuntimeError,'ownership tag'):
                self.m.owned('/dev/vg0/a',{'uuid':'original','bytes':8192},'expected-tag')

    def test_recorded_missing_disk_is_not_recreated(self):
        from types import SimpleNamespace
        with patch.object(self.m,'run',return_value=SimpleNamespace(stdout='{"report":[{"lv":[]}]}')) as run:
            with self.assertRaisesRegex(RuntimeError,'disappeared'):
                self.m.allocate('/dev/vg0/a',8192,'tag',{'uuid':'old','bytes':8192})
            self.assertEqual(run.call_count,1)

    def test_json_sort_order_cannot_swap_root_and_data_disks(self):
        operation='a'*24
        work=self.root/'operations'/operation;work.mkdir(parents=True)
        config={'name':'bak','uuid':'11111111-1111-1111-1111-111111111111','type':'pvh','kernel':'/mnt/dom0_data/kernel','ramdisk':'/mnt/dom0_data/initramfs',
                'disk':['phy:/dev/vg0/zzroot,xvda,w','phy:/dev/vg0/aadata,xvdb,w']}
        (work/'old.cfg').write_text('\n'.join(k+' = '+repr(v) for k,v in config.items()))
        state={'operation_id':operation,'role':'bak','old_uuid':config['uuid'],
               'old_disks':{'/dev/vg0/aadata':{},'/dev/vg0/zzroot':{}},
               'new_disks':{'/dev/vg0/vmupd_'+operation+'_data':{},'/dev/vg0/vmupd_'+operation+'_root':{}}}
        with patch.object(self.m.t,'BASE',self.root):
            old,new=self.m.disk_order(state)
        self.assertTrue(old[0].endswith('zzroot'));self.assertTrue(new[0].endswith('_root'))


class ScheduledServices(unittest.TestCase):
    def setUp(self):
        self.nightly=load('nightly_services_test',ROOT/'ansible/bin/ops-controller-nightly')
        self.view={'instance':{'boxes':{'boxa':{'vpn-egress':{'clients':[]},'substrate':{'shared-guests':{'iot':{'runtime-state':'stopped'}}}}}}}

    def test_one_image_per_profile_and_stopped_guest_never_starts(self):
        with patch.object(self.nightly.platform_source,'snapshot',return_value=self.view), \
             patch.object(updates,'authority',return_value='running'), \
             patch.object(updates,'step',return_value={'pending':[]}), \
             patch.object(updates,'execute',return_value={'state':'complete'}) as execute, \
             patch.object(self.nightly.runtime,'vm_update_installation_lock',side_effect=contextlib.nullcontext), \
             patch.object(self.nightly.inputs,'revision',return_value='a'*40), \
             patch.object(self.nightly.infrastructure_images,'local_action',return_value={'state':'candidate-reused','operation_id':'b'*24}) as prepare:
            result=self.nightly.service_runs()
        self.assertEqual([c.args[3] for c in prepare.call_args_list],['prepare','prepare','prepare','cleanup','cleanup','cleanup'])
        self.assertEqual([c.args[1] for c in execute.call_args_list],['bak','bak','bak','dmz','dmz','dmz','vpn-egress','vpn-egress','vpn-egress','router','router','router'])
        self.assertEqual(result[2]['state'],'skipped')

    def test_installed_profile_cleans_before_build_and_after_unchanged_health(self):
        with patch.object(self.nightly.platform_source,'snapshot',return_value=self.view), \
             patch.object(updates,'authority',return_value='running'), \
             patch.object(updates,'step',return_value={'pending':[],'installed':{'image':'c'*24}}), \
             patch.object(updates,'execute',return_value={'state':'complete','replaced':False}), \
             patch.object(self.nightly.runtime,'vm_update_installation_lock',side_effect=contextlib.nullcontext), \
             patch.object(self.nightly.inputs,'revision',return_value='a'*40), \
             patch.object(self.nightly.infrastructure_images,'local_action',return_value={'state':'candidate-reused','operation_id':'c'*24}) as image:
            self.nightly.service_runs()
        self.assertEqual([c.args[3] for c in image.call_args_list],
                         ['cleanup-before','prepare','cleanup-before','prepare','cleanup-before','prepare','cleanup','cleanup','cleanup'])
        self.assertTrue(all(c.args[4]=='c'*24 for c in image.call_args_list if c.args[3]!='prepare'))

    def test_service_failure_marks_the_whole_nightly_run_failed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary, patch.object(self.nightly,'STATE',Path(temporary)), \
             patch.object(self.nightly,'run',return_value={'state':'complete'}), \
             patch.object(self.nightly,'boot_check',return_value={}),patch.object(self.nightly,'log_retention',return_value={}), \
             patch.object(self.nightly,'service_runs',side_effect=RuntimeError('service reboot incomplete')):
            self.assertEqual(self.nightly.main(['--services']),1)
            self.assertEqual(json.loads((Path(temporary)/'current.json').read_text())['state'],'failed')

    def test_pending_operation_stops_before_upstream_work(self):
        with patch.object(self.nightly.platform_source,'snapshot',return_value=self.view), \
             patch.object(updates,'authority',return_value='running'), \
             patch.object(updates,'step',return_value={'pending':[{'role':'bak','operation_id':'b'*24}]}), \
             patch.object(self.nightly.runtime,'vm_update_installation_lock',side_effect=contextlib.nullcontext), \
             patch.object(self.nightly.infrastructure_images,'local_action') as prepare:
            with self.assertRaisesRegex(RuntimeError,'--resume'):self.nightly.service_runs()
            prepare.assert_not_called()

    def test_selected_vpn_retires_after_acceptance_before_image_cleanup(self):
        events=[]
        def execute(*args,**kwargs):
            events.append('retire' if kwargs.get('retire') else 'accepted')
            return {'state':'complete','replaced':True}
        def image(*args):
            events.append(args[3])
            return {'state':'candidate-reused','operation_id':'b'*24}
        with patch.object(self.nightly.platform_source,'snapshot',return_value=self.view), \
             patch.object(updates,'authority',return_value='running'), \
             patch.object(updates,'step',return_value={'pending':[]}), \
             patch.object(updates,'execute',side_effect=execute) as update, \
             patch.object(self.nightly.runtime,'vm_update_installation_lock',side_effect=contextlib.nullcontext), \
             patch.object(self.nightly.inputs,'revision',return_value='a'*40), \
             patch.object(self.nightly.infrastructure_images,'local_action',side_effect=image):
            self.nightly.service_runs(roles=['vpn-egress'])
        self.assertEqual(events,['retire','prepare','accepted','retire','cleanup'])
        self.assertTrue(all(c.args[1]=='vpn-egress' for c in update.call_args_list))

    def test_failed_vpn_verification_stops_before_final_retirement_and_cleanup(self):
        with patch.object(self.nightly.platform_source,'snapshot',return_value=self.view), \
             patch.object(updates,'authority',return_value='running'), \
             patch.object(updates,'step',return_value={'pending':[]}), \
             patch.object(updates,'execute',side_effect=[{'state':'complete'},RuntimeError('VPN verification incomplete')]) as update, \
             patch.object(self.nightly.runtime,'vm_update_installation_lock',side_effect=contextlib.nullcontext), \
             patch.object(self.nightly.inputs,'revision',return_value='a'*40), \
             patch.object(self.nightly.infrastructure_images,'local_action',return_value={'state':'candidate-reused','operation_id':'b'*24}) as image:
            with self.assertRaisesRegex(RuntimeError,'VPN verification incomplete'):
                self.nightly.service_runs(roles=['vpn-egress'])
        self.assertEqual(update.call_count,2)
        self.assertEqual([c.args[3] for c in image.call_args_list],['prepare'])



class FrozenNetworkEnvironmentTest(unittest.TestCase):
    def test_native_subscription_seed_preserves_existing_cache_and_age(self):
        import copy, datetime, grp, os, pwd, shutil, subprocess, tempfile, yaml
        if not shutil.which('ansible-playbook'): self.skipTest('controller Ansible is required')
        tasks=yaml.safe_load((ROOT/'ansible/roles/vpn-egress-runtime/tasks/main.yml').read_text())
        selected=[copy.deepcopy(t) for t in tasks if t['name'] in (
            'Inspect the subscription cache without changing its age',
            'Seed a missing subscription cache before startup')]
        self.assertEqual(len(selected),2)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); cache=root/'guest/proxies/test.yaml'; cache.parent.mkdir(parents=True)
            seed=root/'seed.yaml'; seed.write_text('first validated seed'); seed.chmod(0o600)
            text=yaml.safe_dump(selected).replace('/var/lib/vpn-egress/',str(root/'guest')+'/')
            text=text.replace('owner: vpn-egress','owner: '+pwd.getpwuid(os.getuid()).pw_name)
            text=text.replace('group: vpn-egress','group: '+grp.getgrgid(os.getgid()).gr_name)
            variables={'vpn_egress_config':{'proxy-providers':{'relays':{'path':'./proxies/test.yaml'}}},
                       'vpn_egress_subscription_seed':str(seed),'vpn_egress_subscription_mtime':'202610140000.00'}
            def converge():
                play=root/'play.yml'
                play.write_text(yaml.safe_dump([{'hosts':'localhost','connection':'local','gather_facts':False,
                    'vars':variables,'tasks':yaml.safe_load(text)}]))
                result=subprocess.run(['ansible-playbook','-i','localhost,',str(play)],text=True,capture_output=True,
                    timeout=60,env=dict(os.environ,ANSIBLE_NOCOLOR='1',ANSIBLE_STDOUT_CALLBACK='default'))
                self.assertEqual(result.returncode,0,result.stderr+result.stdout)
            converge()
            stamp=cache.stat().st_mtime_ns
            self.assertEqual(cache.read_text(),'first validated seed')
            self.assertEqual(datetime.datetime.fromtimestamp(cache.stat().st_mtime,datetime.timezone.utc).isoformat(),
                             '2026-10-14T00:00:00+00:00')
            seed.write_text('new controller seed'); variables['vpn_egress_subscription_mtime']='202610150000.00'
            cache.write_text('live native subscription'); os.utime(cache,ns=(stamp,stamp))
            converge()
            self.assertEqual(cache.read_text(),'live native subscription')
            self.assertEqual(cache.stat().st_mtime_ns,stamp)
            cache.unlink()  # A new root receives the frozen seed and its recorded age.
            converge()
            self.assertEqual(cache.read_text(),'new controller seed')
            self.assertEqual(datetime.datetime.fromtimestamp(cache.stat().st_mtime,datetime.timezone.utc).isoformat(),
                             '2026-10-15T00:00:00+00:00')

    def test_real_ansible_converges_existing_account_without_changing_uid(self):
        import os, shutil, subprocess, tempfile, yaml
        if not shutil.which('ansible-playbook'): self.skipTest('controller Ansible is required')
        tasks=yaml.safe_load((ROOT/'ansible/roles/vpn-egress-runtime/tasks/main.yml').read_text())
        task=copy.deepcopy(next(t for t in tasks if t['name']=='Set the existing proxy account group home and shell'))
        task.pop('notify')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); database=root/'passwd'
            database.write_text('root:x:0:0:root:/root:/bin/ash\nvpn-egress:x:102:65533::/var/empty:/sbin/nologin\n')
            task['ansible.builtin.lineinfile']['path']=str(database)
            play=root/'play.yml';play.write_text(yaml.safe_dump([{'hosts':'localhost','connection':'local','gather_facts':False,
                'vars':{'ansible_facts':{'getent_group':{'vpn-egress':['x','103','']}}},'tasks':[task]}]))
            for expected_changed in (1,0):
                result=subprocess.run(['ansible-playbook','-i','localhost,',str(play)],text=True,capture_output=True,
                    timeout=60,env=dict(os.environ,ANSIBLE_NOCOLOR='1',ANSIBLE_STDOUT_CALLBACK='default'))
                self.assertEqual(result.returncode,0,result.stderr+result.stdout)
                self.assertIn('changed='+str(expected_changed),result.stdout)
                self.assertEqual(database.read_text(),'root:x:0:0:root:/root:/bin/ash\nvpn-egress:x:102:103::/var/lib/vpn-egress:/sbin/nologin\n')

    def test_real_ansible_resolves_the_frozen_role_from_an_unrelated_directory(self):
        import os, shutil, tempfile
        if not shutil.which('ansible-playbook'): self.skipTest('controller Ansible is required')
        with tempfile.TemporaryDirectory() as temporary:
            work=Path(temporary)
            ansible=work/'public/ansible'; (ansible/'bin').mkdir(parents=True)
            (ansible/'roles/frozen-fixture/tasks').mkdir(parents=True)
            (ansible/'roles/frozen-fixture/tasks/main.yml').write_text('- ansible.builtin.debug: {msg: frozen}\n')
            (ansible/'ansible.cfg').write_text('[defaults]\nroles_path = ./roles\n')
            play=work/'outside.yml'; play.write_text('- hosts: all\n  gather_facts: false\n  roles: [frozen-fixture]\n')
            (ansible/'playbooks').mkdir()
            (ansible/'playbooks/69-air-network.yml').write_text(play.read_text())
            (ansible/'execution-inventory').mkdir()
            (ansible/'execution-inventory/hosts').write_text('localhost-router ansible_connection=local\nlocalhost-ops ansible_connection=local\n')
            (work/'inventory').mkdir()
            (work/'inventory/hosts.json').write_text(json.dumps({'all':{'hosts':{host:{'ansible_connection':'local'} for host in ('localhost-router','localhost-ops')}}}))
            for existing in (None, '/absent/ansible.cfg'):
                with patch.dict(os.environ):
                    if existing is None: os.environ.pop('ANSIBLE_CONFIG',None)
                    else: os.environ['ANSIBLE_CONFIG']=existing
                    with patch.object(updates.vpn_egress_provisioning.compiler,'compile_registry',return_value={'registry_sha256':'a','compiler_version':'v'}):
                        execution.reconcile_vpn_network('localhost',work,{'registry': {'boxes': {'localhost': {}}}})
                    self.assertEqual(os.environ.get('ANSIBLE_CONFIG'),existing)

    def test_frozen_ansible_configuration_is_selected_and_environment_restored(self):
        import vm_service_updates as updates
        import os
        from pathlib import Path
        from unittest.mock import patch
        for existing in (None, '/other/ansible.cfg'):
            for fail in (False, True):
                environment={} if existing is None else {'ANSIBLE_CONFIG':existing}
                def network(box, view, *, repo, inventory):
                    self.assertEqual(os.environ['ANSIBLE_CONFIG'], '/frozen/public/ansible/ansible.cfg')
                    if fail: raise RuntimeError('fixture network failure')
                with self.subTest(existing=existing,fail=fail), patch.dict(os.environ,environment,clear=True), patch.object(updates.vpn_egress_provisioning,'network',side_effect=network):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError,'fixture network failure'):
                            execution.reconcile_vpn_network('boxa',Path('/frozen'),{})
                    else:
                        execution.reconcile_vpn_network('boxa',Path('/frozen'),{})
                    self.assertEqual(os.environ.get('ANSIBLE_CONFIG'),existing)

if __name__ == '__main__':
    unittest.main()
