import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_ops_replacement as existing
from test_infrastructure_guest import load, ROOT


class ControllerDataTests(unittest.TestCase):
    def setUp(self):
        self.fixture = existing.ReplacementTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.m = self.fixture.m
        self.data = load('ops_data_fixture', ROOT/'ansible/roles/infrastructure-guest/files/ops_data.py')
        p=patch.object(self.data, "require_no_dom0_reference");p.start();self.addCleanup(p.stop)
        f = self.fixture
        cfg = Path(f.old['work'])/'ops.cfg'
        cfg.write_text("name = 'ops'\nuuid = 'old-uuid'\ndisk = ['phy:/dev/vg0/lv_ops,xvda,w']\n")
        f.old['boot_sha256'] = self.m.checksum(cfg)
        self.m.write(self.m.BASE/'assignment.json', f.old)
        (self.m.XEN/'ops.cfg').write_text(cfg.read_text())
        for name, value in (
            ('same_device', lambda a,b: a==b),
            ('install_boot', lambda work: (self.m.XEN/'ops.cfg').write_text((work/'ops.cfg').read_text())),
            ('run', self.command)):
            p=patch.object(self.m,name,value);p.start();self.addCleanup(p.stop)

    def command(self, argv, **kwargs):
        f=self.fixture
        if argv[0]=='vgs': return SimpleNamespace(stdout=str(100*1024**3))
        if argv[:3]==['xl','list','-l']: return SimpleNamespace(stdout=json.dumps([f.live]))
        if argv[:2]==['xl','block-attach']:
            f.live['config']['disks'].append({'pdev_path':self.data.DEVICE,'vdev':'xvdb'})
        result=f.run_command(argv,**kwargs)
        if argv[0]=='lvcreate' and 'lv_ops_data' in argv: f.lvs[self.data.DEVICE]['lv_size']=argv[argv.index('--size')+1].rstrip('B')
        if argv[0]=='lvextend': f.lvs[self.data.DEVICE]['lv_size']=argv[argv.index('--size')+1].rstrip('B')
        return result

    def attach(self): return self.data.attach(self.m,'boxa')

    def test_interruption_after_each_external_step_reuses_the_same_disk(self):
        for point in ('lvcreate','block-attach','vgcfgbackup'):
            with self.subTest(point=point):
                f=ControllerDataTests(); f.setUp()
                try:
                    failed=False
                    def interrupted(argv,**kwargs):
                        nonlocal failed
                        result=f.command(argv,**kwargs)
                        if not failed and point in argv:
                            failed=True;raise RuntimeError('lost reply')
                        return result
                    with patch.object(f.m,'run',side_effect=interrupted):
                        with self.assertRaisesRegex(RuntimeError,'lost reply'): f.attach()
                    expected=f.fixture.lvs[f.data.DEVICE]['lv_uuid']
                    value=f.attach()['data_volume']
                    self.assertEqual(value['lv_uuid'],expected)
                    self.assertEqual(f.fixture.events.count('lvcreate'),1)
                    self.assertEqual(len(f.fixture.live['config']['disks']),2)
                    self.assertNotIn('dd',f.fixture.events)
                    self.assertNotIn('mkfs.ext4',f.fixture.events)
                finally:f.doCleanups()

    def test_growth_resumes_after_each_external_step_without_changing_identity(self):
        for point in ('lvextend','vgcfgbackup'):
            with self.subTest(point=point):
                f=ControllerDataTests(); f.setUp()
                try:
                    before=f.attach()['data_volume']
                    proof=f.fixture.root/'mount.json'
                    proof.write_text(json.dumps({'box':'boxa','filesystem_uuid':before['filesystem_uuid'],'mounted':True}))
                    f.data.attach(f.m,'boxa',complete=True,evidence=proof)
                    failed=False
                    def interrupted(argv,**kwargs):
                        nonlocal failed
                        result=f.command(argv,**kwargs)
                        if argv[0]==point and not failed:
                            failed=True;raise RuntimeError('lost resize reply')
                        return result
                    with patch.object(f.m,'run',side_effect=interrupted):
                        with self.assertRaisesRegex(RuntimeError,'lost resize'):
                            f.data.attach(f.m,'boxa',size_gib=50)
                    pending=f.data.attach(f.m,'boxa',size_gib=50)['data_volume']
                    self.assertEqual(pending['resize_bytes'],50*f.data.SIZE)
                    after=f.data.attach(f.m,'boxa',complete=True,evidence=proof)['data_volume']
                    self.assertEqual(after['bytes'],50*f.data.SIZE)
                    self.assertNotIn('resize_bytes',after)
                    for key in ('lv_uuid','filesystem_uuid','tag','path'):
                        self.assertEqual(before[key],after[key])
                    self.assertFalse(f.data.attach(f.m,'boxa',size_gib=50)['changed'])
                finally:f.doCleanups()

    def test_unknown_and_reused_lv_names_are_never_adopted(self):
        self.fixture.lvs[self.data.DEVICE]={'lv_uuid':'unknown','lv_tags':'','lv_size':str(self.data.SIZE)}
        with self.assertRaisesRegex(RuntimeError,'unowned'):self.attach()
        self.fixture.lvs.pop(self.data.DEVICE)
        self.attach()
        self.fixture.lvs[self.data.DEVICE]['lv_uuid']='reused-name'
        with self.assertRaisesRegex(RuntimeError,'identity differs'):self.attach()

    def test_ready_volume_replays_and_replacement_keeps_attachment(self):
        volume=self.attach()['data_volume']
        proof=self.fixture.root/'mount.json'
        proof.write_text(json.dumps({'box':'boxa','filesystem_uuid':volume['filesystem_uuid'],'mounted':True}))
        self.data.attach(self.m,'boxa',complete=True,evidence=proof)
        self.assertFalse(self.attach()['changed'])
        state=self.m.read(self.m.BASE/'assignment.json')
        cfg=self.m.infra.configuration(Path('/work'),'ops',dict(state,bridge='br-ops'))
        self.assertIn(self.data.DEVICE+',xvdb,w',cfg)
        from unittest.mock import patch
        old_guest=self.fixture.guest
        def guest(value):
            result=old_guest(value)
            if value.get('data_volume'):result['config']['disks'].append({'pdev_path':self.data.DEVICE,'vdev':'xvdb'})
            return result
        with patch.object(self.fixture,'guest',side_effect=guest):
            self.fixture.replace();self.fixture.accept()
            after=self.fixture.state()
            self.assertEqual(after['data_volume']['lv_uuid'],volume['lv_uuid'])
            self.assertEqual(after['configuration']['ops_data_uuid'],volume['filesystem_uuid'])

    def test_insufficient_capacity_and_other_guest_reference_stop_setup(self):
        original=self.command
        def no_space(argv,**kwargs):
            return SimpleNamespace(stdout='0') if argv[0]=='vgs' else original(argv,**kwargs)
        with patch.object(self.m,'run',side_effect=no_space):
            with self.assertRaisesRegex(RuntimeError,'free LVM capacity'):self.attach()
        self.assertNotIn('lvcreate',self.fixture.events)
        self.attach()
        foreign={'domid':99,'config':{'disks':[{'pdev_path':self.data.DEVICE}]}}
        def shared(argv,**kwargs):
            return SimpleNamespace(stdout=json.dumps([self.fixture.live,foreign])) if argv[:3]==['xl','list','-l'] else original(argv,**kwargs)
        with patch.object(self.m,'run',side_effect=shared):
            with self.assertRaisesRegex(RuntimeError,'another live guest'):self.attach()

    def test_lost_assignment_publication_before_allocation_is_replayable(self):
        original=self.m.write
        failed=False
        def write(path,value):
            nonlocal failed
            original(path,value)
            if not failed and path.name=='assignment.json' and value.get('data_volume'):
                failed=True;raise RuntimeError('lost assignment reply')
        with patch.object(self.m,'write',side_effect=write):
            with self.assertRaisesRegex(RuntimeError,'lost assignment'):self.attach()
        intent=self.m.read(self.m.BASE/'assignment.json')['data_volume']
        actual=self.attach()['data_volume']
        self.assertEqual(actual['filesystem_uuid'],intent['filesystem_uuid'])
        self.assertEqual(self.fixture.events.count('lvcreate'),1)

    def test_dom0_mount_reference_is_refused(self):
        with patch.object(Path,'stat',return_value=SimpleNamespace(st_rdev=0)), \
                patch.object(Path,'read_text',return_value='1 2 0:0 / /data rw - ext4 /dev/data rw\n'):
            # Invoke the original helper rather than the fixture's no-device stub.
            native=load('ops_data_mount_fixture',ROOT/'ansible/roles/infrastructure-guest/files/ops_data.py')
            with self.assertRaisesRegex(RuntimeError,'mounted in dom0'):
                native.require_no_dom0_reference(self.m)

    def test_unfinished_storage_blocks_replacement(self):
        self.attach()
        with self.assertRaisesRegex(RuntimeError,'finish the retained'):self.fixture.replace()


class GuestDataTests(unittest.TestCase):
    def test_missing_or_different_filesystem_is_not_formatted_on_replacement(self):
        m=load('ops_guest_data_fixture',ROOT/'ansible/roles/vm-template-builder/files/vm_ops_data.py')
        for text in ('','/dev/xvdb: UUID="different" TYPE="ext4"'):
            with patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=text)),patch.object(m,'run') as run:
                with self.assertRaises(RuntimeError):m.configure('boxa','11111111-2222-3333-4444-555555555555')
                run.assert_not_called()
