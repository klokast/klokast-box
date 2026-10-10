"""Service replacement decisions and evidence, without live infrastructure."""
import contextlib
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from test_infrastructure_guest import load, ROOT
import vm_service_probe as probe
import vm_service_updates as updates


class ServiceDecisions(unittest.TestCase):
    def setUp(self):
        self.before = {'identity':{'tailscale':'retained'},'accounts':{'neo':[1000,1000]},'workloads':{'containers':['kept']},
                       'kernel':'6.test','packages_sha256':'f'*64,'retained_uuid':'data','kernel_modules':True,'online':True,'firewall':True,'service':'podman'}
        self.record = {'box':'boxa','role':'bak','image':'a'*24,'operation_id':'b'*24,'new_uuid':'new',
                       'requested_configuration':{'engine_commit':'old','instance_commit':'old','before':self.before,'source_files':{},'image_evidence':{'kernel_release':'6.test','packages_sha256':'f'*64}}}
        self.after = dict(self.before,xen_uuid='new',copy={'kind':'klokast.vm-service-state.v1','box':'boxa','role':'bak','copy_verified':True,'source_files':{}})
        for target,name,value in ((updates,'authority','running'),(updates.inputs,'revision','c'*40),
                (updates,'step',{'pending':[],'installed':self.record}),(probe,'probe',self.after)):
            p=patch.object(target,name,return_value=value);m=p.start();self.addCleanup(p.stop);setattr(self,name,m)
        p=patch.object(updates.runtime,'vm_update_installation_lock',side_effect=contextlib.nullcontext);p.start();self.addCleanup(p.stop)

    def test_unchanged_image_and_changed_sources_never_replace(self):
        result=updates.execute('boxa','bak',image='a'*24)
        self.assertFalse(result['replaced']);self.assertTrue(result['configuration_update_needed'])
        self.assertEqual([c.args[2] for c in self.step.call_args_list],['status'])

    def test_accepted_replay_needs_no_old_source_snapshot(self):
        with patch.object(updates.inputs,'recover',side_effect=AssertionError('historical input should not execute')):
            self.assertFalse(updates.execute('boxa','bak',resume='b'*24)['replaced'])

    def test_unchanged_image_still_refuses_boot_assignment_drift(self):
        self.step.return_value={'pending':[],'installed':self.record,'assignment':{'stage':'complete','configuration_drift':True,'runtime':'running'}}
        with self.assertRaisesRegex(RuntimeError,'boot assignment'):updates.execute('boxa','bak',image='a'*24)
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

    def test_dry_plan_does_not_build_or_probe_workloads(self):
        with patch.object(updates.images,'local_action') as prepare:
            self.assertEqual(updates.execute('boxa','bak',dry_run=True)['state'],'planned')
            prepare.assert_not_called();self.probe.assert_not_called()

    def test_data_and_identity_changes_refuse_acceptance(self):
        for key,value in [('identity',{}),('workloads',{}),('retained_uuid','other'),('accounts',{}),('xen_uuid','old'),('copy',{}),('firewall',False),('kernel','wrong'),('packages_sha256','wrong')]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                probe.verify(self.before,dict(self.after,**{key:value}),self.record)

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

    def test_vpn_retains_an_existing_shared_nogroup_gid(self):
        import tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new'
            (old/'etc').mkdir(parents=True);(new/'etc').mkdir(parents=True)
            for base in (old,new):
                (base/'etc/passwd').write_text('neo:x:1000:1000::/home/neo:/bin/ash\nvpn-egress:x:102:65533::/var/empty:/sbin/nologin\n')
            (old/'etc/group').write_text('neo:x:1000:\nnogroup:x:65533:\n')
            (old/'etc/shadow').write_text('neo:!:0:0:99999:7:::\nvpn-egress:!:0:0:99999:7:::\n')
            (new/'etc/shadow').write_text('root:!:0:0:99999:7:::\nneo:!:0:0:99999:7:::\nvpn-egress:!:0:0:99999:7:::\n')
            def user(uid):
                if uid==102: raise KeyError(uid)
                return SimpleNamespace(pw_name='neo',pw_gid=1000)
            with patch.object(self.m,'OLD',old),patch.object(self.m,'Path',lambda value:new/str(value).lstrip('/')), \
                 patch.object(self.m.pwd,'getpwuid',side_effect=user), \
                 patch.object(self.m.grp,'getgrgid',side_effect=lambda gid:SimpleNamespace(gr_name='nogroup' if gid==65533 else 'neo')), \
                 patch.object(self.m.grp,'getgrnam',return_value=SimpleNamespace(gr_mem=['neo'])), \
                 patch.object(self.m,'run') as run:
                self.m.accounts({'accounts':{'neo':[1000,1000],'vpn-egress':[102,65533]}})
            run.assert_called_once_with(['adduser','-D','-u','102','-G','nogroup','-s','/sbin/nologin','vpn-egress'])

    def test_missing_identity_or_unknown_mount_layout_refuses(self):
        for key,value in [('source_files',{}),('accounts',{'neo':[1001,1001]}),('root_partition','../../sda'),('role','ops')]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.m.validate(dict(self.value,**{key:value}))


class NativePreparation(unittest.TestCase):
    def setUp(self):
        import shutil, tempfile
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        shutil.copyfile(ROOT/'ansible/roles/vm-service-update/files/vm-service-update',self.root/'vm-service-update')
        shutil.copyfile(ROOT/'ansible/roles/vm-update-recovery/files/vm-update-transaction',self.root/'vm-update-transaction')
        self.m=load('native_service_test',self.root/'vm-service-update')

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
        self.assertEqual(prepare.call_count,2)
        self.assertEqual([c.args[1] for c in execute.call_args_list],['bak','dmz','vpn-egress'])
        self.assertEqual(result[2]['state'],'skipped')

    def test_service_failure_marks_the_whole_nightly_run_failed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary, patch.object(self.nightly,'STATE',Path(temporary)), \
             patch.object(self.nightly,'run',return_value={'state':'complete'}), \
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

if __name__=='__main__':unittest.main()
