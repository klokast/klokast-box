"""First acceptance is bound to verification before its durable pointer moves."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'lib'))
import router_generations as generations
import router_executor as executor
import router_initial_acceptance as acceptance
import router_personalize as personalize
import router_records as records
from router_transaction import TransactionError
from router_release_fixtures import ENGINE, release


class InitialAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name) / 'data'
        self.base.mkdir(mode=0o700)
        for name in ('records','generations','operations'):
            (self.base / name).mkdir(mode=0o700)
        for target,name,value in ((records,'ROOT_UID',os.geteuid()),
                                  (records,'parents',lambda path:None)):
            handle = patch.object(target,name,value)
            handle.start(); self.addCleanup(handle.stop)
        self.operation = 'a'*24
        self.work = self.base / 'operations' / self.operation
        self.work.mkdir(mode=0o700)
        self.final = self.work / 'finalization'
        self.final.mkdir(mode=0o700)
        self.storage = records.Records('boxa',self.base)
        self.release = release()
        self.disk = {'path':'/dev/vg0/routergen_'+self.operation,
            'uuid':'exact-uuid','bytes':2147483648}
        self.enrollment = {'kind':'klokast.router-initial-enrollment-guest.v1',
            'box':'boxa','operation_id':self.operation,'machine_id':'nExactMachine'}
        self.current = generations.seal({
            'kind':'klokast.router-initial-installation.v1','box':'boxa','role':'router',
            'operation_id':self.operation,'engine_commit':ENGINE,
            'selection_sha256':'1'*64,'release_sha256':self.release['receipt_sha256'],
            'disk':self.disk,'stage':'enrolled','preparation_sha256':'2'*64,
            'enrollment_sha256':generations.digest(self.enrollment),
            'machine_id':'nExactMachine','generation_sha256':None})
        records.write(self.base / 'installation.json',self.current)
        records.write(self.work / 'initial-enrollment-result.json',{'installation':self.current})
        component = self.release['inputs']['tailscale']
        self.prepared = {'first_contact':{'kind':'fixture'},
            'prepared':{'accounts':{'dnsmasq_uid':102,'dnsmasq_gid':103,
            'tailscale_gid':104},'configuration_files':dict.fromkeys(personalize.FILES,'d'*64),
            'tailscale':{key:component[key] for key in ('version','sha256',
                'tailscale_sha256','tailscaled_sha256','openrc_sha256')}}}
        self.source = {'box':'boxa','operation_id':self.operation,
            'engine_commit':ENGINE,'inputs_sha256':self.release['inputs']['inputs_sha256'],
            'template_operation':'c'*24}
        self.boot_request = {'xen':{'uuid':'8c681f14-92dd-484d-84cf-82b7ca8c6a3d',
            'memory':512,'vcpus':1,'vif':['bridge=br-wan,mac=00:16:3e:50:00:01']}}
        directory = '/mnt/dom0_data/klokast-router-updates/generations/' + self.operation
        self.boot_intent = {'boot':{name:{'path':directory+'/'+name,
            'sha256':'3'*64,'bytes':100} for name in ('kernel','initramfs')}}
        self.state = {name:{'sha256':letter*64} for name,letter in (
            ('var/lib/tailscale/tailscaled.state','f'),
            ('var/lib/dhcpcd/duid','a'),('var/lib/dhcpcd/secret','b'),
            ('etc/ssh/ssh_host_rsa_key','c'),('etc/ssh/ssh_host_ecdsa_key','d'),
            ('etc/ssh/ssh_host_ed25519_key','e'))}
        self.offline = {'kind':'klokast.router-initial-finalization-result.v1',
            'operation_id':self.operation,'inputs_sha256':self.source['inputs_sha256'],
            'job_sha256':generations.digest(acceptance.finalization.job_for(
                'boxa',self.operation,self.source,{},self.prepared,self.release,self.enrollment)),
            'success':True,'machine_id':'nExactMachine',
            'finalized':{'packages':self.release['runtime_packages'],
                'tests':self.release['runtime_tests'],'enrolled_state_preserved':True},
            'state':self.state,'state_sha256':personalize.digest(self.state)}
        records.write(self.final / 'result.json',self.offline)
        records.write(self.final / 'complete.json',{
            'kind':'klokast.router-initial-finalization-complete.v1',
            'box':'boxa','operation_id':self.operation,'status':'offline-finalized',
            'disk':self.disk,
            'result_sha256':generations.digest(self.offline)})
        self.expected = acceptance.expected_for('boxa',self.operation,ENGINE,self.current,
            self.prepared,self.release,self.offline)
        self.verified = {'kind':'klokast.router-initial-runtime-verification.v1',
            'box':'boxa','operation_id':self.operation,
            'expected_sha256':self.expected['record_sha256'],
            'release_sha256':self.release['receipt_sha256'],
            'machine_id':'nExactMachine','status':'verified','services':True,
            'packages':True,'identity':True,'management':True,'dom0':True}
        records.write(self.final / 'runtime-expected.json',self.expected)
        records.write(self.final / 'runtime-verification.json',self.verified)
        self.grant = {'kind':'klokast.router-initial-accept-grant.v1','box':'boxa',
            'operation_id':self.operation,'engine_commit':ENGINE,
            'verification_sha256':generations.digest(self.verified),
            'installation_sha256':self.current['record_sha256'],
            'granted_at':1000,'expires_at':1300}
        records.write(self.final / 'accept-grant.json',self.grant)
        self.host = Mock()
        self.actual_autostart = acceptance.install_autostart
        context = lambda storage,operation,engine,**kwargs:(
            self.work,self.storage.installation(),self.source,{},self.prepared,
            self.release,self.boot_request,self.boot_intent,self.enrollment)
        for target,name,value in ((acceptance.native,'Native',Mock(return_value=self.host)),
                (acceptance.finalization,'context',context),
                (acceptance.time,'time',Mock(return_value=1001)),
                (acceptance,'install_autostart',lambda storage,record,host:storage.accepted())):
            handle=patch.object(target,name,value)
            handle.start(); self.addCleanup(handle.stop)

    def test_verified_installation_and_first_pointer_are_exact_and_retryable(self):
        before = executor.provisioning_status(self.storage)
        self.assertIsNone(before['assignment'])
        self.assertEqual(before['installation']['operation_id'], self.operation)
        self.assertEqual(before['installation']['stage'], 'enrolled')
        def live(*args,**kwargs):
            self.assertTrue((self.final / 'accept-intent.json').exists())
            self.assertFalse((self.base / 'accepted.json').exists())
        with patch.object(acceptance.finalization,'verify_final_live',side_effect=live) as proof:
            first = acceptance.execute(self.storage,self.operation,ENGINE)
            self.assertEqual(first['status'],'accepted')
            self.assertEqual(self.storage.installation()['stage'],'verified')
            self.assertEqual(self.storage.accepted()['current_sha256'],first['generation_sha256'])
            self.assertEqual(acceptance.execute(self.storage,self.operation,ENGINE),first)
            self.assertEqual(proof.call_count,1)
            after = executor.provisioning_status(self.storage)
            self.assertEqual(after['installation']['stage'], 'verified')
            self.assertEqual(after['assignment']['current_sha256'], first['generation_sha256'])

    def test_stale_grant_refuses_before_any_acceptance_write(self):
        records.write(self.final / 'accept-grant.json',{**self.grant,'expires_at':1001})
        with self.assertRaisesRegex(TransactionError,'grant is stale'):
            acceptance.execute(self.storage,self.operation,ENGINE)
        self.assertFalse((self.final / 'accept-intent.json').exists())
        self.assertFalse((self.base / 'accepted.json').exists())

    def test_retry_after_verified_stage_finishes_same_generation(self):
        real_accept = self.storage.accept_initial
        attempts = []
        def publish_once(record):
            attempts.append(record['record_sha256'])
            if len(attempts) == 1:
                raise TransactionError('interrupted before accepted pointer')
            return real_accept(record)
        with patch.object(acceptance.finalization,'verify_final_live'), \
                patch.object(self.storage,'accept_initial',side_effect=publish_once) as publish:
            with self.assertRaisesRegex(TransactionError,'interrupted before accepted pointer'):
                acceptance.execute(self.storage,self.operation,ENGINE)
            self.assertEqual(self.storage.installation()['stage'],'verified')
            self.assertFalse((self.base / 'accepted.json').exists())
            finished = acceptance.execute(self.storage,self.operation,ENGINE)
            self.assertEqual(finished['generation_sha256'],
                             self.storage.installation()['generation_sha256'])
            self.assertEqual(publish.call_count,2)
            self.assertEqual(attempts[0],attempts[1])

    def test_boot_recovery_completes_only_a_proven_initial_autostart(self):
        with patch.object(acceptance.finalization,'verify_final_live'):
            acceptance.execute(self.storage,self.operation,ENGINE)
        xen = self.base / 'xen'
        (xen / 'auto').mkdir(parents=True)
        host = Mock()
        host.guest.return_value = None
        host.monotonic.return_value = 100
        with patch.object(executor.native,'Native',return_value=host), \
                patch.object(executor.native,'command') as persist:
            self.assertEqual(executor.boot_assignment(self.storage,recover_initial=True,xen=xen),
                             'accepted-assignment-verified')
            self.assertEqual((xen / 'router.cfg').read_text(),
                             generations.configuration(self.storage.generation(
                                 self.storage.accepted()['current_sha256'])))
            self.assertEqual(os.readlink(xen / 'auto/router.cfg'),'../router.cfg')
            persist.assert_called_once()
            self.assertEqual(executor.boot_assignment(self.storage,xen=xen),
                             'accepted-assignment-verified')
            persist.assert_called_once()
            (xen / 'router.cfg').write_text('name = "changed"\n')
            with self.assertRaisesRegex(TransactionError,'boot definition differs'):
                executor.boot_assignment(self.storage,recover_initial=True,xen=xen)

    def test_autostart_persistence_requires_accepted_live_generation(self):
        with patch.object(acceptance.finalization,'verify_final_live'):
            acceptance.execute(self.storage,self.operation,ENGINE)
        record = self.storage.generation(self.storage.accepted()['current_sha256'])
        xen = self.base / 'xen'
        (xen / 'auto').mkdir(parents=True)
        self.host.guest.return_value = ('accepted',{'domid':7})
        with patch.object(acceptance.native,'command') as persist:
            self.actual_autostart(self.storage,record,self.host,xen=xen)
            self.assertEqual((xen / 'router.cfg').read_text(),generations.configuration(record))
            self.assertEqual(os.readlink(xen / 'auto/router.cfg'),'../router.cfg')
            self.actual_autostart(self.storage,record,self.host,xen=xen)
            self.assertEqual(persist.call_count,2)
            (xen / 'router.cfg').write_text('name = "changed"\n')
            with self.assertRaisesRegex(TransactionError,'definition changed'):
                self.actual_autostart(self.storage,record,self.host,xen=xen)


if __name__ == '__main__':
    unittest.main()
