"""Baseline publication preserves source ownership across every write boundary."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from test_router_generations import generation
import router_baseline as baseline
import router_generations as generations
import router_records as records


class BaselinePublication(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.base=self.root/'klokast-router-updates';self.base.mkdir(mode=0o700)
        for name in ('records','generations','operations'):(self.base/name).mkdir(mode=0o700)
        for name,value in (('ROOT_UID',os.geteuid()),('parents',lambda path:None)):
            p=patch.object(records,name,value);p.start();self.addCleanup(p.stop)
        self.store=records.Records('boxa',self.base)
        self.xen=self.root/'xen';(self.xen/'auto').mkdir(parents=True)
        old=generation('legacy');old.pop('record_sha256')
        old['xen']['vif']=['bridge=br-'+name+',mac=00:16:3e:00:00:0'+str(i)
            for i,name in enumerate(('wan','lan','dmz','bak','iot','usr'),1)]
        for name,item in old['boot'].items():item['path']='/mnt/dom0_data/xen_images/router-'+name
        self.generation=generations.seal(old)
        self.boot=baseline.native.literal_configuration(generations.configuration(self.generation));self.boot.pop('uuid')
        self.write_boot(self.boot)
        (self.xen/'auto/router.cfg').symlink_to('../router.cfg')
        self.topology_path=self.root/'ops/router-network.json';self.topology_path.parent.mkdir()
        self.topology={'box':'boxa','stage':'accepted','accepted_before':None,'accepted_after':None,
            'boot_before':dict(self.boot,vif=self.boot['vif']+['bridge=br-ops,mac=00:16:3e:00:00:07']),
            'boot_after':self.boot,'live_uuid':old['xen']['uuid']}
        records.write(self.topology_path,self.topology)
        self.uplink=['bridge=br-ops,mac=00:16:3e:00:00:08','bridge=br-wan,mac=00:16:3e:00:00:09']
        records.write(self.topology_path.parent/'network.json',dict(box='boxa',stage='accepted',boot_after={'vif':self.uplink}))
        self.domains=[{'config':{'c_info':{'name':'router','uuid':old['xen']['uuid']}}},
                      {'config':{'c_info':{'name':'ops'},'nics':[
                          dict(part.split('=') for part in value.split(',')) for value in self.uplink]}}]
        self.host=SimpleNamespace(monotonic=lambda:0,guard=lambda *a,**k:None,inventory=lambda **k:self.domains,
            disk=lambda *a,**k:None,artifact=lambda *a,**k:None,guest=lambda *a,**k:('baseline',self.domains[0]))
        self.commands=[]
        def command(argv,*a,**kw):
            self.commands.append(argv)
            if argv[0]=='/sbin/lvs':return json.dumps({'report':[{'lv':[{'lv_path':old['disk']['path'],
                'lv_uuid':old['disk']['uuid'],'lv_size':str(old['disk']['bytes'])}]}]})
            self.assertEqual(argv,['/usr/sbin/lbu','commit','-d']);return ''
        p=patch.object(baseline.native,'command',side_effect=command);p.start();self.addCleanup(p.stop)
        p=patch.object(baseline,'boot_artifact',side_effect=lambda path:next(copy.deepcopy(v) for v in old['boot'].values() if v['path']==path))
        p.start();self.addCleanup(p.stop)
        observed,_=self.observe()
        guest=dict(identity={'machine_id':None,'machine_key':'f'*64},router_accounts=old['accounts'],
            root_partition='3',persistent_storage=None,xen_uuid=old['xen']['uuid'],kernel=old['kernel_release'],
            packages=old['packages'],alpine_branch=old['alpine_branch'],configuration_files=old['configuration_files'])
        self.value=baseline.proposal('boxa','a'*24,'c'*40,'d'*40,observed,guest)

    def write_boot(self,value):
        records.atomic(self.xen/'router.cfg',('\n'.join(k+' = '+repr(v) for k,v in value.items())+'\n').encode())

    def observe(self):return baseline.observe(self.store,self.topology_path,xen=self.xen,host=self.host)
    def publish(self,**kw):return baseline.publish(self.store,self.topology_path,self.value,xen=self.xen,host=self.host,**kw)
    def recover(self):baseline.recover_boot(self.store,self.topology_path,xen=self.xen,host=self.host)

    def test_publication_and_replay_preserve_original_evidence_and_disk(self):
        self.assertEqual(self.publish()['state'],'complete')
        before={str(p):p.read_bytes() for p in self.base.rglob('*.json')}
        self.assertEqual(self.publish()['state'],'complete')
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.base.rglob('*.json')})
        saved=records.read(self.base/'baseline.json')
        self.assertEqual(saved['observation']['topology'],self.topology)
        self.assertEqual(self.store.accepted(),self.value['accepted'])
        boot=baseline.native.literal_configuration((self.xen/'router.cfg').read_text())
        self.assertEqual(boot,dict(self.boot,uuid=self.generation['xen']['uuid']))
        self.assertTrue(all(c[0] in ('/sbin/lvs','/usr/sbin/lbu') for c in self.commands))

    def test_reboot_recovery_and_retry_after_each_publication_write(self):
        for boundary in ('prepared','generation','topology','boot','accepted'):
            with self.subTest(boundary=boundary):
                # A fresh fixture isolates each crash from the previous one.
                fixture=BaselinePublication();fixture.setUp()
                try:
                    def interrupt(stage):
                        if stage==boundary:raise OSError('power loss')
                    with self.assertRaisesRegex(OSError,'power loss'):fixture.publish(checkpoint=interrupt)
                    fixture.host.guest=lambda *a,**k:None
                    fixture.recover()
                    self.assertEqual(baseline.native.literal_configuration((fixture.xen/'router.cfg').read_text())['uuid'],fixture.generation['xen']['uuid'])
                    fixture.host.guest=lambda *a,**k:('baseline',fixture.domains[0])
                    self.assertEqual(fixture.publish()['state'],'complete')
                finally:fixture.doCleanups()

    def test_changed_source_or_topology_refuses_without_assignment(self):
        for change in ('uuid','boot','uplink','disk'):
            with self.subTest(change=change):
                if change=='uuid':self.domains[0]['config']['c_info']['uuid']='different'
                elif change=='boot':self.write_boot(dict(self.boot,memory=1024))
                elif change=='uplink':self.domains[1]['config']['nics'].pop()
                else:
                    self.value['observation']['disk']['uuid']='substituted'
                    self.value['record_sha256']=generations.digest({k:v for k,v in self.value.items() if k!='record_sha256'})
                with self.assertRaises((RuntimeError,ValueError)):self.publish()
                self.assertFalse((self.base/'accepted.json').exists())
                if change=='uuid':self.domains[0]['config']['c_info']['uuid']=self.generation['xen']['uuid']
                elif change=='boot':self.write_boot(self.boot)
                elif change=='uplink':self.domains[1]['config']['nics'].append(dict(bridge='br-wan',mac='00:16:3e:00:00:09'))

    def test_conflicts_fence_recovery_and_preserve_original_assignment(self):
        self.publish()
        alien=copy.deepcopy(self.value['accepted']);alien['operation_id']='f'*24
        alien.pop('record_sha256');alien=generations.seal(alien)
        records.write(self.base/'accepted.json',alien)
        original=(self.xen/'router.cfg').read_bytes()
        with self.assertRaisesRegex(RuntimeError,'conflicting'):self.recover()
        with self.assertRaisesRegex(RuntimeError,'conflicting'):self.publish()
        self.assertEqual(records.read(self.base/'accepted.json'),alien)
        self.assertEqual((self.xen/'router.cfg').read_bytes(),original)

    def test_pending_operations_common_owner_and_unrelated_assignment_refuse(self):
        for name in ('pending.json','cold-test.json','installation.json'):
            records.write(self.base/name,{'unknown':'operation'})
            with self.assertRaisesRegex(RuntimeError,'finish'):self.observe()
            (self.base/name).unlink()
        active=self.root/'klokast-vm-updates/active';active.mkdir(parents=True)
        (active/'router.json').symlink_to(active/'missing')
        with self.assertRaisesRegex(RuntimeError,'common'):self.observe()
        (active/'router.json').unlink()
        self.store.adopt(self.value['generation'])
        with self.assertRaisesRegex(RuntimeError,'already has'):self.observe()

    def test_historical_topology_reading_and_partial_baseline_binding(self):
        self.assertNotIn('baseline_sha256',self.topology)
        after=baseline.topology_after(self.value)
        for assignment in (None,self.value['accepted']):
            self.assertEqual(baseline.recovery_selection(after,self.value,assignment),after['boot_after'])
        with self.assertRaisesRegex(RuntimeError,'conflicts'):
            baseline.recovery_selection(dict(after,baseline_sha256='0'*64),self.value,None)
        corrupt=copy.deepcopy(self.value);corrupt['observation']['disk']['uuid']='other'
        with self.assertRaises(RuntimeError):baseline.recovery_selection(after,corrupt,None)


if __name__=='__main__':unittest.main()
