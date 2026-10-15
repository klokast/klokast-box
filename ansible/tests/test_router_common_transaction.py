"""Durable handoff from immutable legacy evidence to the common VM owner."""
import copy
from contextlib import nullcontext
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_vm_update_transaction as common
t, REPO = common.t, common.REPO

loader = SourceFileLoader('router_handoff_dispatcher_test', str(REPO/'ansible/roles/router-update-recovery/files/router-update-transaction'))
spec = importlib.util.spec_from_loader(loader.name, loader)
dispatcher = importlib.util.module_from_spec(spec); loader.exec_module(dispatcher)


class RouterTransactions(unittest.TestCase):
    tx = common.Transactions.tx

    def setUp(self):
        common.Transactions.setUp(self)
        renamed = self.base.with_name('klokast-vm-updates'); self.base.rename(renamed)
        self.base = renamed; self.work = self.base/'operations'/self.work.name; t.BASE = self.base
        self.legacy = self.base.parent/'klokast-router-updates'; (self.legacy/'records').mkdir(parents=True)
        self.request.update(kind=t.RETAINED_KIND, role='router', image_profile=t.ROUTER_PROFILE,
                            replacement_seconds=3600, recovery_seconds=300,
                            router_state={'kind':'klokast.router-retained-state.v1', 'box':'boxa',
                                'filesystem_uuid':'12345678-1234-1234-1234-123456789abc',
                                'accounts':{'dnsmasq_uid':100,'dnsmasq_gid':101,'tailscale_gid':102}})
        self.request['old_disks'].pop('/dev/vg0/old-data')
        self.request['new_disks']['/dev/vg0/new-data']['bytes'] = 1024**3
        self.request['retained_disks'] = {'/dev/vg0/new-data':self.request['new_disks']['/dev/vg0/new-data']}
        for side in ('old','new'):
            cfg, _ = t.configuration((self.work/(side+'.cfg')).read_text(), 'bak', self.request[side+'_uuid'])
            cfg.update(name='router', vif=['bridge=br-wan,mac=02:00:00:00:00:01', 'bridge=br-bak,mac=02:00:00:00:00:02'])
            if side == 'old': cfg['disk'] = cfg['disk'][:1]
            self.config(side, cfg)
        old, _ = t.configuration((self.work/'old.cfg').read_text(), 'router', self.request['old_uuid'])
        generation = {'kind':'klokast.router-generation.v1','box':'boxa','role':'router',
            'xen':{'uuid':self.request['old_uuid'],'vif':old['vif']},
            'accounts':self.request['router_state']['accounts'],
            'disk':dict(path='/dev/vg0/old-os', **self.request['old_disks']['/dev/vg0/old-os']),
            'boot':{k:dict(path=path, **item) for k,(path,item) in zip(('kernel','initramfs'),self.request['old_artifacts'].items())}}
        generation['record_sha256']=t.digest(generation)
        t.store(self.legacy/'records'/(generation['record_sha256']+'.json'),generation)
        accepted = {'kind':'klokast.router-assignment.v1','box':'boxa','role':'router','current_sha256':generation['record_sha256']}
        accepted['record_sha256']=t.digest(accepted); t.store(self.legacy/'accepted.json',accepted)
        self.historical = (self.legacy/'accepted.json').read_bytes()
        self.request['router_source']={'assignment_sha256':accepted['record_sha256'],'generation_sha256':generation['record_sha256']}
        (self.xen/'bak.cfg').unlink(); (self.xen/'router.cfg').write_bytes((self.work/'old.cfg').read_bytes())
        self.save()
        p=patch.object(t,'command',return_value=json.dumps({'kind':'klokast.router-common-contract.v1','engine_commit':self.request['engine_commit']}));p.start();self.addCleanup(p.stop)
        p=patch.object(t.socket,'gethostname',return_value='boxa-dom0');p.start();self.addCleanup(p.stop)

    def config(self, side, cfg):
        content='\n'.join(k+' = '+repr(v) for k,v in cfg.items())+'\n'
        (self.work/(side+'.cfg')).write_text(content)
        self.request[side+'_config_sha256']=hashlib.sha256(content.encode()).hexdigest()

    def save(self): t.store(self.work/'request.json',self.request)

    def ready(self):
        t.store(self.work/'router-state-ready.json', {'request_sha256':t.digest(self.request),
            'contract':self.request['router_state'],'operation_id':self.work.name,'ready':True})

    def owner(self):
        with patch.object(t,'operation_lock',return_value=nullcontext()):
            return t.router_owner(self.backend)

    def test_first_migration_binds_history_and_requires_state_finalization(self):
        tx=self.tx();tx.arm();tx.step('stop')
        with self.assertRaises(FileNotFoundError): tx.step('start')
        self.assertEqual(tx.journal['stage'],'stopped')
        self.ready();tx.step('start')
        self.assertEqual(self.owner()['selection'],'new')
        self.assertEqual((self.legacy/'accepted.json').read_bytes(),self.historical)

    def test_every_prestart_interruption_recovers_old_and_retains_common_owner(self):
        for stage in ('armed','stopping','stopped'):
            with self.subTest(stage=stage):
                tx=self.tx();tx.arm()
                if stage=='stopping':
                    self.backend.crash='stop'
                    with self.assertRaises(InterruptedError):tx.step('stop')
                    self.backend.crash=None
                elif stage=='stopped':tx.step('stop')
                self.tx().recover()
                self.assertEqual(self.backend.running,'old')
                self.assertEqual(self.owner()['owner'],'common-vm')
                self.assertEqual(self.owner()['selection'],'old')
                (self.work/'journal.json').unlink();(self.base/'active/router.json').unlink()
                (self.xen/'router.cfg').write_bytes((self.work/'old.cfg').read_bytes())

    def test_lost_start_reply_and_dom0_reboot_never_replay_legacy_assignment(self):
        tx=self.tx();tx.arm();tx.step('stop');self.ready();self.backend.crash='start'
        with self.assertRaises(InterruptedError):tx.step('start')
        self.assertEqual(self.tx().journal['stage'],'starting')
        self.backend.crash=None;self.backend.running=None
        self.tx().recover()
        self.assertEqual(self.owner()['selection'],'new');self.assertTrue(self.owner()['boot_ready'])
        self.assertNotIn(('start','old'),self.backend.calls)
        self.tx().step('start');self.assertEqual(self.backend.running,'new')

    def test_missing_data_disk_after_start_fences_instead_of_using_old(self):
        tx=self.tx();tx.arm();tx.step('stop');self.ready();tx.step('start')
        actual=self.backend.lv
        def missing(path):
            if path.endswith('new-data'):raise t.Refused('data disk disappeared')
            return actual(path)
        with patch.object(self.backend,'lv',side_effect=missing),self.assertRaisesRegex(t.Refused,'disappeared'):
            self.tx().recover()
        self.assertNotIn(('start','old'),self.backend.calls)

    def test_next_replacement_reuses_data_and_keeps_logical_identity_contract(self):
        tx=self.tx();tx.arm();tx.step('stop');self.ready();tx.step('start')
        t.store(self.work/'checks.json',{'request_sha256':t.digest(self.request),'checks':dict.fromkeys(
            ('boot','kernel_modules','identity','retained_state','firewall','forwarding','isolation',
             'dhcp_dns','wan','management','reboot'),True)})
        tx.step('tested');tx.step('accept');tx.step('complete')
        previous=copy.deepcopy(self.request); old=tx.assignment_content('new')
        self.work=self.base/'operations'/('b'*24);self.work.mkdir()
        self.request.update(operation_id=self.work.name,router_source=None,old_uuid=previous['new_uuid'],
            new_uuid='33333333-3333-4333-8333-333333333333',old_disks=previous['new_disks'],
            old_artifacts=previous['new_artifacts'],new_disks={
                '/dev/vg0/next-os':{'uuid':'next-root-identity','bytes':4096},
                '/dev/vg0/new-data':previous['new_disks']['/dev/vg0/new-data']})
        (self.work/'old.cfg').write_bytes(old);self.request['old_config_sha256']=hashlib.sha256(old).hexdigest()
        cfg,_=t.configuration(old.decode(),'router',self.request['old_uuid'])
        cfg.update(uuid=self.request['new_uuid'],disk=['phy:/dev/vg0/next-os,xvda,w','phy:/dev/vg0/new-data,xvdb,w'])
        self.config('new',cfg);self.save();self.backend.running='old'
        tx=self.tx();tx.arm();tx.step('stop');self.ready();tx.step('start')
        self.assertEqual(tx.request['retained_disks'],previous['retained_disks'])
        self.assertEqual(tx.request['router_state'],previous['router_state'])
        self.assertEqual(self.owner()['operation_id'],'b'*24)

    def test_another_guest_cannot_attach_data_during_handoff(self):
        records=self.backend.inventory()+[{'domid':8,'config':{'c_info':{'name':'foreign','uuid':'foreign'},
                 'disks':[{'pdev_path':'/dev/vg0/new-data','vdev':'xvdb','readwrite':1}]}}]
        with patch.object(self.backend,'inventory',return_value=records),self.assertRaisesRegex(t.Refused,'another Xen guest'):
            self.tx().arm()
        self.assertFalse((self.base/'active/router.json').exists())

    def test_legacy_pending_cold_test_or_incomplete_install_prevents_handoff(self):
        for name,value in [('pending.json',{}),('cold-test.json',{}),('installation.json',{'stage':'enrolled'})]:
            t.store(self.legacy/name,value)
            with self.assertRaisesRegex(t.Refused,'finish'):self.tx().arm()
            (self.legacy/name).unlink()
        self.assertEqual(self.backend.calls,[])

    def test_changed_legacy_source_and_old_engine_prevent_handoff(self):
        with patch.object(t,'command',return_value='{}'),self.assertRaisesRegex(t.Refused,'matching legacy handoff guard'):
            self.tx().arm()
        t.store(self.legacy/'accepted.json',{})
        with self.assertRaisesRegex(t.Refused,'legacy evidence'):self.tx().arm()
        self.assertFalse((self.base/'active/router.json').exists())

    def test_router_refuses_shared_profile_and_controller_bridge(self):
        self.request['image_profile']='shared-alpine-v1';self.save()
        with self.assertRaisesRegex(t.Refused,'profile'):self.tx()
        self.request['image_profile']=t.ROUTER_PROFILE
        for side in ('old','new'):
            cfg,_=t.configuration((self.work/(side+'.cfg')).read_text(),'router',self.request[side+'_uuid'])
            cfg['vif'].append('bridge=br-ops,mac=02:00:00:00:00:03');self.config(side,cfg)
        self.save()
        with self.assertRaisesRegex(t.Refused,'independent uplink'):self.tx()

    def test_corrupt_pointer_fails_closed_and_unconverted_router_is_not_fenced(self):
        (self.xen/'auto/router.cfg').symlink_to('../router.cfg')
        t.fence_autostart();self.assertTrue((self.xen/'auto/router.cfg').is_symlink())
        t.store(self.base/'active/router.json',{})
        with self.assertRaisesRegex(t.Refused,'pointer'):self.owner()
        t.fence_autostart();self.assertFalse((self.xen/'auto/router.cfg').exists())

    def test_legacy_dispatcher_delegates_recovery_and_rejects_identity_retirement(self):
        tx=self.tx();tx.arm();tx.step('stop');self.ready();tx.step('start')
        calls=[]
        def run(argv,**kwargs):
            calls.append(argv[1:])
            if argv[1]=='recover':self.tx().recover();value={}
            else:value=self.owner()
            return SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')
        with patch.object(dispatcher,'BASE',self.legacy),patch.object(dispatcher.subprocess,'run',side_effect=run):
            result=dispatcher.common_owner('boot-recover');self.assertTrue(result['boot_ready'])
            self.assertIn(['recover','--operation-id',self.work.name],calls)
            for action in ('retire-completed','cold-run','adopt-baseline','run'):
                with self.assertRaisesRegex(RuntimeError,'legacy mutation and retirement are disabled'):
                    dispatcher.common_owner(action)
            self.assertIsNone(dispatcher.common_owner('accepted-manifest'))
        self.assertNotIn(('start','old'),self.backend.calls)


if __name__=='__main__':unittest.main()
