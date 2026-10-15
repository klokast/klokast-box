"""Controller independence, exact NIC revisions, and interrupted cutover recovery."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ansible/roles/vm-template-builder/files'),
               str(ROOT / 'ansible/roles/infrastructure-guest/files'), str(ROOT / 'ansible/lib')]
import ops_network
import ops_network_guest as guest
import ops_router_network as router
import ops_vpn_proxy


def value(box='boxa'):
    return {'box': box, 'role': 'ops', 'bridge': 'br-ops', 'address': '192.168.125.10/24',
            'bootstrap_source': '192.168.125.1', 'bootstrap_dns': ['1.1.1.1', '8.8.8.8'],
            'uplink': ops_network.uplink(box)}


class RecipeTests(unittest.TestCase):
    def test_uplink_is_stable_and_unique_and_cannot_target_a_workload_bridge(self):
        self.assertEqual(ops_network.uplink('boxa'), ops_network.uplink('boxa'))
        self.assertNotEqual(ops_network.uplink('boxa')['mac'], ops_network.uplink('boxb')['mac'])
        for change in ({'bridge': 'br-dmz'}, {'interface': 'eth0'}, {'mac': '02:00:00:00:00:01'}):
            v=value();v['uplink'].update(change)
            with self.assertRaises(ValueError):ops_network.validate(v)
        with self.assertRaises(ValueError):ops_network.uplink('boxa;touch /tmp/unsafe')

    def test_two_nics_have_one_default_route_and_independent_dns(self):
        files=ops_network.files(value())
        interfaces=files['etc/network/interfaces'][0]
        self.assertNotIn('gateway', interfaces)
        self.assertIn('iface eth1 inet dhcp', interfaces)
        self.assertIn('eth0.disable_ipv6=1', interfaces)
        self.assertIn('NO_DNS="eth1"',files['etc/udhcpc/udhcpc.conf'][0])
        hook=files['etc/network/if-up.d/10-klokast-dns'][0]
        self.assertIn('resolvconf -f -d eth0.klokast',hook)
        self.assertIn('resolvconf -a eth1.klokast',hook)
        self.assertNotIn('192.168.200.41',hook)
        self.assertNotIn('192.168.125.254',hook)

    def test_firewall_has_no_input_ssh_on_uplink_or_forwarding(self):
        text=ops_network.firewall(value())
        self.assertNotIn('flush ruleset',text)
        self.assertEqual(text.count('policy drop;'),3)
        ssh=[line for line in text.splitlines() if 'tcp dport 22 accept' in line]
        self.assertTrue(all('eth0' in line and '192.168.125.1' in line for line in ssh))
        self.assertIn('udp sport 67 udp dport 68 accept',text)
        self.assertIn('meta l4proto ipv6-icmp accept',text)
        self.assertNotIn('7890',text)
        self.assertNotIn('include',text)

    def test_historical_finalizer_config_remains_readable(self):
        import vm_infrastructure_personalize as personalize
        v=value();v.update(kind='klokast.infrastructure-config.v1',gateway='192.168.125.254',
                          public_key='ssh-ed25519 AAAATEST',agent_uid=1004,agent_gid=1004)
        self.assertEqual(personalize.validate(v),v)
        v.pop('uplink');self.assertEqual(personalize.validate(v),v)


class IdentityTests(unittest.TestCase):
    def test_legacy_public_key_absence_is_preserved_as_identity_evidence(self):
        def command(argv):
            return json.dumps({'role':'active','checked_at':'now'} if '--status' in argv else {'Self':{'ID':'node'}})
        with patch.object(guest,'run',side_effect=command),patch.object(Path,'exists',return_value=False):
            observed=guest.identity()
        self.assertIsNone(observed['files']['/home/smith/.ssh/github-klokast-instance.pub'])
        self.assertEqual(observed['tailscale_id'],'node')

    def test_observation_time_is_not_controller_identity(self):
        authorities=[{'role':'standby','active':False,'checked_at':str(i)} for i in range(2)]
        def command(argv):
            return json.dumps(authorities.pop(0) if '--status' in argv else {'Self':{'ID':'same-node'}})
        with patch.object(guest,'run',side_effect=command),patch.object(Path,'read_bytes',return_value=b'same-key'):
            self.assertEqual(guest.identity(),guest.identity())


class GuestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        def mapped(path):
            path=Path(path)
            if path.is_absolute() and not path.is_relative_to(self.root):return self.root/str(path).lstrip('/')
            return path
        self.map=mapped
        for key,val in {'/etc/network/interfaces':'old-interfaces\n','/etc/nftables.nft':'old-firewall\n',
                        '/etc/network/if-up.d/10-klokast-dns':'old-dns\n',
                        '/etc/profile.d/vpn-egress.sh':'old-proxy\n',
                        '/etc/gitconfig':'[user]\n name = smith\n# BEGIN KLOKAST VPN EGRESS\n[http]\n proxy = old\n# END KLOKAST VPN EGRESS\n',
                        '/proc/sys/kernel/random/boot_id':'boot-a\n'}.items():
            p=mapped(key);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(val)
        self.calls=[]
        for name,replacement in [('BASE',mapped('/var/lib/klokast/network/ops-uplink')),
                                 ('MARKER',mapped('/etc/klokast/ops-uplink.json')),('Path',mapped),
                                 ('identity',lambda:{'tailscale_id':'node-a','authority':{'role':'standby'}}),
                                 ('run',lambda argv,**kw:self.calls.append(argv) or '')]:
            p=patch.object(guest,name,replacement);p.start();self.addCleanup(p.stop)
        p=patch.object(guest.socket,'gethostname',return_value='boxa-ops');p.start();self.addCleanup(p.stop)

    def test_prepare_is_repeatable_and_does_not_start_the_uplink(self):
        first=guest.prepare(value());second=guest.prepare(value())
        self.assertEqual(first,second)
        self.assertFalse(any('ifup' in c[0] for c in self.calls))
        self.assertEqual(self.map('/etc/network/interfaces').read_text(),'old-interfaces\n')
        self.assertNotIn('backups', {k:v for k,v in first.items() if k!='backups'})

    def test_boot_recovery_restores_exact_preimages_without_network_commands(self):
        state=guest.prepare(value());guest.apply_files(value());self.calls.clear()
        self.assertNotIn('proxy = old',self.map('/etc/gitconfig').read_text())
        result=guest.rollback(state,boot=True)
        self.assertEqual(result['stage'],'rolled-back')
        self.assertEqual(self.map('/etc/nftables.nft').read_text(),'old-firewall\n')
        self.assertEqual(self.map('/etc/network/interfaces').read_text(),'old-interfaces\n')
        self.assertEqual(self.map('/etc/profile.d/vpn-egress.sh').read_text(),'old-proxy\n')
        self.assertIn('proxy = old',self.map('/etc/gitconfig').read_text())
        self.assertFalse(self.map('/etc/sysctl.d/92-klokast-ops-uplink.conf').exists())
        self.assertFalse(any(c[0] in ('/sbin/ip','/sbin/ifdown') for c in self.calls))

    def test_timeout_rolls_back_and_accepted_state_never_rolls_back(self):
        state=guest.prepare(value());state['deadline']=0;guest.save(state)
        with patch.object(guest,'rollback') as rollback:
            guest.watchdog();rollback.assert_called_once()
        state['stage']='accepted';guest.save(state)
        with patch.object(guest,'rollback') as rollback:
            guest.watchdog();rollback.assert_not_called()

    def test_live_rollback_stops_dhcp_before_restoring_interfaces(self):
        state=guest.prepare(value());guest.apply_files(value())
        def command(argv, **kwargs):
            if argv == ['/sbin/ifdown', 'eth1']:
                self.assertIn('iface eth1 inet dhcp',self.map('/etc/network/interfaces').read_text())
            return ''
        with patch.object(guest,'run',side_effect=command) as run:
            guest.rollback(state)
        run.assert_any_call(['/sbin/ifdown', 'eth1'],check=False)
        self.assertEqual(self.map('/etc/network/interfaces').read_text(),'old-interfaces\n')

    def test_pending_inputs_cannot_change_and_retry_after_rollback_is_explicit(self):
        state=guest.prepare(value());other=value();other['bootstrap_dns']=['9.9.9.9','8.8.8.8']
        with self.assertRaisesRegex(RuntimeError,'recorded network inputs'):guest.prepare(other)
        guest.rollback(state,boot=True)
        self.assertEqual(guest.prepare(value())['stage'],'prepared')


class RouterCleanupTests(unittest.TestCase):
    def test_shared_interface_sets_keep_all_workloads(self):
        original='iifname { "eth2", "eth3", "eth4", "eth5", "eth6" } accept\n' + \
                 'iifname "eth6" ip saddr 192.168.125.10 tcp dport 443 accept\n' + \
                 'iifname "eth2" ip saddr 192.168.200.11 tcp dport 443 accept\n'
        result=router.transform('etc/nftables.nft',original)
        self.assertNotIn('eth6',result)
        self.assertIn('"eth2", "eth3", "eth4", "eth5"',result)
        self.assertIn('192.168.200.11',result)
        self.assertEqual(router.transform('etc/nftables.nft',result),result)

    def test_remove_only_ops_network_stanza(self):
        text='auto eth5\niface eth5 inet static\n    address 192.168.175.254\n\nauto eth6\niface eth6 inet static\n    address 192.168.125.254\n    netmask 255.255.255.0\n'
        self.assertEqual(router.transform('etc/network/interfaces',text),'auto eth5\niface eth5 inet static\n    address 192.168.175.254\n\n')
        self.assertEqual(router.transform('etc/dnsmasq.conf','interface=eth5\ninterface=eth6\n'), 'interface=eth5\n')
        self.assertEqual(router.transform('etc/dhcpcd.conf','denyinterfaces eth2 eth3 eth6\n'), 'denyinterfaces eth2 eth3 \n')

    def test_unknown_rule_is_not_silently_deleted(self):
        for text in ('iifname "other" ip saddr 192.168.125.10 accept\n','define management = "eth6"\n'):
            with self.assertRaises(RuntimeError):router.transform('etc/nftables.nft',text)


class Dom0RevisionTests(unittest.TestCase):
    def setUp(self):
        import test_ops_replacement as existing
        from test_infrastructure_guest import load
        self.fixture = existing.ReplacementTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.m = load('ops_network_dom0_fixture', ROOT/'ansible/roles/infrastructure-guest/files/ops_network_dom0.py')
        f=self.fixture
        cfg=Path(f.old['work'])/'ops.cfg'
        self.before='name = "ops"\nuuid = "old-uuid"\ndisk = ["phy:/dev/vg0/lv_ops,xvda,w"]\nvif = ["bridge=br-ops,mac=02:00:00:00:00:01"]\n'
        cfg.write_text(self.before);(f.m.XEN/'ops.cfg').write_text(self.before)
        f.old['boot_sha256']=f.m.checksum(cfg);f.m.write(f.m.BASE/'assignment.json',f.old)
        f.live['config']['nics']=[{'bridge':'br-ops','mac':'02:00:00:00:00:01'}]
        for name,new in [('driver',f.m),('BASE',f.m.BASE),('RECORD',f.m.BASE/'network.json'),
                         ('domain',lambda name:f.live),('run',self.command)]:
            q=patch.object(self.m,name,new);q.start();self.addCleanup(q.stop)
        q=patch.object(f.m,'install_boot',lambda work:(f.m.XEN/'ops.cfg').write_text((work/'ops.cfg').read_text()))
        q.start();self.addCleanup(q.stop)
        q=patch.object(Path,'is_dir',return_value=True);q.start();self.addCleanup(q.stop)

    def command(self,argv,**kwargs):
        f=self.fixture;f.events.append(argv[1] if argv[0]=='xl' else argv[0])
        if argv[:2]==['xl','network-attach']:
            n=dict(arg.split('=',1) for arg in argv[3:]);f.live['config']['nics'].append(n)
        if argv[:2]==['xl','network-detach']:
            f.live['config']['nics']=[n for n in f.live['config']['nics'] if n['mac']!=argv[3]]
        from types import SimpleNamespace
        return SimpleNamespace(stdout='',returncode=0)

    def test_attach_commit_and_resume_preserve_disks_and_private_mac(self):
        state=self.m.prepare('boxa',value());state=self.m.attach(state)
        state=self.m.commit(state);self.m.commit(state)
        after=self.fixture.m.read(self.m.BASE/'assignment.json')
        for field in ('root_lv','lv_uuid','uuid','artifacts'):
            self.assertEqual(after[field],self.fixture.old[field])
        self.assertEqual(len(self.fixture.live['config']['nics']),2)
        self.assertEqual(self.fixture.live['config']['nics'][0]['mac'],'02:00:00:00:00:01')
        self.assertEqual(self.fixture.events.count('network-attach'),1)
        self.assertFalse(any(name in self.fixture.events for name in ('destroy','shutdown','lvcreate','dd')))

    def test_lost_commit_reply_reuses_the_same_interface(self):
        state=self.m.attach(self.m.prepare('boxa',value()))
        original=self.m.write;failed=False
        def interrupted(path,item):
            nonlocal failed
            original(path,item)
            if not failed and path.name=='assignment.json':failed=True;raise RuntimeError('lost reply')
        with patch.object(self.m,'write',side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError,'lost reply'):self.m.commit(state)
        self.m.commit(self.m.status())
        self.assertEqual(self.fixture.events.count('network-attach'),1)

    def test_rollback_restores_exact_boot_bytes_after_commit(self):
        state=self.m.commit(self.m.attach(self.m.prepare('boxa',value())))
        self.m.rollback(state)
        self.assertEqual((Path(self.fixture.old['work'])/'ops.cfg').read_text(),self.before)
        self.assertEqual(self.fixture.m.read(self.m.BASE/'assignment.json'),self.fixture.old)
        self.assertEqual(len(self.fixture.live['config']['nics']),1)

    def test_reused_disk_or_incomplete_reboot_refuses_before_attachment(self):
        self.fixture.lvs[self.fixture.old['root_lv']]['lv_uuid']='reused'
        with self.assertRaisesRegex(RuntimeError,'UUID differs'):self.m.prepare('boxa',value())
        self.assertNotIn('network-attach',self.fixture.events)
        self.fixture.lvs[self.fixture.old['root_lv']]['lv_uuid']='old-lv'
        self.fixture.m.write(self.m.BASE/'replacement.json',{'stage':'accepted','reboot_verification':{'status':'required'}})
        with self.assertRaisesRegex(RuntimeError,'reboot verification'):self.m.prepare('boxa',value())


class RouterBootRecoveryTests(unittest.TestCase):
    def test_recovery_follows_only_the_recorded_accepted_pointer(self):
        import ops_router_dom0 as native
        before={'vif':['workload','management']};after={'vif':['workload']}
        state={'box':'boxa','stage':'prepared','boot_before':before,'boot_after':after,
               'accepted_before':{'current':'old'},'accepted_after':{'current':'new'}}
        for pointer,expected in (({'current':'old'},before),({'current':'new'},after)):
            for boot in (before,after):
                with self.subTest(pointer=pointer,boot=boot), \
                     patch.object(native.controller.driver,'config_values',return_value=boot), \
                     patch.object(native.controller.driver,'read',return_value=pointer), \
                     patch.object(native.controller,'text') as write,patch.object(native,'run'):
                    native.recover_boot('boxa',state)
                    write.assert_called_once_with(Path('/etc/xen/router.cfg'),native.controller.render(expected))
        with patch.object(native.controller.driver,'config_values',return_value=before), \
             patch.object(native.controller.driver,'read',return_value={'current':'unrelated'}), \
             patch.object(native.controller,'text') as write:
            with self.assertRaisesRegex(RuntimeError,'accepted pointer differs'):native.recover_boot('boxa',state)
            write.assert_not_called()


class ProxyTests(unittest.TestCase):
    def test_proxy_environment_is_explicit_loopback_and_does_not_inherit_other_proxies(self):
        old={'http_proxy':'http://bad','ALL_PROXY':'http://bad','NO_PROXY':'*','TOKEN':'kept'}
        result=ops_vpn_proxy.proxy_environment(12345,old)
        self.assertEqual(result['https_proxy'],'http://127.0.0.1:12345')
        self.assertEqual(result['TOKEN'],'kept')
        self.assertNotIn('ALL_PROXY',result)
        self.assertNotEqual(result['NO_PROXY'],'*')
        self.assertEqual(old['NO_PROXY'],'*')

    def test_missing_command_does_not_open_a_tunnel(self):
        with patch.object(ops_vpn_proxy,'authorize') as auth,patch.object(ops_vpn_proxy.subprocess,'Popen') as popen:
            with self.assertRaises(ValueError):ops_vpn_proxy.execute('boxa',[])
            auth.assert_not_called();popen.assert_not_called()


if __name__=='__main__':unittest.main()
