"""Clean VPN inputs, identity copying, rebuild and scoped nightly selection."""
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_infrastructure_guest import ROOT, load
sys.path.insert(0,str(ROOT/'ansible/roles/vm-template-builder/files'))
import vm_vpn_reconstruct as guest
import vm_guest_identity as identity
import vpn_egress_provisioning as vpn
import vm_service_updates as updates
from test_routing_profile import FR


class CleanIdentity(unittest.TestCase):
    def test_only_allowed_identity_survives_package_configuration_and_state_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary)/'old';target=Path(temporary)/'new';target.mkdir()
            for name in (*identity.REQUIRED, 'etc/machine-id', 'etc/init.d/vpn-egress','usr/local/bin/mihomo','etc/vpn-egress/config.yml',
                         'opt/extra','home/neo/extra','lib/apk/db/installed','var/lib/vpn-egress/cache'):
                path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('old '+name)
            packages=target/'lib/apk/db/installed';packages.parent.mkdir(parents=True);packages.write_text('qualified packages')
            copied=guest.preserve_identity(source,target)
            self.assertEqual(set(copied),identity.REQUIRED|{'etc/machine-id'})
            self.assertEqual(packages.read_text(),'qualified packages')
            for name in ('etc/init.d/vpn-egress','usr/local/bin/mihomo','etc/vpn-egress','opt','home','var/lib/vpn-egress'):
                self.assertFalse((target/name).exists(),name)
            for name in identity.REQUIRED:self.assertEqual((source/name).read_bytes(),(target/name).read_bytes())

    def test_legacy_machine_id_absence_is_preserved_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary)/'old';target=Path(temporary)/'new'
            for name in identity.REQUIRED:
                path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('identity')
            extra=target/'etc/machine-id';extra.parent.mkdir(parents=True);extra.write_text('new identity must not replace recorded absence')
            guest.preserve_identity(source,target)
            self.assertFalse(extra.exists())

    def test_missing_or_linked_identity_is_not_inferred(self):
        for linked in (False, True):
            with tempfile.TemporaryDirectory() as temporary:
                source=Path(temporary)/'old';target=Path(temporary)/'new';source.mkdir();target.mkdir()
                if linked:
                    path=source/'var/lib/tailscale/tailscaled.state';path.parent.mkdir(parents=True);path.symlink_to('/etc/passwd')
                with self.assertRaisesRegex(RuntimeError,'identity'):guest.preserve_identity(source,target)

    def test_domain_list_is_validated_before_it_can_seed_the_new_cache(self):
        self.assertEqual(vpn.validate_domain_list(b'payload: ["+.example.org", example.net]')['payload'][1],'example.net')
        for data in (b'payload: []',b'payload: ["IP-CIDR,0.0.0.0/0,DIRECT"]',b'payload: [null]',b'[]'):
            with self.subTest(data=data),self.assertRaises(ValueError):vpn.validate_domain_list(data)
        selected=dict(FR,**{'domain-list-url':'https://example.org/domains.yaml'})
        with patch.object(vpn,'download',side_effect=RuntimeError('unavailable')):
            with self.assertRaisesRegex(RuntimeError,'unavailable or invalid'):vpn.domain_list(selected)
        self.assertIsNone(vpn.domain_list(FR))


class RebuildAndSelection(unittest.TestCase):
    def test_same_image_rebuild_prepares_current_inputs_and_starts_clean_reconstruction(self):
        before={'root_partition':'','accounts':{'neo':[1000,1000],'vpn-egress':[102,103]},'source_files':{},'workloads':{}}
        receipt={'files':{'inputs.json':{'profile_sha256':'qualified'},'release-evidence.json':{'kernel_release':'6.test','packages':{}}}}
        snapshot={'instance':{'boxes':{'boxa':{'routing-profile':FR}}},
                  'rendered':{'projection':{'registry':{'boxes':{'boxa':{}}},'registry_sha256':'registry'}}}
        with tempfile.TemporaryDirectory() as temporary:
            catalog=Path(temporary);(catalog/'build.lock').touch()
            patches=[patch.object(updates,'authority',return_value='running'),
                patch.object(updates.inputs,'revision',return_value='c'*40),
                patch.object(updates,'step',return_value={'pending':[],'installed':{'image':'a'*24}}),
                patch.object(updates.images,'local_action',side_effect=[{'state':'candidate-reused','operation_id':'a'*24},receipt]),
                patch.object(updates.vm_local_images,'validate'),patch.object(updates.vm_local_images,'import_receipt'),
                patch.object(updates.images,'qualify_profile',return_value={}),
                patch.object(updates,'digest',return_value='qualified'),
                patch.object(updates.vm_service_probe,'probe',return_value=before),
                patch.object(updates.platform_source,'snapshot',return_value=snapshot),
                patch.object(updates.model,'registry_box_configs',return_value={'boxa':{'vpn_egress':{}}}),
                patch.object(updates.vpn_routing_runtime,'installed_profiles',return_value={}),
                patch.object(updates.vpn_routing_runtime,'installed_air_bootstrap',return_value={}),
                patch.object(updates.vpn_egress_provisioning,'prepare_configuration',return_value={'vpn_egress_binary':'fixture','vpn_egress_config':{}}),
                patch.object(updates.vpn_egress_provisioning,'creation_config',return_value={'role':'vpn-egress'}),
                patch.object(updates.inputs,'freeze',return_value=(catalog,'e'*64)),patch.object(updates.inputs,'save'),
                patch.object(updates,'finish_frozen',return_value={'state':'complete','replaced':True}),
                patch.object(updates.runtime,'vm_update_installation_lock',side_effect=contextlib.nullcontext),
                patch.object(updates.vpn_egress_provisioning,'configuration_signature',return_value='signature')]
            with contextlib.ExitStack() as stack:
                mocks=[stack.enter_context(p) for p in patches]
                # The fixed discovery catalog is the existing controller-local lock path.
                original_path=updates.Path
                def paths(value):return catalog if value=='/var/lib/klokast/updates/discovery' else original_path(value)
                stack.enter_context(patch.object(updates,'Path',side_effect=paths))
                result=updates.execute('boxa','vpn-egress',rebuild=True)
            self.assertTrue(result['replaced'])
            self.assertEqual(mocks[3].call_args_list[0].args[3],'prepare')
            mocks[8].assert_called_once_with('boxa','vpn-egress',repair=True)
            variables=mocks[15].call_args.args[1]
            self.assertIn('vpn_creation',variables['service_configuration'])
            self.assertIn('vpn_runtime',variables);self.assertIn('vpn_network',variables)

    def test_rebuild_cannot_select_resume_or_a_shared_guest(self):
        with self.assertRaises(RuntimeError):updates.execute('boxa','vpn-egress',rebuild=True,resume='a'*24)
        with self.assertRaises(RuntimeError):updates.execute('boxa','bak',rebuild=True)

    def test_nightly_role_selection_excludes_every_other_role(self):
        nightly=load('vpn_nightly_selection',ROOT/'ansible/bin/ops-controller-nightly')
        view={'instance':{'boxes':{'boxa':{'vpn-egress':{}},'boxb':{}}}}
        view['instance']['boxes']['boxa']['vpn-egress']={'clients':[]}
        with patch.object(nightly.platform_source,'snapshot',return_value=view), \
             patch.object(updates,'execute',return_value={'state':'planned'}) as execute, \
             patch.object(updates,'authority'),patch.object(nightly.runtime,'vm_update_installation_lock',side_effect=contextlib.nullcontext):
            nightly.service_runs(True,roles=['vpn-egress','vpn-egress'])
        execute.assert_called_once_with('boxa','vpn-egress',dry_run=True,lock_held=True)

    def test_unrelated_source_changes_do_not_report_pending_vpn_configuration(self):
        record={'requested_configuration':{'vpn_intent_sha256':'same'}}
        snapshot={'rendered':{'projection':{'registry':{}}}}
        with patch.object(updates.platform_source,'snapshot',return_value=snapshot), \
             patch.object(updates.model,'registry_box_configs',return_value={'boxa':{}}), \
             patch.object(updates.vpn_egress_provisioning,'configuration_signature',return_value='same'):
            self.assertFalse(updates.configuration_needed(record,'boxa','vpn-egress','new-code','new-instance'))
        with patch.object(updates.platform_source,'snapshot',return_value=snapshot), \
             patch.object(updates.model,'registry_box_configs',return_value={'boxa':{}}), \
             patch.object(updates.vpn_egress_provisioning,'configuration_signature',return_value='changed'):
            self.assertTrue(updates.configuration_needed(record,'boxa','vpn-egress','new-code','new-instance'))
