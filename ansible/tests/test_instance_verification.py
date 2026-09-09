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

    def test_recovery_manifest_reconstructs_only_in_detached_controller_tree(self):
        from contextlib import ExitStack
        m=self.m
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp)
            for name in ('INSTANCE','REPO_ROOT','ROLLBACK_ROOT','SOURCE_ROOT','RECOVERY_ROOT','RECOVERY_MANIFEST_ROOT'):
                path=root/name;path.mkdir();stack.enter_context(patch.object(m,name,path))
            for name in ('DEPLOYMENT','REGISTRY','CONTROLLER_HA'):
                path=root/(name+'.yml');path.write_text('{}\n');stack.enter_context(patch.object(m,name,path))
            for name in ('klokast-instance.json','klokast.lock.json'):(m.INSTANCE/name).write_text('{}\n')
            binding={'plan':self.plan(),'state':self.fixture.state(True),'binary_sha256':'b'*64,'builder_receipt_sha256':'c'*64}
            binding['plan']['plan_sha256']='a'*64
            for name in m.VERIFICATION_BINDING_FIELDS:
                path=root/name
                if name=='build_dir':
                    path.mkdir();(path/'klokast').write_bytes(b'sealed');(path/'receipt.json').write_text('{}\n')
                else:path.write_text('{}\n')
                binding[name]=str(path)
            stack.enter_context(patch.object(m,'validate_inputs_v3',return_value=binding))
            stack.enter_context(patch.object(m,'controller_identity_status',return_value={'checked':True}))
            stack.enter_context(patch.object(m,'verification_source_history',return_value=[]))
            with redirect_stdout(io.StringIO()) as output:m.verification_recovery_manifest(SimpleNamespace())
            result=json.loads(output.getvalue())
            manifest=json.loads(Path(result['path']).read_text())
            self.assertEqual(manifest['reconstruction'],'verified-detached-copy')
            self.assertFalse(manifest['activated'])
            self.assertFalse(manifest['credentials_copied'])
            self.assertTrue(manifest['credential_reseed'])
            self.assertEqual(Path(result['path']).stat().st_mode & 0o777,0o400)
            self.assertEqual(list(m.RECOVERY_MANIFEST_ROOT.glob('reconstruction-*')),[])

    def test_unsigned_collection_rechecks_source_and_cleans_runtime(self):
        from contextlib import ExitStack
        import os
        m=self.m
        for drift in (False,True):
            with self.subTest(source_changes=drift),tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
                root=Path(tmp);p=self.plan();p['plan_sha256']='a'*64
                state=self.fixture.state(True)
                binding={'plan':p,'state':state,'binary_sha256':'b'*64,'builder_receipt_sha256':'c'*64,
                         **{k:str(root/k) for k in m.VERIFICATION_BINDING_FIELDS}}
                binding['recovery_path']=str(root/('d'*64+'.json'))
                status={'source':'instance_specification_v1','controllers':self.fixture.pair,
                        'authority_state_sha256':state['authority_state_sha256']}
                final=copy.deepcopy(binding)
                if drift:final['binary_sha256']='e'*64
                for name,value in [('BOX_RUNTIME_ROOT',root/'box'),('RUNTIME_ROOT',root/'policy')]:stack.enter_context(patch.object(m,name,value))
                stack.enter_context(patch.object(m.os,'chown'))
                stack.enter_context(patch.object(m,'smith_gid',return_value=0))
                stack.enter_context(patch.object(m,'validate_inputs_v3',side_effect=[binding,final]))
                stack.enter_context(patch.object(m,'controller_identity_status',return_value=status))
                stack.enter_context(patch.object(m,'verify_controller_pair',return_value=self.fixture.pair))
                stack.enter_context(patch.object(m,'verify_declared_runners',return_value=[{'hostname':'boxa-ops-airunner'}]))
                stack.enter_context(patch.object(m,'verification_adoption_evidence',return_value='f'*64))
                routers=stack.enter_context(patch.object(m,'run_box_resource'))
                def consumers(binding,work):
                    self.assertEqual(work.stat().st_mode & 0o777,0o750)
                    return {k:'a'*64 for k in ('compiler_sha256','router_variables_sha256','inventory_sha256','inventory_policy_sha256')},work/'registry.json'
                stack.enter_context(patch.object(m,'verification_consumers',side_effect=consumers))
                commands=[]
                def run(command,**kwargs):
                    commands.append(command)
                    if command[0]==m.RENDERER:Path(command[-1]).write_bytes(b'policy')
                    elif command[0]==m.MUTATION_HELPER and command[1]=='get-before':(root/'policy'/command[2]/'live.body').write_bytes(b'policy')
                    elif command[0]!=m.MUTATION_HELPER or command[1]!='validate-candidate':self.fail('unexpected command '+str(command))
                    return Mock(returncode=0,stdout='',stderr='')
                stack.enter_context(patch.object(m,'run',side_effect=run))
                previous=os.umask(0o077)
                try:
                    if drift:
                        with self.assertRaisesRegex(m.ApplyError,'changed during verification'):m.verification_collect(SimpleNamespace(),'verification-unsigned-test')
                    else:
                        collected=m.verification_collect(SimpleNamespace(),'verification-unsigned-test')
                        self.assertEqual(collected['evidence']['live_policy_sha256'],m.sha256_bytes(b'policy'))
                finally:os.umask(previous)
                self.assertEqual([v.args[2] for v in routers.call_args_list],['verify-box-access','verify-box-access'])
                self.assertEqual(list((root/'box').iterdir()),[])
                self.assertEqual(list((root/'policy').iterdir()),[])

if __name__=='__main__':unittest.main()
