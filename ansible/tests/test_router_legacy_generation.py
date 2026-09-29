"""A legacy baseline record must not claim uninspected template state."""
import copy
import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_generations as generations
import router_legacy_generation as legacy
import router_records as records
import router_updates as updates
from platform_updates import UpdateError, timestamp
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

    def test_adopted_live_adapter_rejects_drift_and_projects_only_checked_identity(self):
        args=self.fixture()
        source=legacy.assemble(**args)
        assignment=generations.seal({'kind':'klokast.router-assignment.v1','box':'boxa','role':'router',
            'current_sha256':source['record_sha256'],'previous_sha256':None,
            'operation_id':source['generation_id'],'engine_commit':source['engine_commit'],
            'policy_sha256':records.BASELINE_AUTHORITY_SHA256,
            'evidence_sha256':source['evidence_sha256']})
        now=dt.datetime(2026,9,27,8,tzinfo=dt.timezone.utc)
        guest,dom0=args['guest'],args['dom0']
        guest['observed_at']=dom0['observed_at']=timestamp(now)
        dom0['accepted_record_present']=True
        result=updates.legacy_live(box='boxa',assignment=assignment,source=source,
                                   guest=guest,dom0=dom0,now=now)
        self.assertEqual(result['generation'],source['record_sha256'])
        self.assertEqual(result['alpine_branch'],'v3.23')
        self.assertNotIn('ssh_keys',result)
        guest['expected_configuration']['/etc/nftables.nft']='0'*64
        # The new engine can render a new candidate without changing the
        # accepted legacy disk. Its original hash remains the live authority.
        self.assertEqual(updates.legacy_live(box='boxa',assignment=assignment,source=source,
                                             guest=guest,dom0=dom0,now=now)['generation'],
                         source['record_sha256'])
        guest['configuration_files']['/etc/nftables.nft']['sha256']='0'*64
        with self.assertRaises(UpdateError):
            updates.legacy_live(box='boxa',assignment=assignment,source=source,
                                guest=guest,dom0=dom0,now=now)
        guest['configuration_files']['/etc/nftables.nft']['sha256']='a'*64
        guest['packages']['tailscale']='2-r0'
        with self.assertRaises(UpdateError):
            updates.legacy_live(box='boxa',assignment=assignment,source=source,
                                guest=guest,dom0=dom0,now=now)
        guest['packages']['tailscale']='1-r0'
        changed={**assignment,'engine_commit':'0'*40}
        changed.pop('record_sha256')
        changed=generations.seal(changed)
        with self.assertRaises(UpdateError):
            updates.legacy_live(box='boxa',assignment=changed,source=source,
                                guest=guest,dom0=dom0,now=now)


if __name__=='__main__': unittest.main()
