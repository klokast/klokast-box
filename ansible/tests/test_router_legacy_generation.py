"""A legacy baseline record must not claim uninspected template state."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_generations as generations
import router_legacy_generation as legacy
from router_transaction import TransactionError
import test_router_updates as fixtures


class LegacyGenerationTests(unittest.TestCase):
    def fixture(self):
        guest,dom0=fixtures.LegacyBaselineTests().fixture()
        item=guest['include_files'].pop('/etc/example.nft')
        paths=('/etc/klokast/app-resources/router-forward.nft',
               '/etc/klokast/app-resources/router-forward.d/000-empty.nft')
        guest['include_files']={path:copy.deepcopy(item) for path in paths}
        guest['expected_includes']['files']={path:'b'*64 for path in paths}
        guest['packages']=dict.fromkeys(('tailscale','dhcpcd','dnsmasq','nftables','openssh-keygen'),'1-r0')
        dom0['xen'].update(memory=512,vcpus=1,vif=['bridge=br-wan,mac=00:16:3e:00:00:01'])
        dom0['logical_volumes']['report'][0]['lv'][0].update(lv_size='2147483648',origin='')
        dom0['boot_artifacts']={key:{'path':'/mnt/dom0_data/xen_images/router-'+name,
                                   'sha256':'a'*64,'bytes':1234}
                                for key,name in (('kernel','kernel'),('ramdisk','initramfs'))}
        dom0['xen'].update(kernel=dom0['boot_artifacts']['kernel']['path'],
                           ramdisk=dom0['boot_artifacts']['ramdisk']['path'])
        return dict(box='boxa',operation='f'*24,engine_commit='c'*40,guest=guest,dom0=dom0)

    def test_inspected_legacy_record_has_only_proved_configuration(self):
        args=self.fixture()
        result=legacy.assemble(**args)
        self.assertEqual(generations.generation(result,'boxa'),result)
        self.assertEqual(result['origin'],'legacy')
        self.assertNotIn('etc/resolv.conf',result['configuration_files'])
        self.assertIn('etc/klokast/app-resources/router-forward.d/000-empty.nft',result['configuration_files'])

    def test_drift_and_foreign_disk_or_boot_identity_block_record(self):
        for change in (lambda v:v['guest']['configuration_files']['/etc/dnsmasq.conf'].update(sha256='0'*64),
                       lambda v:v['dom0']['logical_volumes']['report'][0]['lv'][0].update(lv_size='1'),
                       lambda v:v['dom0']['boot_artifacts']['kernel'].update(path='/mnt/dom0_data/other'),
                       lambda v:v['guest']['packages'].update(openssh='1-r0')):
            args=copy.deepcopy(self.fixture())
            change(args)
            with self.subTest(change=change),self.assertRaises((TransactionError,generations.GenerationError)):
                legacy.assemble(**args)


if __name__=='__main__': unittest.main()
