"""Router selection and acceptance use the shared flow and native evidence."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import vm_router_updates as router
import vm_service_probe
import vm_service_runtime
import vm_service_execution


class RouterUpdates(unittest.TestCase):
    def setUp(self):
        self.contract=dict(kind='klokast.router-retained-state.v1',box='boxa',filesystem_uuid='12345678-1234-1234-1234-123456789abc',
                           accounts=dict(dnsmasq_uid=100,dnsmasq_gid=100,tailscale_gid=101))
        self.before=dict(identity=dict(machine_key='a'*64,machine_id=None,ssh_keys={},duid='b'*64,privacy_secret='c'*64),
                         accounts={'neo':[1000,1000]},router_accounts=self.contract['accounts'],persistent_storage=None)
        files={'etc/dnsmasq.conf':'dhcp-leasefile=/var/lib/misc/dnsmasq.leases\n','etc/nftables.nft':'# policy\n'}
        self.recipe=dict(files=files)
        evidence=dict(kernel_release='test',packages_sha256='d'*64,tailscale_version='1.2.3',
                      tailscale_sha256='e'*64,tailscaled_sha256='f'*64)
        self.state=dict(role='router',new_uuid='candidate',requested_configuration=dict(router_state=self.contract,
                        router_configuration=self.recipe,image_evidence=evidence,before=self.before))
        self.after=dict(self.before,persistent_storage=dict(self.contract,mounted=True),retained_uuid=self.contract['filesystem_uuid'],
            xen_uuid='candidate',kernel='test',packages_sha256='d'*64,tailscale_version='1.2.3',tailscale_sha256='e'*64,
            tailscaled_sha256='f'*64,configuration_files={'etc/nftables.nft':hashlib.sha256(b'# policy\n').hexdigest(),
            'etc/dnsmasq.conf':hashlib.sha256(b'dhcp-leasefile=/srv/retained/dnsmasq/dnsmasq.leases\n').hexdigest()},
            **dict.fromkeys(('kernel_modules','online','firewall','forwarding','wan','service'),True))

    def test_router_selects_native_profile_and_never_podman_probe(self):
        self.assertEqual(vm_service_runtime.profile('router'),'router-alpine-v3')
        self.assertEqual(vm_service_runtime.target({'instance':{'boxes':{'boxa':{}}}},'boxa','router'),'running')
        with patch.object(router,'probe',return_value=self.after) as probe:
            self.assertEqual(vm_service_probe.probe('boxa','router'),self.after)
            probe.assert_called_once_with('boxa',repair=False)

    def test_migration_freezes_source_but_reattachment_keeps_the_exact_contract(self):
        source=dict(assignment_sha256='a'*64,generation_sha256='b'*64,topology_sha256='c'*64)
        with patch.object(router,'render',return_value=(self.recipe,{'policy':'frozen'})):
            config,values=router.configuration('boxa',self.before,dict(router_source=source),{})
            self.assertEqual(config['router_source'],source)
            self.assertEqual(config['router_state']['accounts'],self.contract['accounts'])
            config,_=router.configuration('boxa',self.after,dict(assignment={'stage':'complete'}),{})
            self.assertEqual(config['router_state'],self.contract)
            self.assertIsNone(config['router_source'])
            with self.assertRaisesRegex(RuntimeError,'disagree'):router.configuration('boxa',self.after,{}, {})
            with self.assertRaisesRegex(RuntimeError,'legacy source'):router.configuration('boxa',self.before,{}, {})

    def test_router_acceptance_preserves_absence_and_checks_native_image(self):
        checks=vm_service_probe.verify(self.before,self.after,self.state)
        self.assertTrue(checks['retained_state']);self.assertNotIn('workloads_unchanged',checks)
        for change in ({'retained_uuid':None},{'tailscaled_sha256':'other'},{'wan':False},
                       {'identity':dict(self.before['identity'],machine_id='new')},
                       {'configuration_files':dict(self.after['configuration_files'],**{'etc/nftables.nft':'other'})}):
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                vm_service_probe.verify(self.before,dict(self.after,**change),self.state)

    def test_failed_router_network_verification_cannot_submit_acceptance(self):
        state=dict(self.state,journal={'stage':'booted'},reboot_verification={'status':'complete'})
        variables=dict(service_configuration=state['requested_configuration'],service_receipt={},router_verification={})
        with patch.object(vm_service_runtime,'step',return_value=state) as step, \
             patch.object(vm_service_runtime,'wait_probe',return_value=self.after), \
             patch.object(vm_service_runtime,'authority',return_value='running'), \
             patch.object(router,'verify_network',side_effect=RuntimeError('router policy failed')):
            with self.assertRaisesRegex(RuntimeError,'router policy failed.*--resume'):
                vm_service_execution.finish('boxa','router',Path('/frozen'),variables,{},'a'*24,'b'*24,'c'*64)
        self.assertEqual([c.args[2] for c in step.call_args_list],['prepare','advance'])


if __name__=='__main__':unittest.main()
