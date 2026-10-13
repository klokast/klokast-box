"""DMZ persistent disk, first transition, health and retirement invariants."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
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


class DMZRetirement(unittest.TestCase):
    def setUp(self):
        retirement_tests.Retirement.setUp(self)
        self.data='/dev/vg0/persistent-dmz-data'
        identity={'uuid':'stable-data-identity','bytes':100000}
        self.volumes[self.data]={'identity':identity,'tag':'data-ownership'}
        for work,record in self.records:
            record['role']='dmz'
            for side in ('old_disks','new_disks'):record[side][self.data]=identity
            request=self.m.read(work/'request.json')
            request.update(role='dmz',old_disks=record['old_disks'],new_disks=record['new_disks'])
            self.t.store(work/'request.json',request);self.t.store(work/'service.json',record)

    def test_three_roots_retire_without_deleting_shared_data(self):
        proposal=retirement.retire(self.m,'boxa',True,role='dmz')
        self.assertEqual(len(proposal['items']),1)
        self.assertNotIn(self.data,[v['root_lv'] for v in proposal['items']])
        retirement.retire(self.m,'boxa',role='dmz')
        self.assertIn(self.data,self.volumes)
        self.assertEqual(len(self.volumes),3)
        self.assertTrue(retirement.released(self.m,*self.records[0]))
        self.assertEqual(retirement.retire(self.m,'boxa',role='dmz')['removed_disks'],[])

    def test_interruption_after_root_removal_preserves_data_and_replays(self):
        original=self.m.run
        def interrupt(argv):
            result=original(argv)
            if argv[0]=='lvremove':raise RuntimeError('lost removal reply')
            return result
        with patch.object(self.m,'run',side_effect=interrupt),self.assertRaises(RuntimeError):
            retirement.retire(self.m,'boxa',role='dmz')
        self.assertIn(self.data,self.volumes)
        self.assertTrue(retirement.retire(self.m,'boxa',role='dmz')['complete'])
        self.assertIn(self.data,self.volumes)


class DMZHealth(unittest.TestCase):
    def setUp(self):
        self.uuid='11111111-1111-4111-8111-111111111111'
        self.before={'identity':{'hostname':'boxa-dmz','network':'old','firewall':'old'},
            'accounts':{'neo':[1000,1000]},'workloads':{'running':[],'containers':[],'images':[],'volumes':[]},
            'retained_uuid':self.uuid,'xen_uuid':'old'}
        storage={'kind':'klokast.dmz-storage.v1','box':'boxa','role':'dmz','filesystem_uuid':self.uuid}
        self.after=dict(copy.deepcopy(self.before),xen_uuid='new',persistent_storage=storage,
            application_writers=[],copy=None,kernel='new-kernel',packages_sha256='a'*64,
            kernel_modules=True,online=True,firewall=True,service='podman')
        self.after['identity'].update(network='new',firewall='new')
        self.record={'box':'boxa','role':'dmz','new_uuid':'new','data_uuid':self.uuid,
            'requested_configuration':{'dmz_creation':{},'before':self.before,
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
    def test_bad_filesystem_identity_refuses_without_running_a_command(self):
        module=load('dmz_personalizer',ROOT/'ansible/roles/vm-template-builder/files/vm_dmz_personalize.py')
        for value in (None,'','/dev/xvda','../root'):
            with self.subTest(value=value),patch.object(module.base,'run') as run,self.assertRaises(RuntimeError):
                module.storage({'data_uuid':value},initialize=True)
            run.assert_not_called()

    def test_normal_cli_cannot_grant_first_transition_during_resume(self):
        with self.assertRaisesRegex(RuntimeError,'new DMZ replacement'):
            updates.execute('boxa','dmz',resume='a'*24,discard_root_data=True)
        with self.assertRaisesRegex(RuntimeError,'new DMZ replacement'):
            updates.execute('boxa','bak',discard_root_data=True)

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
    def setUp(self):
        guest_tests.CloneTests.setUp(self)
        self.config.write_text(json.dumps({'box':'boxa','role':'dmz','bridge':'br-dmz',
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

    def provision(self):return self.module.provision('boxa','dmz',self.operation,self.config)

    def test_lost_allocation_reply_preserves_exact_data_on_resume(self):
        original=self.module.run
        def interrupt(argv,**kwargs):
            result=original(argv,**kwargs)
            if argv[0]=='lvcreate':raise RuntimeError('lost root allocation reply')
            return result
        with patch.object(self.module,'run',side_effect=interrupt),self.assertRaisesRegex(RuntimeError,'lost root'):
            self.provision()
        state=self.provision()['state']
        self.assertEqual(state['lv_uuid'],'lv_dmz-uuid')
        self.assertEqual(state['data_lv_uuid'],'lv_dmz_data-uuid')
        writes=[c for c in self.commands if c[0] in ('dd','lvcreate')]
        self.assertEqual(len([c for c in writes if c[0]=='lvcreate']),2)
        self.provision()
        self.assertEqual(writes,[c for c in self.commands if c[0] in ('dd','lvcreate')])

    def test_unknown_data_is_never_adopted_or_formatted(self):
        self.volumes['/dev/vg0/lv_dmz_data']={'lv_uuid':'foreign','lv_tags':''}
        with self.assertRaisesRegex(RuntimeError,'unowned DMZ data'):self.provision()
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
    def setUp(self):update_tests.NativePreparation.setUp(self)

    def test_replacement_attaches_data_in_place_without_copying_it(self):
        state={'operation_id':'a'*24,'data_lv':'/dev/vg0/retained','data_uuid':'11111111-1111-4111-8111-111111111111',
            'initialize_data':False,'requested_configuration':{'box':'boxa','role':'dmz','root_partition':'',
            'source_files':{},'accounts':{},'dmz_creation':{},'before':{'identity':{'machine_id':None}}}}
        tx=SimpleNamespace(journal={'stage':'stopped'},guest=lambda:None)
        with patch.object(self.m,'resume_finalizer',return_value=False), \
             patch.object(self.m,'disk_order',return_value=(['/dev/old','/dev/vg0/retained'],['/dev/new','/dev/vg0/retained'])), \
             patch.object(self.m.t,'Transaction',return_value=tx),patch.object(self.m,'release_slots'), \
             patch.object(self.m,'domain',return_value=None),patch.object(self.m,'attach_loop',return_value='/dev/loop0'), \
             patch.object(self.m,'detach_loop'),patch.object(self.m,'boot_guest') as boot, \
             patch.object(self.m,'finalizer_result'),patch.object(self.m,'run') as run, \
             patch.object(self.m.t,'atomic',side_effect=lambda path,content:path.write_bytes(content)):
            self.m.finalize(self.root,state)
        run.assert_not_called()
        self.assertIn('phy:/dev/vg0/retained,xvde,w',(self.root/'finalize.cfg').read_text())
        self.assertEqual(boot.call_count,1)

    def test_incomplete_initial_creation_blocks_all_service_roles(self):
        creation={'kind':'klokast.infrastructure-assignment.v1','box':'boxa','role':'dmz','operation_id':'a'*24}
        with patch.object(self.m.t,'BASE',self.root/'updates'), \
             patch.object(self.m.vm_dmz_creation,'assignment',return_value=creation):
            self.assertEqual(self.m.pending('boxa'),[creation])
        self.assertEqual(updates.resume_command('boxa',creation),
            'ansible/bin/platform-update provision --box boxa --role dmz')
