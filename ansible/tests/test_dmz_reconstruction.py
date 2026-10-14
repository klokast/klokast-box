"""Shared guest persistent disk, first transition, health and retirement invariants."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from test_infrastructure_guest import load, ROOT
import test_vm_service_retirement as retirement_tests
import test_infrastructure_guest as guest_tests
import test_vm_service_updates as update_tests
import vm_service_retirement as retirement
import vm_service_probe as probe
import vm_service_updates as updates
import xen_build_runtime
import dmz_provisioning


class DMZRetirement(unittest.TestCase):
    role='dmz'
    def setUp(self):
        retirement_tests.Retirement.setUp(self)
        self.data='/dev/vg0/persistent-'+self.role+'-data'
        identity={'uuid':'stable-data-identity','bytes':100000}
        self.volumes[self.data]={'identity':identity,'tag':'data-ownership'}
        for work,record in self.records:
            record['role']=self.role
            for side in ('old_disks','new_disks'):record[side][self.data]=identity
            request=self.m.read(work/'request.json')
            request.update(role=self.role,old_disks=record['old_disks'],new_disks=record['new_disks'])
            self.t.store(work/'request.json',request);self.t.store(work/'service.json',record)

    def test_three_roots_retire_without_deleting_shared_data(self):
        proposal=retirement.retire(self.m,'boxa',True,role=self.role)
        self.assertEqual(len(proposal['items']),1)
        self.assertNotIn(self.data,[v['root_lv'] for v in proposal['items']])
        retirement.retire(self.m,'boxa',role=self.role)
        self.assertIn(self.data,self.volumes)
        self.assertEqual(len(self.volumes),3)
        self.assertTrue(retirement.released(self.m,*self.records[0]))
        self.assertEqual(retirement.retire(self.m,'boxa',role=self.role)['removed_disks'],[])

    def test_interruption_after_root_removal_preserves_data_and_replays(self):
        original=self.m.run
        def interrupt(argv):
            result=original(argv)
            if argv[0]=='lvremove':raise RuntimeError('lost removal reply')
            return result
        with patch.object(self.m,'run',side_effect=interrupt),self.assertRaises(RuntimeError):
            retirement.retire(self.m,'boxa',role=self.role)
        self.assertIn(self.data,self.volumes)
        self.assertTrue(retirement.retire(self.m,'boxa',role=self.role)['complete'])
        self.assertIn(self.data,self.volumes)


class DMZHealth(unittest.TestCase):
    role='dmz'
    def setUp(self):
        self.uuid='11111111-1111-4111-8111-111111111111'
        self.before={'identity':{'hostname':'boxa-'+self.role,'network':'old','firewall':'old'},
            'accounts':{'neo':[1000,1000]},'workloads':{'running':[],'containers':[],'images':[],'volumes':[]},
            'retained_uuid':self.uuid,'xen_uuid':'old'}
        storage={'kind':'klokast.'+self.role+'-storage.v1','box':'boxa','role':self.role,'filesystem_uuid':self.uuid}
        self.after=dict(copy.deepcopy(self.before),xen_uuid='new',persistent_storage=storage,
            application_writers=[],copy=None,kernel='new-kernel',packages_sha256='a'*64,
            kernel_modules=True,online=True,firewall=True,service='podman')
        self.after['identity'].update(network='new',firewall='new')
        self.record={'box':'boxa','role':self.role,'new_uuid':'new','data_uuid':self.uuid,
            'requested_configuration':{self.role+'_creation':{},'before':self.before,
                'image_evidence':{'kernel_release':'new-kernel','packages_sha256':'a'*64}}}

    def test_new_os_and_configuration_keep_same_persistent_store(self):
        self.assertTrue(probe.verify(self.before,self.after,self.record)['retained_state'])
        self.after['retained_uuid']='22222222-2222-4222-8222-222222222222'
        with self.assertRaisesRegex(RuntimeError,'retained storage'):probe.verify(self.before,self.after,self.record)

    def test_only_explicit_first_transition_discards_root_workloads(self):
        self.before['workloads']['containers']=['old-development-container']
        with self.assertRaisesRegex(RuntimeError,'workload records'):probe.verify(self.before,self.after,self.record)
        self.record['requested_configuration']['discard_root_data']=True
        probe.verify(self.before,self.after,self.record)

    def test_mount_evidence_and_application_writers_block_acceptance(self):
        for field,value in [('persistent_storage',None),('application_writers',[{'pid':'2','path':'/srv/retained/apps/data'}])]:
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                probe.verify(self.before,dict(self.after,**{field:value}),self.record)


class DMZConfiguration(unittest.TestCase):
    def test_absent_ssh_host_keys_preserve_only_recorded_dmz_identity(self):
        identity=load('dmz_guest_identity',ROOT/'ansible/roles/vm-template-builder/files/vm_guest_identity.py')
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source';target=Path(directory)/'target'
            (source/'var/lib/tailscale').mkdir(parents=True)
            (source/'var/lib/tailscale/tailscaled.state').write_text('synthetic state')
            target.mkdir()
            with patch.object(identity.os,'chown'):
                with self.assertRaisesRegex(RuntimeError,'required VM identity is absent'):
                    identity.preserve_identity(source,target)
                copied=identity.preserve_identity(source,target,require_ssh=False)
            self.assertEqual(set(copied),{'var/lib/tailscale/tailscaled.state'})
            self.assertEqual((target/'var/lib/tailscale/tailscaled.state').read_text(),'synthetic state')
            self.assertFalse((target/'etc/ssh/ssh_host_ed25519_key.pub').exists())
            (target/'etc/ssh').mkdir(parents=True)
            (target/'etc/ssh/ssh_host_ed25519_key.pub').write_text('unrecorded key')
            with self.assertRaisesRegex(RuntimeError,'unrecorded SSH host identity'):
                identity.preserve_identity(source,target,require_ssh=False)

    def test_bad_filesystem_identity_refuses_without_running_a_command(self):
        module=load('dmz_personalizer',ROOT/'ansible/roles/vm-template-builder/files/vm_dmz_personalize.py')
        for value in (None,'','/dev/xvda','../root'):
            with self.subTest(value=value),patch.object(module.base,'run') as run,self.assertRaises(RuntimeError):
                module.storage({'data_uuid':value},initialize=True)
            run.assert_not_called()

    def test_normal_cli_cannot_grant_first_transition_during_resume(self):
        with self.assertRaisesRegex(RuntimeError,'new BAK, DMZ or IoT replacement'):
            updates.execute('boxa','dmz',resume='a'*24,discard_root_data=True)
        with self.assertRaisesRegex(RuntimeError,'new BAK, DMZ or IoT replacement'):
            updates.execute('boxa','vpn-egress',discard_root_data=True)

    def test_blank_owned_data_supports_both_blkid_implementations(self):
        module=load('dmz_blank_personalizer',ROOT/'ansible/roles/vm-template-builder/files/vm_dmz_personalize.py')
        expected='11111111-1111-4111-8111-111111111111'
        for status in (0,2):
            with self.subTest(status=status), \
                 patch.object(module,'Path',return_value=SimpleNamespace(read_text=lambda:'0')), \
                 patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=status,stdout='')), \
                 patch.object(module.base,'run',side_effect=RuntimeError('format reached')) as run:
                with self.assertRaisesRegex(RuntimeError,'format reached'):
                    module.storage({'data_uuid':expected},initialize=True)
                run.assert_called_once_with(['mkfs.ext4','-F','-U',expected,'/dev/xvde'])

    def test_existing_filesystem_is_never_formatted_even_with_initialize(self):
        module=load('dmz_personalizer_uuid',ROOT/'ansible/roles/vm-template-builder/files/vm_dmz_personalize.py')
        expected='11111111-1111-4111-8111-111111111111'
        other='22222222-2222-4222-8222-222222222222'
        with patch.object(module,'Path',return_value=SimpleNamespace(read_text=lambda:'0')), \
             patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=other)), \
             patch.object(module.base,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'refusing to format'):
                module.storage({'data_uuid':expected},initialize=True)
            run.assert_not_called()


class DMZCreation(unittest.TestCase):
    role='dmz'
    def setUp(self):
        guest_tests.CloneTests.setUp(self)
        self.config.write_text(json.dumps({'box':'boxa','role':self.role,'bridge':'br-'+self.role,
            'data_uuid':'11111111-1111-4111-8111-111111111111'}))
        context=patch.object(xen_build_runtime,'domain',return_value=None);context.start();self.addCleanup(context.stop)
        self.volumes={};self.commands=[]
        def run(argv, **kwargs):
            self.commands.append(argv)
            if argv[0]=='lvcreate':
                name=argv[argv.index('--name')+1]
                self.volumes['/dev/vg0/'+name]={'lv_uuid':name+'-uuid','lv_tags':argv[argv.index('--addtag')+1]}
        for name,fn in [('lv_info',lambda p:self.volumes.get(str(p))),('run',run),
                        ('domain',lambda name:None),('loop_devices',lambda path:[])]:
            context=patch.object(self.module,name,side_effect=fn);context.start();self.addCleanup(context.stop)

    def provision(self):return self.module.provision('boxa',self.role,self.operation,self.config)

    def test_lost_allocation_reply_preserves_exact_data_on_resume(self):
        original=self.module.run
        def interrupt(argv,**kwargs):
            result=original(argv,**kwargs)
            if argv[0]=='lvcreate':raise RuntimeError('lost root allocation reply')
            return result
        with patch.object(self.module,'run',side_effect=interrupt),self.assertRaisesRegex(RuntimeError,'lost root'):
            self.provision()
        state=self.provision()['state']
        self.assertEqual(state['lv_uuid'],'lv_'+self.role+'-uuid')
        self.assertEqual(state['data_lv_uuid'],'lv_'+self.role+'_data-uuid')
        writes=[c for c in self.commands if c[0] in ('dd','lvcreate')]
        self.assertEqual(len([c for c in writes if c[0]=='lvcreate']),2)
        self.provision()
        self.assertEqual(writes,[c for c in self.commands if c[0] in ('dd','lvcreate')])

    def test_unknown_data_is_never_adopted_or_formatted(self):
        self.volumes['/dev/vg0/lv_'+self.role+'_data']={'lv_uuid':'foreign','lv_tags':''}
        with self.assertRaisesRegex(RuntimeError,'unowned shared guest data'):self.provision()
        self.assertEqual(self.commands,[])

    def test_completed_finalizer_reply_loss_does_not_reformat_or_reboot_finalizer(self):
        original=self.module.boot_guest
        def interrupt(*args,**kwargs):
            original(*args,**kwargs)
            path=args[0]/'result.slot';value=json.loads(path.read_text())
            value['kind']='klokast.infrastructure-finalize.v1';path.write_text(json.dumps(value))
            raise RuntimeError('lost finalizer reply')
        with patch.object(self.module,'boot_guest',side_effect=interrupt),self.assertRaisesRegex(RuntimeError,'lost finalizer'):
            self.provision()
        writes=[c for c in self.commands if c[0]=='dd']
        self.assertEqual(self.provision()['state']['stage'],'ready')
        self.assertEqual(self.boots,1)
        self.assertEqual(writes,[c for c in self.commands if c[0]=='dd'])


class DMZNativeFinalization(unittest.TestCase):
    role='dmz'
    def setUp(self):update_tests.NativePreparation.setUp(self)

    def test_replacement_attaches_data_in_place_without_copying_it(self):
        state={'operation_id':'a'*24,'data_lv':'/dev/vg0/retained','data_uuid':'11111111-1111-4111-8111-111111111111',
            'initialize_data':False,'requested_configuration':{'box':'boxa','role':self.role,'root_partition':'',
            'source_files':{},'accounts':{},self.role+'_creation':{},'before':{'identity':{'machine_id':None}}}}
        tx=SimpleNamespace(journal={'stage':'stopped'},guest=lambda:None)
        with patch.object(self.m.finalization,'resume_finalizer',return_value=False), \
             patch.object(self.m.finalization,'disk_order',return_value=(['/dev/old','/dev/vg0/retained'],['/dev/new','/dev/vg0/retained'])), \
             patch.object(self.m.finalization.t,'Transaction',return_value=tx),patch.object(self.m.finalization,'release_slots'), \
             patch.object(self.m.finalization,'domain',return_value=None),patch.object(self.m.finalization,'attach_loop',return_value='/dev/loop0'), \
             patch.object(self.m.finalization,'detach_loop'),patch.object(self.m.finalization,'boot_guest') as boot, \
             patch.object(self.m.finalization,'finalizer_result'),patch.object(self.m.finalization,'run') as run, \
             patch.object(self.m.finalization.t,'atomic',side_effect=lambda path,content:path.write_bytes(content)):
            self.m.finalization.finalize(self.root,state)
        run.assert_not_called()
        self.assertIn('phy:/dev/vg0/retained,xvde,w',(self.root/'finalize.cfg').read_text())
        self.assertEqual(boot.call_count,1)

    def test_incomplete_initial_creation_blocks_all_service_roles(self):
        creation={'kind':'klokast.infrastructure-assignment.v1','box':'boxa','role':self.role,'operation_id':'a'*24}
        with patch.object(self.m.t,'BASE',self.root/'updates'), \
             patch.object(self.m.vm_dmz_creation,'assignment',side_effect=lambda read,base,box,role:creation if role==self.role else None):
            self.assertEqual(self.m.pending('boxa'),[creation])
        self.assertEqual(updates.resume_command('boxa',creation),
            'ansible/bin/platform-update provision --box boxa --role '+self.role)


class IoTRetirement(DMZRetirement):
    role='iot'


class IoTHealth(DMZHealth):
    role='iot'


class IoTCreation(DMZCreation):
    role='iot'


class IoTNativeFinalization(DMZNativeFinalization):
    role='iot'


class SharedProvisioningDecisions(unittest.TestCase):
    def setUp(self):
        update_tests.ServiceDecisions.setUp(self)
        self.step.return_value={'pending':[],'installed':None,'assignment':None,'present':False}

    def test_update_plans_creation_for_absent_vm_without_building_or_probing(self):
        for role in ('bak','dmz','iot'):
            with self.subTest(role=role),patch.object(updates.images,'local_action') as image:
                result=updates.execute('boxa',role,dry_run=True)
                self.assertEqual((result['action'],result['role'],result['root_gib'],result['data_gib']),
                                 ('create',role,8,50))
                image.assert_not_called();self.probe.assert_not_called()

    def test_update_passes_one_selected_image_to_initial_iot_creation(self):
        with patch.object(dmz_provisioning,'provision',return_value={'created':True}) as create:
            self.assertTrue(updates.execute('boxa','iot',image='f'*24)['created'])
        create.assert_called_once_with('boxa',repo=updates.REPO,role='iot',dry_run=False,lock_held=True,image='f'*24)

    def test_absent_vm_does_not_accept_permission_to_discard_an_unknown_root(self):
        with patch.object(dmz_provisioning,'provision') as create,self.assertRaisesRegex(RuntimeError,'existing source root'):
            updates.execute('boxa','iot',discard_root_data=True)
        create.assert_not_called()

    def test_an_offline_assigned_iot_is_not_recreated(self):
        self.step.return_value['assignment']={'stage':'complete','runtime':'stopped'}
        with patch.object(dmz_provisioning,'provision') as create,self.assertRaisesRegex(RuntimeError,'reconcile'):
            updates.execute('boxa','iot')
        create.assert_not_called()

    def test_incomplete_creation_of_another_role_blocks_new_creation(self):
        self.step.return_value['pending']=[{'kind':'klokast.infrastructure-assignment.v1','role':'dmz'}]
        with patch.object(updates.images,'local_action') as image,self.assertRaisesRegex(RuntimeError,'--role dmz'):
            dmz_provisioning.provision('boxa',repo=ROOT,role='iot',lock_held=True)
        image.assert_not_called()

    def test_unowned_live_iot_requires_explicit_transition(self):
        self.step.return_value['present']=True
        with self.assertRaisesRegex(RuntimeError,'--discard-root-data'):
            dmz_provisioning.provision('boxa',repo=ROOT,role='iot',lock_held=True)

    def test_new_iot_freezes_its_own_creation_and_runtime_inputs(self):
        receipt={'files':{'release-evidence.json':{'kernel_release':'test','packages':{}}}}
        public={'box':'boxa','role':'iot'};values={'shared_guest_role':'iot'}
        with patch.object(updates.images,'local_action',return_value=receipt) as image, \
             patch.object(dmz_provisioning,'configuration',return_value=(public,values)), \
             patch.object(updates.inputs,'freeze',return_value=(Path('/frozen'),'f'*64)) as freeze, \
             patch.object(updates,'finish_frozen',return_value={'created':True}) as finish:
            dmz_provisioning.provision('boxa',repo=ROOT,role='iot',image='e'*24,lock_held=True)
        self.assertEqual(image.call_args.args[3],'image-receipt')
        variables=freeze.call_args.args[1]
        self.assertTrue(variables['service_configuration']['iot_initial'])
        self.assertEqual(variables['service_configuration']['iot_creation']['role'],'iot')
        self.assertEqual(variables['iot_runtime'],values)
        self.assertNotIn('dmz_runtime',variables)
        self.assertEqual(finish.call_args.args[1],'iot')

    def test_creation_replay_verifies_installed_guest_without_a_new_image(self):
        self.record['role']='iot';self.after['copy']['role']='iot'
        self.step.return_value['installed']=self.record
        with patch.object(updates.images,'local_action') as image:
            result=dmz_provisioning.provision('boxa',repo=ROOT,role='iot',lock_held=True)
        self.assertFalse(result['created']);image.assert_not_called()
        self.probe.assert_called_once_with('boxa','iot')

    def test_interrupted_iot_creation_recovers_frozen_inputs_without_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);token='d'*24;work=root/token;work.mkdir(mode=0o700)
            public={'box':'boxa','role':'iot'}
            config={'box':'boxa','role':'iot','iot_initial':True,'input_snapshot':token}
            variables={'service_configuration':config,'infrastructure_config':public,'iot_runtime':{}}
            manifest={'variables':variables};checksum=updates.inputs.checksum(manifest)
            (work/'manifest.json').write_text(json.dumps(manifest))
            creation={'kind':'klokast.infrastructure-assignment.v1','role':'iot','operation_id':'e'*24,
                'configuration':dict(public,iot_inputs={'input_snapshot':token,'inputs_sha256':checksum})}
            self.step.return_value.update(creation=creation,pending=[creation])
            with patch.object(updates,'ROOT',root),patch.object(updates.inputs,'recover',return_value=(work,variables)) as recover, \
                 patch.object(updates.images,'local_action') as image,patch.object(dmz_provisioning,'configuration') as configuration, \
                 patch.object(updates,'finish_frozen',return_value={'created':True}) as finish:
                dmz_provisioning.provision('boxa',repo=ROOT,role='iot',lock_held=True)
            image.assert_not_called();configuration.assert_not_called()
            self.assertEqual(recover.call_args.args[0]['inputs_sha256'],checksum)
            self.assertEqual(finish.call_args.args[1],'iot')
            self.assertEqual(finish.call_args.args[5],'e'*24)


class SharedFinalizerInputs(unittest.TestCase):
    def setUp(self):
        update_tests.FinalizerInputs.setUp(self)
        self.value['role']='iot'
        self.public={'kind':'klokast.infrastructure-config.v1','box':'boxa','role':'iot',
            'address':'192.168.150.10/24','gateway':'192.168.150.1','bridge':'br-iot',
            'bootstrap_source':'192.168.100.2','public_key':'ssh-ed25519 AAAATEST',
            'agent_uid':1004,'agent_gid':1004,'data_uuid':'11111111-1111-4111-8111-111111111111'}
        self.value.update(iot_creation=self.public,iot_storage={'uuid':self.public['data_uuid'],
            'initialize':False,'persistent_before':True},iot_machine_id_sha256=None)

    def test_iot_uses_its_own_bridge_storage_and_identity_inputs(self):
        self.assertEqual(self.m.validate(self.value),self.value)
        for field,value in [('bridge','br-dmz'),('role','dmz')]:
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                self.m.validate(dict(self.value,iot_creation=dict(self.public,**{field:value})))

    def test_mismatched_storage_and_frozen_pointer_refuse(self):
        other='22222222-2222-4222-8222-222222222222'
        with self.assertRaises(RuntimeError):
            self.m.validate(dict(self.value,iot_storage=dict(self.value['iot_storage'],uuid=other)))
        infrastructure=load('iot_infrastructure_validation',ROOT/'ansible/roles/vm-template-builder/files/vm_infrastructure_personalize.py')
        pointer={'input_snapshot':'a'*24,'inputs_sha256':'b'*64}
        infrastructure.validate(dict(self.public,iot_inputs=pointer))
        with self.assertRaises(RuntimeError):infrastructure.validate(dict(self.public,dmz_inputs=pointer))

    def test_storage_setup_uses_role_marker_and_checks_it_before_tailscale(self):
        module=load('iot_storage_setup',ROOT/'ansible/roles/vm-template-builder/files/vm_dmz_personalize.py')
        for role in ('bak','dmz','iot'):
            with self.subTest(role=role),tempfile.TemporaryDirectory() as directory:
                data=Path(directory)/'data';podman=Path(directory)/'podman'
                real_path=Path
                def path(name):
                    return SimpleNamespace(read_text=lambda:'0') if name=='/sys/class/block/xvde/ro' else real_path(name)
                with patch.object(module,'DATA',data),patch.object(module,'PODMAN',podman), \
                     patch.object(module,'Path',side_effect=path),patch.object(module.os,'chown'), \
                     patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=self.public['data_uuid'])), \
                     patch.object(module.base,'run') as run,patch.object(module.base,'put') as put:
                    evidence=module.storage(dict(self.public,role=role),initialize=False)
                self.assertEqual(evidence['kind'],'klokast.'+role+'-storage.v1')
                self.assertEqual(json.loads((data/('platform-'+role+'-storage.json')).read_text()),evidence)
                files={call.args[0]:call.args[1] for call in put.call_args_list}
                self.assertIn('rc_need="localmount nftables klokast-'+role+'-storage"',files['etc/conf.d/tailscale'])
                self.assertIn('--verify '+role,files['etc/init.d/klokast-'+role+'-storage'])
                self.assertIn('/srv/retained/podman /home/neo/.local/share/containers',files['etc/fstab'])
                self.assertFalse(any(call.args[0][0]=='mkfs.ext4' for call in run.call_args_list))


class SharedCreationAcceptance(unittest.TestCase):
    def test_reboot_acceptance_preserves_role_disk_and_identity_bindings(self):
        module=load('shared_creation_acceptance',ROOT/'ansible/roles/vm-service-update/files/vm_dmz_creation.py')
        for role in ('bak','dmz','iot'):
            with self.subTest(role=role),tempfile.TemporaryDirectory() as directory:
                base=Path(directory)/'updates'
                path=base.parent/'klokast-infrastructure'/role/'assignment.json';path.parent.mkdir(parents=True)
                root='/dev/vg0/lv_'+role;data=root+'_data';operation='a'*24
                state={'kind':'klokast.infrastructure-assignment.v1','box':'boxa','role':role,
                    'operation_id':operation,'stage':'ready','uuid':'guest-identity','lv_tag':'root-tag',
                    'lv_uuid':'root-identity','data_lv':data,'data_tag':'data-tag','data_lv_uuid':'data-identity',
                    'data_uuid':'11111111-1111-4111-8111-111111111111'}
                path.write_text(json.dumps(state))
                native=SimpleNamespace(device=lambda path:path,persist=lambda:None)
                m=SimpleNamespace(t=SimpleNamespace(BASE=base,Native=lambda:native),
                    read=lambda path:json.loads(path.read_text()),write=lambda path,value:path.write_text(json.dumps(value)),
                    owned=lambda path,tag:{'uuid':'root-identity' if tag=='root-tag' else 'data-identity'},
                    domain=lambda role:{'config':{'disks':[{'pdev_path':root},{'pdev_path':data}]}},
                    require_identity=lambda guest,uuid:None)
                before={'boot_id':'first','identity':{'hostname':'boxa-'+role},
                        'xen_uuid':state['uuid'],'retained_uuid':state['data_uuid']}
                module.evidence(m,'boxa',operation,{'phase':'before','probe':before},role=role)
                record={'box':'boxa','role':role,'operation_id':operation,'image':operation,
                    'new_uuid':state['uuid'],'data_uuid':state['data_uuid'],'requested_configuration':{'before':before}}
                after=dict(before,boot_id='second')
                with self.assertRaisesRegex(RuntimeError,'preserve identity'):
                    module.evidence(m,'boxa',operation,{'phase':'after','probe':before,'service':record},role=role)
                with self.assertRaisesRegex(RuntimeError,'acceptance record'):
                    module.evidence(m,'boxa',operation,{'phase':'after','probe':after,
                        'service':dict(record,role='vpn-egress')},role=role)
                accepted=module.evidence(m,'boxa',operation,{'phase':'after','probe':after,'service':record},role=role)
                self.assertEqual(accepted['reboot_verification']['status'],'complete')
                self.assertEqual(set(accepted['service']['new_disks']),{root,data})
                self.assertEqual(accepted['service']['role'],role)
