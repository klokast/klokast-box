"""Plan v8 and nonce-bound read-only verification, with real evidence files."""
import copy
import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_inventory_source as fixture


class InstanceVerificationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.InventorySourceTest()
        self.fixture.setUp()
        self.m = self.fixture.m

    def plan(self):
        m = self.m
        p = self.fixture.plan(True)
        for k in ('compatible', 'compatibility', 'compatibility_inputs', 'migration_target', 'connectivity_target', 'selected_box'):
            p.pop(k, None)
        p.update(schema_version=8, kind=m.KIND_PLAN_V8, diagnostics=[], refusals=[], legacy_removal_ready=False)
        p['projection']['control_plane']['airunners'] = p['inventory']['airunners']
        p['action_groups'] = m.verification_groups(p)
        p['actions'] = []
        for g in p['action_groups']:
            for scope in g['scopes']:
                op = 'verify_instance_authority'
                p['actions'].append({'id': op+'-'+m.sha256_bytes((op+'\0'+scope).encode())[:16], 'scope': scope, 'operation': op,
                    'executor': m.VERIFICATION_EXECUTOR, 'authority_before': 'instance_specification_v1', 'authority_after': 'instance_specification_v1',
                    'preconditions': m.VERIFICATION_PRECONDITIONS, 'rollback': {'strategy':'no_mutation','authority':'instance_specification_v1'}})
        p['actions'].sort(key=lambda a:a['id'])
        return p

    def store_plan(self, root, p):
        p = copy.deepcopy(p)
        p.pop('plan_sha256',None)
        p['plan_sha256'] = self.m.inventory_digest(p)
        path = root / p['instance']['commit'] / (p['plan_sha256']+'.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.m.canonical(p)+'\n')
        return path

    def test_plan_v8_closed_coverage_and_old_versions(self):
        m = self.m
        with tempfile.TemporaryDirectory() as tmp, patch.object(m,'PLAN_ROOT',Path(tmp)):
            p = self.plan()
            path = self.store_plan(Path(tmp), p)
            self.assertEqual(m.verify_plan_v8(path)[2]['executor'], m.VERIFICATION_EXECUTOR)
            for mutation in (
                lambda p:p.update(compatibility_inputs=[]), lambda p:p.update(compatibility=None),
                lambda p:p.update(schema_version=7,kind=m.KIND_PLAN_V7), lambda p:p.update(legacy_removal_ready=True),
                lambda p:p['action_groups'].pop(), lambda p:p['actions'].pop(),
                lambda p:p['actions'][0].update(operation='adopt_instance_specification'),
                lambda p:p['authority_state']['setting_groups'][0].update(source='legacy_platform_resources'),
                lambda p:p['inventory']['airunners'].append('other-ops'),
                lambda p:p['projection']['control_plane']['active_controller'].update(hostname='wrong-ops'),
            ):
                bad=copy.deepcopy(p); mutation(bad)
                with self.assertRaises((m.ApplyError,ValueError)):
                    m.verify_plan_v8(self.store_plan(Path(tmp),bad))

    def collected(self):
        p = self.plan()
        return {'evidence':{k:'a'*64 for k in self.m.VERIFICATION_EVIDENCE_FIELDS},
                'binding':{k:'/protected/'+k for k in self.m.VERIFICATION_BINDING_FIELDS},
                'controller_pair':self.fixture.pair, 'action_groups':p['action_groups'],
                'private_commit':'b'*40, 'engine_commit':'c'*40}

    def test_execution_consumes_nonce_before_checks_and_refuses_exact_replay(self):
        m=self.m
        collected=self.collected()
        intent=m.verification_intent(collected,'verification-test-nonce',m.now_utc())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); pre=root/'pre'; pre.mkdir(); directory=pre/intent['nonce']; directory.mkdir()
            for name, value in [('intent',intent),('binding',collected['binding'])]:
                (directory/(name+'.json')).write_text(m.canonical(value)+'\n')
            def collect(*args):
                self.assertTrue((root/'nonces'/intent['nonce']).is_file())
                return collected
            with patch.object(m,'PREFLIGHT_ROOT',pre), patch.object(m,'NONCE_ROOT',root/'nonces'), patch.object(m,'EXECUTION_ROOT',root/'receipts'), patch.object(m,'smith_gid',return_value=0), patch.object(m,'verify_signature'), patch.object(m,'verification_collect',side_effect=collect), patch.object(m,'publish_authority') as publish, patch.object(m,'append_audit'), patch.object(m.os,'chown'), redirect_stdout(io.StringIO()):
                m.verification_execute(SimpleNamespace(),intent)
                receipt=json.loads(next((root/'receipts'/intent['nonce']).glob('*.json')).read_text())
                self.assertEqual(receipt['kind'],'klokast.apply-execution.v6')
                self.assertEqual(receipt['result'],'verified')
                self.assertEqual(receipt['authority_state_sha256'],intent['evidence']['authority_state_sha256'])
                publish.assert_not_called()
                with self.assertRaisesRegex(m.ApplyError,'already used'):
                    m.verification_execute(SimpleNamespace(),intent)

    def test_changed_evidence_and_storage_failure_burn_nonce_without_publication(self):
        m=self.m
        for changed in ('private_inputs_sha256','inventory_policy_sha256','source_status_sha256','controller_roles_sha256','runner_identities_sha256','storage'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); collected=self.collected()
                intent=m.verification_intent(collected,'verification-test-nonce',m.now_utc())
                directory=root/intent['nonce'];directory.mkdir()
                for name,value in [('intent',intent),('binding',collected['binding'])]: (directory/(name+'.json')).write_text(m.canonical(value)+'\n')
                new=copy.deepcopy(collected)
                if changed!='storage':new['evidence'][changed]='f'*64
                with patch.object(m,'PREFLIGHT_ROOT',root),patch.object(m,'NONCE_ROOT',root/'nonces'),patch.object(m,'verify_signature'),patch.object(m,'verification_collect',return_value=new),patch.object(m,'store_identity_receipt',side_effect=OSError('disk full')) as store,patch.object(m,'publish_authority') as publish:
                    with self.assertRaisesRegex(m.ApplyError,'fresh approval'):
                        m.verification_execute(SimpleNamespace(),intent)
                    self.assertTrue((root/'nonces'/intent['nonce']).is_file())
                    publish.assert_not_called()
                    if changed!='storage':store.assert_not_called()

    def test_expiry_unknown_intent_and_missing_adoption_archive(self):
        m=self.m
        intent=m.verification_intent(self.collected(),'verification-test-nonce',m.now_utc()-dt.timedelta(minutes=11))
        with self.assertRaisesRegex(m.ApplyError,'expired'):m.validate_verification_intent(intent)
        intent=m.verification_intent(self.collected(),'verification-test-nonce',m.now_utc())
        intent['legacy_registry_sha256']='a'*64
        with self.assertRaisesRegex(m.ApplyError,'unknown'):m.validate_verification_intent(intent)
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'PREFLIGHT_ROOT',Path(tmp)):
            with self.assertRaises(m.ApplyError):m.verification_adoption_evidence(self.fixture.state(True))

    def test_consumer_commands_are_verification_only(self):
        import inspect
        m=self.m
        text=inspect.getsource(m.verification_collect)+inspect.getsource(m.verification_consumers)+inspect.getsource(m.verification_execute)
        for forbidden in ('publish_authority(', 'apply-box-access', 'rc-service', 'rollback_to_legacy', 'render_policies('):
            self.assertNotIn(forbidden,text)
        self.assertIn('ANSIBLE_CACHE_PLUGIN=memory',text)
        self.assertIn('"get-before"',text)
        self.assertIn('"validate-candidate"',text)

if __name__=='__main__':unittest.main()
