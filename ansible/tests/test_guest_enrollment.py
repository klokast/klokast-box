"""First enrollment must preserve identity, fence retries and keep keys private."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_infrastructure_guest import load, ROOT

GUEST = ROOT / 'ansible/roles/vm-template-builder/files/guest_enrollment.py'
HOST = ROOT / 'ansible/roles/infrastructure-guest/files/infrastructure_enrollment.py'
KEY = 'tskey-auth-TESTING01234567890'
UUID = '12345678-1234-4321-8321-123456789abc'
ATTEMPT = 'a' * 24


class GuestTests(unittest.TestCase):
    def setUp(self):
        self.module = load('enrollment_guest_test', GUEST)
        self.request = dict(kind=self.module.KIND, attempt_id=ATTEMPT, vm_uuid=UUID,
                            config_sha256='f'*64, hostname='boxa-air', tags=['tag:airunner'], auth_key=KEY)
        self.running = {'BackendState': 'Running', 'Self': {'ID': 'n123TEST', 'HostName': 'boxa-air', 'Tags': ['tag:airunner']}}

    def test_registration_key_is_only_stdin_and_never_forces_reauthentication(self):
        def command(argv, **kwargs):
            if argv[0] == 'up':
                self.assertEqual(kwargs['key'], KEY+'\n')
                self.assertNotIn(KEY, ' '.join(argv))
                self.assertIn('--auth-key=file:/dev/stdin', argv)
                self.assertNotIn('--force-reauth', argv)
            return SimpleNamespace(returncode=0, stdout='{"RunSSH":true}')
        with patch.object(self.module, 'status', side_effect=[{'BackendState':'NeedsLogin'}, self.running]), \
                patch.object(self.module, 'command', side_effect=command) as calls:
            result = self.module.enroll(self.request)
        self.assertEqual(result['device_id'], 'n123TEST')
        self.assertNotIn(KEY, json.dumps(result))
        self.assertEqual(sum(c.args[0][0]=='up' for c in calls.call_args_list), 1)

    def test_existing_identity_is_checked_without_using_supplied_key(self):
        with patch.object(self.module, 'status', return_value=self.running), \
                patch.object(self.module, 'command', return_value=SimpleNamespace(returncode=0, stdout='{"RunSSH":true}')) as command:
            self.assertEqual(self.module.enroll(self.request)['status'], 'enrolled')
        self.assertEqual(command.call_args.args[0], ['debug','prefs'])

    def test_probe_requests_key_only_for_an_unregistered_device(self):
        self.request['auth_key']=''
        with patch.object(self.module,'status',return_value={'BackendState':'NeedsLogin'}), \
                patch.object(self.module,'command') as command:
            self.assertEqual(self.module.enroll(self.request), {'status':'needs-key'})
        command.assert_not_called()
        with patch.object(self.module,'status',return_value={'BackendState':'NeedsLogin','Self':{'ID':'old-device'}}), \
                patch.object(self.module,'command') as command:
            with self.assertRaisesRegex(RuntimeError,'existing-device-needs-repair'):
                self.module.enroll(self.request)
        command.assert_not_called()

    def test_wrong_tags_or_hostname_cannot_report_success(self):
        for field, value in (('Tags',['tag:ops']),('HostName','boxb-air')):
            current=copy.deepcopy(self.running);current['Self'][field]=value
            with self.subTest(field=field), patch.object(self.module,'status',return_value=current), \
                    patch.object(self.module,'command') as command:
                with self.assertRaisesRegex(RuntimeError,'identity-not-converged'):
                    self.module.enroll(self.request)
            command.assert_not_called()

    def test_timeout_after_cli_registration_recovers_the_same_identity(self):
        with patch.object(self.module,'status',side_effect=[{'BackendState':'NeedsLogin'},self.running]), \
                patch.object(self.module,'command',side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0,stdout='{"RunSSH":true}')]):
            self.assertEqual(self.module.enroll(self.request)['status'],'enrolled')

    def tree(self, root):
        files={'proc/cmdline':'klokast_enrollment='+ATTEMPT,
               'sys/hypervisor/uuid':UUID, 'sys/class/block/xvdg/ro':'1', 'sys/class/block/xvdh/ro':'0',
               'etc/klokast-infrastructure.json':json.dumps({'config_sha256':'f'*64,'configuration':{'role':'air','box':'boxa'}}),
               'dev/xvdg':json.dumps(self.request), 'dev/xvdh':''}
        for name, content in files.items():
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)

    def test_full_guest_failure_redacts_errors_and_credential(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.tree(root)
            with patch.object(self.module,'Path',side_effect=lambda p:root/str(p).lstrip('/')), \
                    patch.object(self.module,'INPUT',root/'dev/xvdg'), patch.object(self.module,'OUTPUT',root/'dev/xvdh'), \
                    patch.object(self.module.stat,'S_ISBLK',return_value=True), patch.object(self.module.os,'geteuid',return_value=0), \
                    patch.object(self.module,'enroll',side_effect=RuntimeError(KEY)):
                self.assertEqual(self.module.main(),1)
            result=(root/'dev/xvdh').read_bytes().split(b'\0')[0]
            self.assertNotIn(KEY.encode(),result)
            self.assertEqual(json.loads(result)['status'],'failed')

    def test_normal_boot_does_not_read_devices_or_call_tailscale(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'proc').mkdir();(root/'proc/cmdline').write_text('root=/dev/xvda')
            with patch.object(self.module,'Path',side_effect=lambda p:root/str(p).lstrip('/')), \
                    patch.object(self.module,'command') as command:
                self.assertEqual(self.module.main(),0)
            command.assert_not_called()

    def test_guest_rejects_foreign_assignment_and_invalid_credential(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.tree(root)
            with patch.object(self.module,'Path',side_effect=lambda p:root/str(p).lstrip('/')):
                self.assertEqual(self.module.validate(self.request,ATTEMPT),self.request)
                for field,value in (('vm_uuid','other'),('hostname','other-air'),('tags',['tag:ops']),('config_sha256','0'*64),('auth_key','bad')):
                    with self.subTest(field=field), self.assertRaises(RuntimeError):
                        self.module.validate(dict(self.request,**{field:value}),ATTEMPT)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.module=load('enrollment_host_test',HOST)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.work=self.root/'work';self.work.mkdir()
        self.state={'stage':'prepared','box':'boxa','uuid':UUID,'config_sha256':'f'*64}
        self.current=None;self.loops={};self.attached=set();self.calls=[];self.result_status='enrolled'
        for name,value in (('RUNTIME',self.root/'run'),('safe_directory',lambda *_:None),('safe_file',lambda *_:None),
                           ('domain',lambda *_:self.current),('run',self.native_run),
                           ('loop_devices',lambda p:self.loops.get(str(p),[])),('attach_loop',self.attach),('detach_loop',self.detach)):
            mock=patch.object(self.module,name,value);mock.start();self.addCleanup(mock.stop)

    def attach(self,path,readonly=False):
        self.assertEqual(readonly,path.name=='input.slot')
        device='/dev/loop'+str(len(self.loops));self.loops[str(path)]=[device];return device

    def detach(self,path,device):
        self.assertNotIn(path.stem,self.attached)
        self.assertEqual(self.loops.pop(str(path)),[device])

    def native_run(self,argv):
        self.calls.append([str(v) for v in argv])
        if argv[:2]==['xl','create']:
            self.current={'domid':42,'config':{'c_info':{'uuid':UUID}}}
            if Path(argv[-1]).name=='air.cfg':
                return SimpleNamespace(stdout='')
            self.attached={'input','result'}
            request=json.loads((self.module.directory(self.state)/'input.slot').read_bytes().split(b'\0')[0])
            result={k:request[k] for k in ('kind','attempt_id','vm_uuid','config_sha256')}
            result.update(status=self.result_status)
            if self.result_status=='enrolled':result.update(device_id='n123TEST',hostname='boxa-air',tags=['tag:airunner'])
            (self.module.directory(self.state)/'result.slot').write_bytes(json.dumps(result).encode()+b'\0')
        if argv[:2]==['xl','block-detach']:
            self.attached.discard('input' if argv[-1]=='xvdg' else 'result')
        if argv[:2]==['xl','shutdown']:
            self.current=None;self.attached=set()
        rows='\n'.join(str(self.module.DEVICES[k][1])+' backend' for k in self.attached)
        return SimpleNamespace(stdout=rows)

    def test_success_cleans_volatile_devices_and_records_only_public_result(self):
        result=self.module.enroll(self.work,self.state,"disk = []\nextra = ''\n",KEY)
        self.assertEqual(result['action'],'ready')
        record=(self.work/'assignment.json').read_text();self.assertNotIn(KEY,record)
        self.assertEqual(self.state['enrollment_result']['device_id'],'n123TEST')
        self.assertEqual(self.loops,{})
        self.assertFalse(self.module.directory(self.state).exists())
        self.assertFalse(any(KEY in ' '.join(c) for c in self.calls))
        self.assertFalse(any(c[:2]==['xl','shutdown'] for c in self.calls))

    def test_failed_enrollment_preserves_guest_and_refuses_another_key(self):
        self.result_status='failed'
        with self.assertRaisesRegex(RuntimeError,'guest enrollment failed'):
            self.module.enroll(self.work,self.state,'',KEY)
        self.assertIsNotNone(self.current);self.assertTrue(self.loops)
        self.assertEqual(self.module.status(self.work,self.state),'resume')
        with self.assertRaisesRegex(RuntimeError,'cannot replace'):
            self.module.enroll(self.work,self.state,'',KEY)
        self.assertFalse(any(c[:2]==['xl','shutdown'] for c in self.calls))

    def test_lost_reply_resumes_success_without_reissuing_a_key(self):
        with patch.object(self.module,'cleanup',side_effect=RuntimeError('disconnect')):
            with self.assertRaisesRegex(RuntimeError,'disconnect'):
                self.module.enroll(self.work,self.state,'',KEY)
        self.assertEqual(self.module.status(self.work,self.state),'resume')
        result=self.module.enroll(self.work,self.state,'','')
        self.assertEqual(result['action'],'ready')
        self.assertEqual(sum(c[:2]==['xl','create'] for c in self.calls),1)
        self.assertEqual(self.loops,{})

    def test_host_reboot_uses_keyless_probe_before_requesting_another_key(self):
        self.state['enrollment_attempt']=ATTEMPT
        self.assertEqual(self.module.status(self.work,self.state),'probe')
        self.result_status='needs-key'
        result=self.module.enroll(self.work,self.state,'','')
        self.assertEqual(result['action'],'key-required')
        self.assertIsNone(self.current);self.assertEqual(self.loops,{})
        self.assertNotIn('enrollment_attempt',self.state)

    def test_existing_registered_root_is_recovered_by_keyless_probe(self):
        self.state['enrollment_attempt']=ATTEMPT
        result=self.module.enroll(self.work,self.state,'','')
        self.assertEqual(result['action'],'ready')
        self.assertFalse(any(c[:2]==['xl','shutdown'] for c in self.calls))

    def test_stale_result_and_foreign_domain_cannot_be_accepted_or_cleaned(self):
        self.result_status='failed'
        with self.assertRaises(RuntimeError):self.module.enroll(self.work,self.state,'',KEY)
        self.state['enrollment_result']['vm_uuid']='other'
        with self.assertRaisesRegex(RuntimeError,'does not match'):
            self.module.status(self.work,self.state)
        self.current['config']['c_info']['uuid']='other'
        with self.assertRaises(RuntimeError):self.module.cleanup(self.state)
        self.assertTrue(self.loops)

    def test_saved_success_survives_host_reboot_and_starts_the_permanent_recipe(self):
        with patch.object(self.module,'cleanup',side_effect=RuntimeError('disconnect')):
            with self.assertRaises(RuntimeError):self.module.enroll(self.work,self.state,'',KEY)
        self.current=None;self.attached=set();self.loops={}
        self.module.cleanup(self.state)
        result=self.module.enroll(self.work,self.state,'','')
        self.assertEqual(result['action'],'ready')
        self.assertEqual(self.calls[-1],['xl','create',str(self.work/'air.cfg')])
        self.assertIsNotNone(self.current)

    def test_ready_assignment_never_uses_new_credentials(self):
        self.state['stage']='ready'
        self.assertFalse(self.module.enroll(self.work,self.state,'',KEY)['changed'])
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()


class SharedGuestTests(GuestTests):
    role = 'bak'

    def setUp(self):
        super().setUp()
        self.request.update(hostname='boxa-'+self.role, tags=['tag:vm'])
        self.running['Self'].update(HostName='boxa-'+self.role, Tags=['tag:vm'])

    def tree(self, root):
        super().tree(root)
        marker=root/'etc/klokast-infrastructure.json'
        value=json.loads(marker.read_text())
        value['configuration']['role']=self.role
        marker.write_text(json.dumps(value))


class DMZGuestTests(SharedGuestTests):
    role = 'dmz'


class IoTGuestTests(SharedGuestTests):
    role = 'iot'
