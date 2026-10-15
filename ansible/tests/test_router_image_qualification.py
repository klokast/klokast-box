"""Router image admission needs native replacement evidence, never Podman evidence."""
import copy
import json
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import platform_updates as p
import infrastructure_images
import platform_update_images


class Qualification(unittest.TestCase):
    def setUp(self):
        self.inputs = {'kind':'klokast.vm-template-inputs.v1','engine_commit':'a'*40,
            'profile':'router-alpine-v3','branch':'v3.24','architecture':'x86_64',
            'tailscale':{'version':'1.2.3','sha256':'d'*64,'openrc_sha256':'e'*64},
            'packages':[dict(name=n,version='1-r0',origin=n,architecture='x86_64',
                file='packages/'+n+'-1-r0.apk',bytes=100,sha256='a'*64)
                for n in ('linux-virt','dhcpcd','dnsmasq','nftables')]}
        self.inputs['inputs_sha256'] = p.digest(self.inputs)
        identity = {'machine_key':'a'*64,'files':{'etc/machine-id':'b'*64}}
        contract = {'filesystem_uuid':str(uuid.uuid4())}
        proofs = {k:dict(kind='klokast.router-image-fixture.v1',stage=k,operation_id='a'*24,
            inputs_sha256=self.inputs['inputs_sha256'],success=True,production_data_used=False)
            for k in ('seed','migrate','first','reboot-first','missing','substitute','attach','second','reboot-second')}
        proofs['seed']['seed'] = dict(identity=identity,contract=contract)
        proofs['seed']['native_services'] = dict.fromkeys(('wan_dhcp_identity','wan_lease','lan_lease','lan_dns',
            'offline_tailscale_state','latest_preferences_after_restart','native_machine_key_after_restart',
            'synthetic_profile','upstream_daemon_version'),True)
        for k in ('migrate','attach'): proofs[k].update(copy_verified=True,contract=contract)
        for k in ('first','reboot-first','second','reboot-second'):
            proofs[k].update(identity=identity,filesystem_uuid=contract['filesystem_uuid'],
                             boot_id=str(uuid.uuid4()),storage=True,services=True,leases=True)
        for k in ('missing','substitute'): proofs[k].update(refused=True,root_state_absent=True)
        self.candidate = dict(kind='klokast.vm-template-candidate.v1',operation_id='a'*24,
            inputs_sha256=self.inputs['inputs_sha256'],success=True,accepted=False,
            kernel_release='test-virt',modules_release='test-virt',tests=dict.fromkeys(p.BASE_BUILD_TESTS,True),
            artifacts={k:dict(bytes=100,sha256='c'*64) for k in ('root','kernel','initramfs')},
            boot_test=dict(kind='klokast.vm-template-test-result.v1',operation_id='a'*24,
                inputs_sha256=self.inputs['inputs_sha256'],success=True,kernel_release='test-virt',tested_root_sha256='c'*64,
                router_qualification=proofs,tests=dict.fromkeys(p.ROUTER_TESTS,True)))

    def release(self): return p.no_application_release(self.inputs,self.candidate,{}, {}, {})

    def test_complete_native_evidence_admits_router_without_podman(self):
        release = self.release()
        self.assertEqual(release['profile'],'router-alpine-v3')
        self.assertNotIn('rootless_podman',release['tests'])
        self.assertIn('retained_reattachment',release['tests'])

    def test_missing_stages_wrong_identity_replayed_boot_and_failed_refusal_block_release(self):
        original = copy.deepcopy(self.candidate)
        def evidence(): return self.candidate['boot_test']['router_qualification']
        changes = [lambda: self.candidate['boot_test'].update(success=False),
            lambda: evidence()['seed'].update(native_services={}), lambda: evidence().pop('attach'),
            lambda: evidence()['second'].update(identity={'changed':True}),
            lambda: evidence()['reboot-first'].update(boot_id=evidence()['first']['boot_id']),
            lambda: evidence()['missing'].update(root_state_absent=False),
            lambda: evidence()['migrate'].update(copy_verified=False),
            lambda: evidence()['seed'].update(inputs_sha256='f'*64)]
        for change in changes:
            self.candidate = copy.deepcopy(original); change()
            with self.assertRaises(p.UpdateError): self.release()

    def test_upstream_component_and_recipe_changes_require_qualification(self):
        version = platform_update_images.template_versions(self.inputs)
        changed = copy.deepcopy(self.inputs); changed['engine_commit'] = 'b'*40
        self.assertEqual(version,platform_update_images.template_versions(changed))
        changed['tailscale']['version'] = '1.2.4'
        self.assertNotEqual(version,platform_update_images.template_versions(changed))
        root = Path(__file__).resolve().parents[2]
        profile = json.loads((root/'ansible/update-profiles/router-alpine-v3.json').read_text())
        qualified = infrastructure_images.qualify_profile(profile,repo=root)
        self.assertRegex(qualified['recipe_sha256'],'^[0-9a-f]{64}$')


if __name__ == '__main__': unittest.main()
