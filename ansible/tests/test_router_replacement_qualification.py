"""A/B readiness may select only a retired exact old/new/old diagnostic."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import router_replacement_qualification as qualification
from router_transaction import TransactionError


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.request = {'kind':'klokast.router-transaction-request.v1',
            'role':'router','box':'boxa','operation_id':'a'*24,
            'engine_commit':'b'*40,'policy_sha256':'1'*64,
            'accepted_sha256':'2'*64,'old_sha256':'3'*64,
            'candidate_sha256':'4'*64,'cutover_seconds':900,'recovery_seconds':900}
        self.old = {'record_sha256':'3'*64,'disk':{'path':'/dev/vg0/lv_router',
            'uuid':'old','bytes':2147483648},'packages':{'dnsmasq':'old'},
            'kernel_release':'old-kernel','configuration_files':{'etc/dnsmasq.conf':'8'*64}}
        self.candidate = {'record_sha256':'4'*64,'template_operation':'c'*24,
            'release_sha256':'5'*64,'packages':{'dnsmasq':'new'},
            'kernel_release':'new-kernel'}
        self.release = {'receipt_sha256':'5'*64,'runtime_packages':self.candidate['packages'],
            'inputs':{'inputs_sha256':'6'*64}}
        self.preparation = {'kind':'klokast.router-replacement-preparation.v1',
            'box':'boxa','operation_id':'a'*24,'engine_commit':'b'*40,
            'policy_sha256':'1'*64,'old_sha256':'3'*64,
            'accepted_assignment_sha256':'2'*64,'template_operation':'c'*24,
            'release_sha256':'5'*64,'inputs_sha256':'6'*64}
        self.accepted = {'assignment':{'record_sha256':'2'*64},
                         'generation':self.old}
        self.host = {'kind':'klokast.router-compatibility-host.v2',
            'box':'boxa','operation_id':'d'*24,'engine_commit':'b'*40,
            'inputs_sha256':'6'*64,'template':{'operation':'c'*24},
            'source':{'accepted':self.accepted,'disk':copy.deepcopy(self.old['disk'])},
            'guest':{'source_packages':self.old['packages'],
                'source_kernel_release':'old-kernel',
                'source_files':self.old['configuration_files'],
                'runtime_packages':self.candidate['packages'],
                'kernel_release':'new-kernel'}}
        self.result = {'kind':'klokast.router-compatibility-result.v2',
            'box':'boxa','operation_id':'d'*24,'engine_commit':'b'*40,
            'inputs_sha256':'6'*64,'source':self.host['source'],
            'template_operation':'c'*24,'source_packages':self.old['packages'],
            'runtime_packages':self.candidate['packages'],
            'candidate_modes':['initial-install','replacement'],
            'phases':list(qualification.PHASES),'success':True,
            'production_identity':False,'enrollment_tested':False,
            'replacement_authorized':False,
            'candidate_disk':{'path':'/dev/vg0/routergen_'+'d'*24,
                'uuid':'disposable','bytes':2147483648}}
        self.phases = {name:{'kind':'klokast.router-compatibility-phase.v2',
            'phase':name,'operation_id':'d'*24,'inputs_sha256':'6'*64,
            'success':True,'production_identity':False}
            for name in qualification.PHASES}
        common = {'wan_dhcp_identity','wan_lease','lan_lease','lan_dns'}
        base = common | {'dhcp_identity_continuity','copied_timestamps',
                         'generation_identity_preserved','fresh_wan_negotiation'}
        self.phases['seed']['tests'] = dict.fromkeys(common | {'expiry_lease_created'},True)
        self.phases['new']['tests'] = dict.fromkeys(base |
            {'expired_lease_removed','old_client_renewed'},True)
        self.phases['old']['tests'] = dict.fromkeys(base,True)
        for name in ('seed','new','old'):
            self.phases[name].update(enrollment_tested=False,
                packages=self.candidate['packages'] if name == 'new' else self.old['packages'],
                state={'files':{},'generation_identity':{},'lan_leases':{}})
        for name in ('forward','reverse'):
            self.phases[name].update(copy_complete=True,copy_receipt_sha256='7'*64)
        self.lifecycle = {'operation_id':'d'*24,'stage':'cleaned'}
        self.retired = {'operation_id':'d'*24,'stage':'retired',
            'path':self.result['candidate_disk']['path'],'uuid':'disposable'}
        patch = mock.patch.object(qualification.generations,'pair')
        patch.start();self.addCleanup(patch.stop)

    def qualify(self):
        return qualification.records(self.request,self.old,self.candidate,
            self.preparation,self.accepted,self.release,{'preflight':True},
            self.host,self.result,self.phases,self.lifecycle,self.retired,'10.1.1.1')

    def test_exact_pair_builds_dom0_readiness(self):
        compatibility,copy_record,ready = self.qualify()
        self.assertEqual(compatibility['candidate_sha256'],'4'*64)
        self.assertEqual(copy_record['forward_receipt_sha256'],'7'*64)
        self.assertEqual(ready['compatibility_sha256'],
            qualification.generations.digest(compatibility))

    def test_changed_source_phase_or_retirement_refuses(self):
        for change in ('source','phase','receipt','retirement'):
            with self.subTest(change=change):
                saved = copy.deepcopy((self.host,self.phases,self.lifecycle))
                if change == 'source':
                    self.host['source']['disk']['uuid'] = 'wrong'
                elif change == 'phase':
                    self.phases['new']['tests']['fresh_wan_negotiation'] = False
                elif change == 'receipt':
                    self.phases['reverse']['copy_receipt_sha256'] = 'wrong'
                else:
                    self.lifecycle['stage'] = 'detached'
                with self.assertRaises(TransactionError):
                    self.qualify()
                self.host,self.phases,self.lifecycle = saved


if __name__ == '__main__':
    unittest.main()
