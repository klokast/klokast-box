"""Routing choices are data, and installed choices change only at replacement."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import routing_profile as profile
import vpn_routing_runtime as runtime

CN = {'domain-list-url':'https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/gfw.txt',
      'bootstrap-dns':['223.5.5.5','119.29.29.29'],
      'vpn-dns':['https://1.1.1.1/dns-query','https://8.8.8.8/dns-query']}
FR = dict(CN, **{'domain-list-url':None, 'bootstrap-dns':['1.1.1.1','8.8.8.8']})


class RoutingProfiles(unittest.TestCase):
    def test_urls_remain_replaceable_without_provider_allowlist(self):
        value=copy.deepcopy(CN)
        value['domain-list-url']='https://lists.example.org/france.yaml'
        self.assertEqual(profile.validate(value),value)
        self.assertNotEqual(profile.providers(value)['selected']['path'],profile.providers(CN)['selected']['path'])
        self.assertEqual(profile.providers(FR),{})

    def test_invalid_inputs_cannot_select_another_transport_or_private_endpoint(self):
        for key, value in [('bootstrap-dns',['127.0.0.1','8.8.8.8']), ('bootstrap-dns',['8.8.8.8']*2),
                           ('bootstrap-dns',[{},[]]), ('vpn-dns',['8.8.8.8','1.1.1.1']),
                           ('vpn-dns',['https://1.1.1.1/dns-query#DIRECT','https://8.8.8.8/dns-query']),
                           ('domain-list-url','http://example.org/list'), ('domain-list-url','https://100.64.0.1/list'),
                           ('domain-list-url','https://user:password@example.org/list')]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError): profile.validate(dict(CN,**{key:value}))

    def test_existing_vm_ignores_desired_edits_during_maintenance_and_verification(self):
        for readonly in (True,False):
            with patch.object(runtime,'remote',return_value={'state':'complete','profile':CN,'uuid':'guest'}),patch.object(runtime,'guest_profile') as guest:
                selected,_=runtime.select('boxa',FR,adopt=True,readonly=readonly)
                self.assertEqual(selected,CN)
                guest.assert_not_called()

    def test_new_vm_uses_desired_profile(self):
        with patch.object(runtime,'remote',return_value={'state':'absent','profile':None}):
            self.assertEqual(runtime.select('boxa',FR)[0],FR)

    def test_incomplete_replacement_blocks_maintenance_but_health_uses_recorded_inputs(self):
        with patch.object(runtime,'remote',return_value={'state':'booted','profile':CN}):
            with self.assertRaisesRegex(RuntimeError,'incomplete'): runtime.select('boxa',FR,adopt=True)
            self.assertEqual(runtime.select('boxa',FR,readonly=True)[0],CN)

    def test_legacy_adoption_requires_live_values_to_match_instance(self):
        with patch.object(runtime,'remote',return_value={'state':'ready','profile':None,'uuid':'guest'}) as remote,patch.object(runtime,'guest_profile',return_value=(CN,'guest')):
            with self.assertRaisesRegex(RuntimeError,'differs from Instance'): runtime.select('boxa',FR,adopt=True)
            self.assertEqual(remote.call_count,1)

    def test_disabled_gateway_does_not_read_old_assignment(self):
        with patch.object(runtime,'remote') as remote:
            self.assertEqual(runtime.installed_profiles({'boxes':{'boxa':{'routing_profile':CN}}}),{})
            remote.assert_not_called()

    def test_secret_free_profile_extraction_requires_vpn_dns(self):
        config={'rule-providers':profile.providers(CN),'dns':{'nameserver':[v+'#VPN' for v in CN['vpn-dns']], 'proxy-server-nameserver':CN['bootstrap-dns']}}
        self.assertEqual(profile.from_config(config),CN)
        config['dns']['nameserver'][0]='https://1.1.1.1/dns-query'
        with self.assertRaisesRegex(ValueError,'VPN transport'): profile.from_config(config)


class FrozenFinalizer(unittest.TestCase):
    def test_new_profile_changes_routing_but_keeps_credentials_and_separates_cache(self):
        import yaml
        from test_infrastructure_guest import load, ROOT
        finalizer=load('routing_finalizer_test', ROOT/'ansible/roles/vm-template-builder/files/vm-service-finalize')
        import platform_vpn_egress as vpn
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'etc/vpn-egress/config.yml'; path.parent.mkdir(parents=True)
            original={'secret':'fixture-secret','proxy-providers':{'relays':{'payload':[{'password':'fixture-password'}]}},
                      'rules':vpn.routing_rules(CN),'rule-providers':{'gfw':dict(profile.providers(CN)['selected'],path='./rules/gfw.yaml')},
                      'dns':{'nameserver':['old'],'proxy-server-nameserver':CN['bootstrap-dns']}}
            path.write_text(yaml.safe_dump(original)); path.chmod(0o640)
            cache=root/'var/lib/vpn-egress/rules/gfw.yaml'; cache.parent.mkdir(parents=True); cache.write_text('payload: [example.org]')
            changed=dict(FR,**{'domain-list-url':'https://lists.example.org/fr.yaml'})
            frozen={'bootstrap_dns':changed['bootstrap-dns'],'vpn_routing':{'rules':vpn.routing_rules(changed),
                    'rule-providers':profile.providers(changed),'nameserver':[v+'#VPN' for v in changed['vpn-dns']]}}
            finalizer.apply_vpn_routing(frozen,root)
            actual=yaml.safe_load(path.read_text())
            self.assertEqual(profile.from_config(actual),changed)
            self.assertEqual(actual['secret'],original['secret'])
            self.assertEqual(actual['proxy-providers'],original['proxy-providers'])
            self.assertEqual(path.stat().st_mode & 0o777,0o640)
            self.assertFalse((root/'var/lib/vpn-egress'/profile.providers(changed)['selected']['path']).exists())
            self.assertTrue(cache.exists())

class RoutingAssignmentSelection(unittest.TestCase):
    def test_pending_resume_uses_frozen_profile_only_after_old_guest_stops(self):
        import hashlib, json
        import vpn_routing_assignment as assignment
        op='a'*24
        request={'box':'boxa','role':'vpn-egress'}
        digest=hashlib.sha256(json.dumps(request,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        base=assignment.BASE/'assignment.json'
        work=assignment.TRANSACTIONS/'operations'/op
        documents={base:{'kind':'klokast.infrastructure-assignment.v1','box':'boxa','role':'vpn-egress','stage':'ready','uuid':'old','routing_profile':CN},
                   assignment.TRANSACTIONS/'active/vpn-egress.json':{'operation_id':op,'request_sha256':digest},
                   work/'request.json':request,
                   work/'service.json':{'kind':'klokast.vm-service-update.v1','operation_id':op,'box':'boxa','role':'vpn-egress','old_uuid':'old','new_uuid':'new','requested_configuration':{'routing_profile':FR,'before':{'routing_profile':CN}}}}
        for stage, expected in [('armed',CN),('stopped',FR),('booted',FR),('complete',FR),('recovered',CN)]:
            documents[work/'journal.json']={'stage':stage}
            with self.subTest(stage=stage),patch.object(assignment,'read',side_effect=lambda path:copy.deepcopy(documents[path])),patch.object(Path,'exists',autospec=True,side_effect=lambda path:path in documents):
                self.assertEqual(assignment.selection('boxa')[2]['profile'],expected)


if __name__ == '__main__':
    unittest.main()
