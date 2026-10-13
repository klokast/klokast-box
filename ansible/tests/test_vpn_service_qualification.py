"""A VPN image cannot qualify on base-image tests alone."""
import copy
import unittest
from test_platform_updates import load_cli
from platform_updates import UpdateError, verify_vpn_service_qualification


class VPNServiceEvidence(unittest.TestCase):
    def test_missing_failed_or_same_boot_evidence_is_rejected(self):
        checks = dict.fromkeys(('numeric_identity','file_access','openrc_service','state_copy'),True)
        first = {'tests':checks, 'production_data_used':False, 'boot_id':'11111111-1111-4111-8111-111111111111'}
        second = dict(first,boot_id='22222222-2222-4222-8222-222222222222')
        normal = {'service_replacement':first}
        candidate = {'inputs_sha256':'a'*64, 'boot_test':{'operation_id':'b'*24,
            'service_replacement':{'finalized':True,'invalid_groups_refused':True,'production_data_used':False},
            'service_reboot_test':{'success':True,'operation_id':'b'*24,'inputs_sha256':'a'*64,'service_replacement':second}}}
        verify_vpn_service_qualification(candidate,normal)
        current=copy.deepcopy(candidate)
        current['boot_test']['service_replacement']['clean_root']=True
        verify_vpn_service_qualification(current,normal)
        current['boot_test']['service_replacement']['clean_root']=False
        with self.assertRaises(UpdateError):verify_vpn_service_qualification(current,normal)
        for change in ('missing','failed','same-boot','wrong-input','missing-ownership','no-negative-test'):
            value=copy.deepcopy(candidate)
            boot=value['boot_test']; reboot=boot['service_reboot_test']
            if change=='missing': del boot['service_replacement']
            elif change=='failed': reboot['success']=False
            elif change=='same-boot': reboot['service_replacement']['boot_id']=first['boot_id']
            elif change=='wrong-input': reboot['inputs_sha256']='c'*64
            elif change=='missing-ownership': del reboot['service_replacement']['tests']['file_access']
            else: boot['service_replacement']['invalid_groups_refused']=False
            with self.subTest(change=change),self.assertRaises(UpdateError):
                verify_vpn_service_qualification(value,normal)
