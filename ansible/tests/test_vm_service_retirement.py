"""Generation retention, interrupted deletion and image reference release."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_infrastructure_guest import ROOT
import vm_service_retirement as retirement
from platform_updates import digest


class Retirement(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name)/'updates';(self.base/'operations').mkdir(parents=True)
        self.volumes={};self.calls=[];self.foreign=set();self.live=set()
        def read(path):return json.loads(path.read_text())
        def store(path,value):path.write_text(json.dumps(value,sort_keys=True))
        def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
        def owned(path,expected=None,tag=None):
            row=self.volumes[path]
            if path in self.foreign or (expected and row['identity']!=expected) or (tag and row['tag']!=tag):
                raise RuntimeError('LV UUID or ownership differs')
            return row['identity']
        def run(argv):
            self.calls.append(argv)
            if argv[:4]==['lvs','--noheadings','-o','lv_tags']:
                return SimpleNamespace(stdout=self.volumes[argv[-1]]['tag'])
            if argv[0]=='lvs':return SimpleNamespace(stdout=json.dumps({'report':[{'lv':[{'lv_path':path,'lv_uuid':row['identity']['uuid']} for path,row in self.volumes.items()]}]}))
            if argv[0]=='lvremove':self.volumes.pop(argv[-1]);return SimpleNamespace(stdout='')
            return SimpleNamespace(stdout='')
        self.t=SimpleNamespace(BASE=self.base,XEN=self.base/'xen',digest=digest,
            HEX=__import__('re').compile('[0-9a-f]{64}'),secure=lambda path,*args,**kw:path,store=store,syncdir=lambda p:None)
        self.m=SimpleNamespace(t=self.t,read=read,owned=owned,checksum=sha,run=run,MIB=1024**2)
        previous={'uuid':'initial','disks':{'/dev/vg0/lv_vpn-egress':{'uuid':'initial-lv','bytes':4096}}}
        self.records=[]
        for char in 'abc':
            operation=char*24;work=self.base/'operations'/operation;work.mkdir()
            root='/dev/vg0/vmupd_'+operation+'_root';identity={'uuid':char+'-lv','bytes':4096}
            self.volumes[root]={'identity':identity,'tag':'klokast.vm-update.'+operation+'.root'}
            record={'kind':'klokast.vm-service-update.v1','box':'boxa','role':'vpn-egress','operation_id':operation,'image':char*24,
                    'old_uuid':previous['uuid'],'old_disks':previous['disks'],'new_uuid':char+'-uuid','new_disks':{root:identity},
                    'reboot_verification':{'status':'complete'}}
            for name in ('kernel','initramfs','new.cfg'):(work/name).write_text(char+name)
            request=dict(record,new_config_sha256=sha(work/'new.cfg'),new_artifacts={str(work/name):{'sha256':sha(work/name)} for name in ('kernel','initramfs')})
            store(work/'service.json',record);store(work/'request.json',request);store(work/'journal.json',{'stage':'complete'})
            self.records.append((work,record));previous={'uuid':record['new_uuid'],'disks':record['new_disks']}
        self.assignment={'stage':'complete','operation_id':'c'*24,'configuration_drift':False,'autostart_drift':False,'runtime':'running'}
        self.m.status=lambda box,role,**kwargs:{'assignment':dict(self.assignment),'pending':[],'installed':self.records[-1][1]}
        def references(m,item):
            if item['root_lv'] in self.live:raise RuntimeError('live reference')
        p=patch.object(retirement.ops_retirement,'no_references',side_effect=references);p.start();self.addCleanup(p.stop)
        actual_path=Path
        fixture=self
        class LV:
            def __init__(self,name):self.name=name
            def exists(self):return self.name in fixture.volumes
            def is_symlink(self):return False
            def __str__(self):return self.name
        def paths(value):return LV(value) if isinstance(value,str) and value.startswith('/dev/vg0/') else actual_path(value)
        p=patch.object(retirement,'Path',side_effect=paths);p.start();self.addCleanup(p.stop)
        p=patch.object(retirement.ops_retirement,'persist');p.start();self.addCleanup(p.stop)

    def test_three_generations_keep_current_and_previous_and_release_old_reference(self):
        proposed=retirement.retire(self.m,'boxa',True)
        self.assertEqual([v['root_lv'] for v in proposed['items']],[next(iter(self.records[0][1]['new_disks']))])
        self.assertEqual(len(self.volumes),3)
        result=retirement.retire(self.m,'boxa')
        self.assertTrue(result['complete']);self.assertEqual(len(self.volumes),2)
        work,record=self.records[0]
        self.assertTrue(retirement.released(self.m,work,record))
        self.assertTrue((work/'service.json').exists());self.assertTrue((work/'request.json').exists())
        self.assertFalse(retirement.retire(self.m,'boxa')['removed_disks'])

    def test_router_retirement_keeps_shared_data_history_and_current_two_roots(self):
        data = '/dev/vg0/vmupd_'+'a'*24+'_data'
        identity = {'uuid':'router-data','bytes':1024**3}
        self.volumes[data] = {'identity':identity,'tag':'klokast.vm-update.'+'a'*24+'.data'}
        legacy = self.base.parent/'klokast-router-updates';legacy.mkdir()
        (legacy/'accepted.json').write_text('historical router assignment and identity')
        for index,(work,record) in enumerate(self.records):
            record['role']='router';record['new_disks'][data]=identity
            if index:record['old_disks'][data]=identity
            request=self.m.read(work/'request.json')
            request.update(role='router',old_disks=record['old_disks'],new_disks=record['new_disks'],retained_disks={data:identity})
            self.t.store(work/'service.json',record);self.t.store(work/'request.json',request)
        result=retirement.retire(self.m,'boxa',role='router')
        self.assertEqual(result['removed_disks'],['/dev/vg0/vmupd_'+'a'*24+'_root'])
        self.assertIn(data,self.volumes);self.assertEqual(len(self.volumes),3)
        self.assertEqual((legacy/'accepted.json').read_text(),'historical router assignment and identity')
        self.assertTrue(retirement.released(self.m,*self.records[0]))
        self.assertTrue(all((work/'service.json').exists() for work,_ in self.records))

    def test_infrastructure_retirement_preserves_the_exact_data_volume(self):
        for role in ('ops','air'):
            with self.subTest(role=role):
                f=Retirement();f.setUp()
                try:
                    data='/dev/vg0/lv_'+role+'_data';identity={'uuid':'retained','bytes':50*1024**3}
                    f.volumes[data]={'identity':identity,'tag':'retained'}
                    for index,(work,record) in enumerate(f.records):
                        record['role']=role;record['new_disks'][data]=identity
                        if index:record['old_disks'][data]=identity
                        request=f.m.read(work/'request.json')
                        request.update(role=role,old_disks=record['old_disks'],new_disks=record['new_disks'],retained_disks={data:identity})
                        f.t.store(work/'service.json',record);f.t.store(work/'request.json',request)
                    result=retirement.retire(f.m,'boxa',role=role)
                    retired = 'ba' if role == 'ops' else 'a'
                    self.assertEqual(result['removed_disks'],['/dev/vg0/vmupd_'+c*24+'_root' for c in retired])
                    self.assertEqual(f.volumes[data]['identity'],identity)
                    self.assertEqual(len(f.volumes), 2 if role == 'ops' else 3)
                    self.assertTrue(retirement.released(f.m,*f.records[0]))
                finally:f.doCleanups()

    def test_disk_deletion_and_marker_publication_resume_from_the_same_plan(self):
        original=self.m.run
        def interrupt(argv):
            result=original(argv)
            if argv[0]=='lvremove':raise RuntimeError('lost delete reply')
            return result
        proposed=retirement.retire(self.m,'boxa',True)
        with patch.object(self.m,'run',side_effect=interrupt),self.assertRaisesRegex(RuntimeError,'lost delete'):
            retirement.retire(self.m,'boxa')
        self.assertEqual(len(self.volumes),2)
        original_store=self.t.store
        def publish(path,value):
            original_store(path,value)
            if path.name=='retired.json':raise RuntimeError('lost publication reply')
        with patch.object(self.t,'store',side_effect=publish),self.assertRaisesRegex(RuntimeError,'publication'):
            retirement.retire(self.m,'boxa')
        self.assertEqual(retirement.retire(self.m,'boxa')['plan_sha256'],proposed['plan_sha256'])

    def original_controller(self, *, adopted=False):
        work,record=self.records[0]
        root='/dev/vg0/lv_ops';data='/dev/vg0/lv_ops_data'
        identity={'uuid':'data','bytes':50*1024**3}
        self.volumes[root]={'identity':{'uuid':'initial-lv','bytes':100*1024**3},'tag':'original-ops'}
        self.volumes[data]={'identity':identity,'tag':'retained'}
        original=self.base.parent/'klokast-infrastructure/ops';original.mkdir(parents=True)
        for name in ('kernel','initramfs','ops.cfg'):(original/name).write_text(name)
        assignment={'stage':'ready','role':'ops','uuid':'initial',
            'lv_uuid':'initial-lv','root_lv':root,'lv_tag':'original-ops',
            'artifacts':{name:{'sha256':self.m.checksum(original/name)} for name in ('kernel','initramfs')}}
        if adopted:
            self.volumes[root]['tag']=''
            assignment.pop('lv_tag')
            assignment.update(origin='legacy-adoption',legacy_config_sha256='f'*64)
        self.t.store(original/'assignment.json',assignment)
        record.update(role='ops',old_disks={root:self.volumes[root]['identity'],data:identity})
        record['new_disks'][data]=identity
        request=self.m.read(work/'request.json')
        request.update(role='ops',old_disks=record['old_disks'],new_disks=record['new_disks'],
            retained_disks={data:identity},old_config_sha256=self.m.checksum(original/'ops.cfg'))
        self.t.store(work/'service.json',record);self.t.store(work/'request.json',request)
        self.m.status=lambda *args,**kwargs:{'assignment':dict(self.assignment),'pending':[],'installed':record}
        return root,data,identity,record,original

    def test_first_controller_acceptance_retires_its_original_root_only(self):
        root,data,identity,record,_=self.original_controller()
        result=retirement.retire(self.m,'boxa',role='ops')
        self.assertEqual(result['removed_disks'],[root])
        self.assertEqual(self.volumes[data]['identity'],identity)
        self.assertIn(next(p for p in record['new_disks'] if p!=data),self.volumes)

    def test_adopted_controller_root_retires_with_its_protected_evidence(self):
        root,data,identity,_,_=self.original_controller(adopted=True)
        result=retirement.retire(self.m,'boxa',role='ops')
        self.assertEqual(result['removed_disks'],[root])
        self.assertEqual(self.volumes[data]['identity'],identity)

    def test_untagged_controller_requires_adoption_and_unchanged_tags(self):
        root,_,_,_,original=self.original_controller(adopted=True)
        self.volumes[root]['tag']='different-owner'
        proposal=retirement.retire(self.m,'boxa',True,role='ops')
        self.assertEqual(proposal['items'],[]);self.assertTrue(proposal['unknown_unchanged'])
        self.volumes[root]['tag']=''
        record=self.m.read(original/'assignment.json');record.pop('origin')
        self.t.store(original/'assignment.json',record)
        proposal=retirement.retire(self.m,'boxa',True,role='ops')
        self.assertEqual(proposal['items'],[]);self.assertTrue(proposal['unknown_unchanged'])

    def test_adopted_controller_tag_change_stops_saved_plan_replay(self):
        root,_,_,_,_=self.original_controller(adopted=True)
        proposal=retirement.retire(self.m,'boxa',True,role='ops')
        work=self.base/'retirement-ops'/proposal['plan_sha256'];work.mkdir()
        self.t.store(work/'plan.json',{k:v for k,v in proposal.items() if k!='state'})
        self.volumes[root]['tag']='different-owner'
        with self.assertRaisesRegex(RuntimeError,'adoption evidence'):
            retirement.retire(self.m,'boxa',role='ops')
        self.assertIn(root,self.volumes)

    def test_reused_lv_name_stops_interrupted_deletion(self):
        original=self.m.run
        def interrupt(argv):
            result=original(argv)
            if argv[0]=='lvremove':raise RuntimeError('lost reply')
            return result
        with patch.object(self.m,'run',side_effect=interrupt),self.assertRaises(RuntimeError):retirement.retire(self.m,'boxa')
        root=next(iter(self.records[0][1]['new_disks']));self.volumes[root]={'identity':{'uuid':'foreign','bytes':4096},'tag':'foreign'}
        with self.assertRaisesRegex(RuntimeError,'UUID or ownership'):retirement.retire(self.m,'boxa')
        self.assertFalse((self.records[0][0]/'retired.json').exists())

    def test_live_reference_and_ownership_conflict_preserve_generation(self):
        root=next(iter(self.records[0][1]['new_disks']))
        for kind in ('live','foreign'):
            with self.subTest(kind=kind):
                (self.live if kind=='live' else self.foreign).add(root)
                proposal=retirement.retire(self.m,'boxa',True)
                self.assertEqual(proposal['items'],[]);self.assertTrue(proposal['unknown_unchanged'])
                self.live.clear();self.foreign.clear()
        self.assertEqual(len(self.volumes),3)

    def test_changed_boot_file_and_incomplete_operations_are_preserved(self):
        (self.records[0][0]/'kernel').write_text('changed')
        self.assertFalse(retirement.retire(self.m,'boxa',True)['items'])
        with patch.object(self.m,'status',return_value={'pending':[{}]}),self.assertRaisesRegex(RuntimeError,'incomplete'):
            retirement.retire(self.m,'boxa',True)

    def test_current_assignment_change_stops_saved_plan_replay(self):
        proposal=retirement.retire(self.m,'boxa',True)
        work=self.base/'retirement'/proposal['plan_sha256'];work.mkdir();self.t.store(work/'plan.json',{k:v for k,v in proposal.items() if k!='state'})
        self.assignment['operation_id']='different'
        with self.assertRaisesRegex(RuntimeError,'assignment changed'):retirement.retire(self.m,'boxa')

    def test_unknown_resource_is_reported_and_not_deleted(self):
        root='/dev/vg0/vmupd_'+'d'*24+'_root';self.volumes[root]={'identity':{'uuid':'unknown','bytes':4096},'tag':'foreign'}
        result=retirement.retire(self.m,'boxa')
        self.assertIn(root,self.volumes);self.assertTrue(any(v['resource']==root for v in result['unknown_unchanged']))
