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
        self.assertIn('resolvconf -d eth0.klokast',hook)
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
