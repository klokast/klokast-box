"""First boot never invents a second disk, UUID, or network identity."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_initial_boot as boot
import router_records as records
from router_transaction import TransactionError
from test_router_updates import ENGINE, PROFILE, release


class InitialBootTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name) / 'data'
        self.base.mkdir(mode=0o700)
        for name, value in (('ROOT_UID',os.geteuid()),('parents',lambda path:None)):
            context = patch.object(records,name,value)
            context.start(); self.addCleanup(context.stop)
        for name in ('records','generations','operations'):
            (self.base / name).mkdir(mode=0o700)
        self.xen = self.base / 'xen'
        self.xen.mkdir()
        self.operation = 'a'*24
        self.work = self.base / 'operations' / self.operation
        self.work.mkdir(mode=0o700)
        self.disk = {'path':'/dev/vg0/routergen_' + self.operation,
                     'uuid':'native-uuid','bytes':2147483648}
        self.release = release()
        self.source = {'kind':'klokast.router-initial-preparation.v1',
            'box':'boxa','operation_id':self.operation,'engine_commit':ENGINE,
            'inputs_sha256':self.release['inputs']['inputs_sha256'],
            'template_operation':'c'*24, 'registry_sha256':'1'*64,
            'release_sha256':self.release['receipt_sha256'], 'selection_sha256':'2'*64}
        self.prepared = {'synthetic':'validated preparer output'}
        self.installation = boot.generations.seal({
            'kind':'klokast.router-initial-installation.v1','box':'boxa','role':'router',
            'operation_id':self.operation,'engine_commit':ENGINE,
            'selection_sha256':'2'*64,'release_sha256':self.release['receipt_sha256'],
            'disk':self.disk,'stage':'prepared',
            'preparation_sha256':boot.generations.digest(self.prepared),
            'enrollment_sha256':None,'machine_id':None,'generation_sha256':None})
        self.xen_record = {'uuid':'8c681f14-92dd-484d-84cf-82b7ca8c6a3d',
                           'memory':512,'vcpus':1,
                           'vif':['bridge=br-wan,mac=00:16:3e:50:00:01',
                                  'bridge=br-bak,mac=00:16:3e:50:00:04']}
        self.value = {'kind':'klokast.router-initial-boot-request.v1',
            'box':'boxa','operation_id':self.operation,'engine_commit':ENGINE,
            'registry_sha256':'1'*64,
            'source_request_sha256':boot.generations.digest(self.source),
            'preparation_sha256':boot.generations.digest(self.prepared),
            'selection_sha256':'2'*64,'release_sha256':self.release['receipt_sha256'],
            'xen':self.xen_record}
        self.grant = {'kind':'klokast.router-initial-boot-grant.v1','engine_commit':ENGINE,
            'request_sha256':boot.generations.digest(self.value),
            'granted_at':1000,'expires_at':1300}
        for name, value in (('request',self.source),('candidate-job',{'job':'fixture'}),
                ('preparation-result',self.prepared),('release',self.release),('profile',PROFILE),
                ('initial-boot-request',self.value),('initial-boot-grant',self.grant)):
            records.write(self.work / (name + '.json'), value)
        records.write(self.base / 'installation.json', self.installation)
        for target, name, value in (
                (boot.time,'time',Mock(return_value=1001)),
                (boot.preparation,'validate_result',Mock()),
                (boot.disks,'verify',Mock(return_value=self.disk)),
                (boot.disks,'record',Mock(return_value={'stage':'cloned'}))):
            context = patch.object(target,name,value)
            context.start(); self.addCleanup(context.stop)
        self.storage = records.Records('boxa',self.base)
        self.files = {'kernel':{'path':str(self.base / 'generations' / self.operation / 'kernel'),
                                'sha256':'3'*64,'bytes':10},
                      'initramfs':{'path':str(self.base / 'generations' / self.operation / 'initramfs'),
                                'sha256':'4'*64,'bytes':20}}
        self.file_patch = patch.object(boot,'boot_files',return_value=self.files)
        self.file_patch.start(); self.addCleanup(self.file_patch.stop)
        self.host = Mock()
        self.host.initial_guest.side_effect = [None,{'domid':7}]
        self.native_class = boot.native.Native
        self.host_patch = patch.object(boot.native,'Native',return_value=self.host)
        self.host_patch.start(); self.addCleanup(self.host_patch.stop)
        self.create = Mock(return_value='')
        self.create_patch = patch.object(boot.native,'command',self.create)
        self.create_patch.start(); self.addCleanup(self.create_patch.stop)

    def execute(self):
        return boot.execute(self.storage,self.operation,ENGINE,xen=self.xen)

    def test_intent_precedes_xl_create_and_retry_reconciles_exact_guest(self):
        def created(argv,*args,**kwargs):
            self.assertEqual(argv[:2],['/usr/sbin/xl','create'])
            self.assertTrue((self.work / 'initial-boot-intent.json').exists())
            self.assertEqual(self.storage.installation(),self.installation)
            return ''
        self.create.side_effect = created
        first = self.execute()
        self.assertEqual(first['status'],'running-first-contact')
        self.assertEqual(first['xen_uuid'],self.xen_record['uuid'])
        self.assertEqual(self.create.call_count,1)
        self.host.initial_guest.side_effect = None
        self.host.initial_guest.return_value = {'domid':8}
        again = self.execute()
        self.assertEqual(again['domain_id'],8)
        self.assertEqual(self.create.call_count,1)
        self.assertEqual(records.read(self.work / 'initial-boot-intent.json')['disk'],self.disk)

    def test_interrupted_create_reconciles_without_second_start(self):
        self.create.side_effect = TransactionError('transport lost after create')
        with self.assertRaisesRegex(TransactionError,'transport lost'):
            self.execute()
        self.assertTrue((self.work / 'initial-boot-intent.json').exists())
        self.create.reset_mock()
        self.host.initial_guest.side_effect = None
        self.host.initial_guest.return_value = {'domid':9}
        self.assertEqual(self.execute()['domain_id'],9)
        self.create.assert_not_called()

    def test_changed_xen_identity_and_enrollment_intent_refuse_reboot(self):
        self.execute()
        changed = {**self.value,'xen':{**self.xen_record,'uuid':'f'*8+'-'+'f'*4+'-'+'f'*4+'-'+'f'*4+'-'+'f'*12}}
        records.write(self.work / 'initial-boot-request.json',changed)
        records.write(self.work / 'initial-boot-grant.json',
                      {**self.grant,'request_sha256':boot.generations.digest(changed)})
        self.create.reset_mock()
        with self.assertRaisesRegex(TransactionError,'boot definition changed'):
            self.execute()
        self.create.assert_not_called()
        records.write(self.work / 'initial-boot-request.json',self.value)
        records.write(self.work / 'initial-boot-grant.json',self.grant)
        records.write(self.work / 'initial-enrollment-intent.json',{'kind':'intent'})
        with self.assertRaisesRegex(TransactionError,'reconcile a started enrollment'):
            self.execute()
        self.create.assert_not_called()

    def test_expired_grant_and_stale_preparation_refuse_before_native_boot(self):
        records.write(self.work / 'initial-boot-grant.json',{**self.grant,'expires_at':1001})
        with self.assertRaisesRegex(TransactionError,'grant is stale'):
            self.execute()
        records.write(self.work / 'initial-boot-grant.json',self.grant)
        records.write(self.work / 'preparation-result.json',{'changed':True})
        with self.assertRaisesRegex(TransactionError,'exact prepared installation'):
            self.execute()
        self.create.assert_not_called()

    def test_guest_scan_refuses_another_disk_or_reused_router_mac(self):
        expected = boot.native.literal_configuration(
            boot.generations.initial_configuration(self.xen_record,self.disk,self.files))
        other = {'domid':4,'config':{'c_info':{'name':'other','uuid':'f'*8+'-'+'f'*4+'-'+'f'*4+'-'+'f'*4+'-'+'f'*12},
                 'disks':[{'pdev_path':'/dev/vg0/other'}],
                 'nics':[{'mac':'00:16:3e:50:00:01'}]}}
        host = self.native_class()
        host.disk = Mock(return_value=100)
        host.device = Mock(side_effect=lambda value:100 if value == self.disk['path'] else 200)
        host.inventory = Mock(return_value=[{'domid':0},other])
        with self.assertRaisesRegex(TransactionError,'claims an initial router MAC'):
            host.initial_guest(self.disk,expected,deadline=100)
        other['config']['nics'] = []
        other['config']['disks'] = [{'pdev_path':self.disk['path']}]
        with self.assertRaisesRegex(TransactionError,'claims the initial router identity or disk'):
            host.initial_guest(self.disk,expected,deadline=100)

    def test_boot_artifact_copy_checks_release_bytes_on_retry(self):
        template = self.base / 'template'
        template.mkdir(mode=0o700)
        original = b'fixed synthetic kernel bytes'
        expected = boot.hashlib.sha256(original).hexdigest()
        source, target = template / 'kernel', self.base / 'kernel'
        source.write_bytes(original)
        source.chmod(0o600)
        first = boot.artifact(source,target,expected,1024)
        self.assertEqual(first['bytes'],len(original))
        self.assertEqual(target.read_bytes(),original)
        self.assertEqual(boot.artifact(source,target,expected,1024),first)
        target.write_bytes(b'changed synthetic bytes')
        with self.assertRaisesRegex(TransactionError,'versioned boot artifact changed'):
            boot.artifact(source,target,expected,1024)


if __name__ == '__main__':
    unittest.main()
