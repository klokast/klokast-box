"""Runner images must qualify without a server or bootstrap SSH dependency."""
import json
from pathlib import Path
import tempfile
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_infrastructure_guest import load, ROOT
import infrastructure_images
import vm_control_configuration
import vm_template_inputs


class RunnerImageTests(unittest.TestCase):
    def test_air_capsule_contains_enrollment_code_and_rendered_firewall(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            (directory/'inputs.json').write_text(json.dumps({'keys':{},'packages':[], 'codex':{'file':'codex.tar.gz'}}))
            (directory/'codex.tar.gz').write_bytes(b'synthetic Codex archive')
            job=ROOT/'ansible/roles/vm-template-builder/files/vm-template-build-guest'
            output=directory/'capsule.tar'
            with patch.object(vm_template_inputs,'verify_inputs'):
                vm_template_inputs.capsule(directory,output,job,*([job]*8),expected_profile='air-alpine-v1')
            with tarfile.open(output) as archive:
                for name in ('guest_enrollment.py','guest-enrollment.openrc','air-firewall.nft'):
                    self.assertIn(name,archive.getnames())
                firewall=archive.extractfile('air-firewall.nft').read().decode()
                self.assertEqual(firewall,vm_template_inputs.air_firewall(ROOT))

    def test_runner_configuration_does_not_read_controller_ssh_credentials(self):
        view={'registry':{'boxes':{'boxa':{'bootstrap_dns':['1.1.1.1','8.8.8.8']}}}}
        with patch.object(vm_control_configuration.Path,'home',side_effect=AssertionError('SSH key must not be read')):
            value=vm_control_configuration.guest_config('boxa','air',view=view)
        self.assertNotIn('public_key',value);self.assertNotIn('bootstrap_source',value)
        self.assertEqual(value['role'],'air')

    def test_firewall_is_the_managed_recipe_without_underlay_ssh(self):
        firewall=vm_template_inputs.air_firewall(ROOT)
        self.assertNotIn('tcp dport 22',firewall)
        self.assertNotIn('platform-bootstrap-ssh',firewall)
        self.assertIn('tailscale-management-input',firewall)
        self.assertIn('udp dport 41641',firewall)
        self.assertIn('192.168.175.11',firewall)
        self.assertIn('policy drop',firewall)

    def test_enrollment_and_firewall_sources_invalidate_image_reuse(self):
        profile=json.loads((ROOT/'ansible/update-profiles/air-alpine-v1.json').read_text())
        qualified=infrastructure_images.qualify_profile(profile,repo=ROOT)
        original=Path.read_bytes
        for relative in ('guest_enrollment.py','guest-enrollment.openrc','air.yml','nftables.nft.j2'):
            def read(path):
                return original(path)+(b'\n# changed\n' if path.name==relative else b'')
            with self.subTest(relative=relative),patch.object(Path,'read_bytes',read):
                altered=infrastructure_images.qualify_profile(profile,repo=ROOT)
                self.assertNotEqual(altered['recipe_sha256'],qualified['recipe_sha256'])
        self.assertNotIn('openssh',profile['packages'])
        self.assertIn('openssh-client-default',profile['packages'])

    def test_runner_image_has_the_controller_test_toolchain_packages(self):
        profile=json.loads((ROOT/'ansible/update-profiles/air-alpine-v1.json').read_text())
        controller=json.loads((ROOT/'ansible/update-profiles/ops-alpine-v1.json').read_text())
        toolchain={'ansible','py3-jinja2','py3-pygithub','py3-yaml','python3'}
        self.assertLessEqual(toolchain,set(profile['packages']))
        self.assertLessEqual(toolchain,set(controller['packages']))
        for name in ('repositories','repository_origin'):
            self.assertEqual(profile[name],controller[name])

    def test_air_personalization_never_enables_sshd_or_installs_authorized_keys(self):
        module=load('air_personalization_test',ROOT/'ansible/roles/vm-template-builder/files/vm_infrastructure_personalize.py')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name,content in {'etc/klokast-template.json':'{"profile":"air-alpine-v1"}',
                                 'usr/local/libexec/air-firewall.nft':vm_template_inputs.air_firewall(ROOT)}.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
            value={'role':'air','box':'boxa','agent_uid':1004,'agent_gid':1004,
                   'address':'192.168.175.11/24','gateway':'192.168.175.254'}
            with patch.object(module,'Path',side_effect=lambda p:root/str(p).lstrip('/')), \
                    patch.object(module,'put',side_effect=lambda p,c,mode=0o644:self.put(root,p,c)), \
                    patch.object(module,'account'),patch.object(module,'run') as run:
                module.finalize(value,'a'*64)
            self.assertFalse((root/'home/neo/.ssh/authorized_keys').exists())
            self.assertFalse((root/'etc/ssh/sshd_config.d').exists())
            self.assertNotIn('platform-bootstrap-ssh',(root/'etc/nftables.nft').read_text())
            self.assertNotIn(['rc-update','add','sshd','default'],[c.args[0] for c in run.call_args_list])
            self.assertIn(['rc-update','add','guest-enrollment','default'],[c.args[0] for c in run.call_args_list])

    def test_gateway_and_controller_personalization_never_enable_sshd(self):
        module=load('service_personalization_test',ROOT/'ansible/roles/vm-template-builder/files/vm_infrastructure_personalize.py')
        import ops_network
        common={'kind':'klokast.infrastructure-config.v1','box':'boxa','agent_uid':1004,'agent_gid':1004,
                'bootstrap_dns':['1.1.1.1','8.8.8.8']}
        values={'vpn-egress':dict(common,role='vpn-egress',bridge='br-dmz',address='192.168.200.41/24',gateway='192.168.200.254'),
                'ops':dict(common,role='ops',bridge='br-ops',address='192.168.125.10/24',gateway='192.168.125.254',
                           bootstrap_source='192.168.125.1',uplink=ops_network.uplink('boxa'))}
        for role,value in values.items():
            with self.subTest(role=role),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                self.put(root,'etc/klokast-template.json','{"profile":"'+role+'-alpine-v1"}')
                self.assertEqual(module.validate(value),value)
                with patch.object(module,'Path',side_effect=lambda p:root/str(p).lstrip('/')), \
                        patch.object(module,'put',side_effect=lambda p,c,mode=0o644:self.put(root,p,c)), \
                        patch.object(module,'account'),patch.object(module,'run') as run:
                    module.finalize(value,'a'*64)
                commands=[c.args[0] for c in run.call_args_list]
                self.assertFalse((root/'home/neo/.ssh/authorized_keys').exists())
                self.assertFalse((root/'etc/ssh/sshd_config.d').exists())
                firewall=(root/'etc/nftables.nft').read_text()
                self.assertNotIn('platform-bootstrap-ssh',firewall)
                self.assertFalse(any('iifname' in line and 'tcp dport 22' in line for line in firewall.splitlines()))
                self.assertIn('policy drop',firewall)
                self.assertNotIn(['rc-update','add','sshd','default'],commands)
                self.assertIn(['rc-update','add','guest-enrollment','default'],commands)
        for name in ('ops-alpine-v1','vpn-egress-alpine-v1','shared-alpine-v1','air-alpine-v1','router-alpine-v3'):
            packages=json.loads((ROOT/'ansible/update-profiles'/(name+'.json')).read_text())['packages']
            self.assertFalse({'openssh','openssh-server','openssh-openrc'}&set(packages),name)

    def test_controller_configuration_has_no_first_contact_key(self):
        view={'registry':{'boxes':{'boxa':{'bootstrap_dns':['1.1.1.1','8.8.8.8']}}}}
        with patch.object(vm_control_configuration.Path,'home',side_effect=AssertionError('SSH key must not be read')):
            value=vm_control_configuration.guest_config('boxa','ops',view=view)
        self.assertNotIn('public_key',value)
        self.assertEqual(value['bootstrap_source'],'192.168.125.1')

    @staticmethod
    def put(root,name,content):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)


if __name__=='__main__':unittest.main()
