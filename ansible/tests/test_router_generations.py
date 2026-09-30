"""Protected router records cannot select another role or alias its old disk."""
import ast
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_generations as g
import router_personalize


def generation(origin='template'):
    identity = ('a' if origin == 'legacy' else 'b') * 24
    directory = '/mnt/dom0_data/klokast-router-updates/generations/' + identity
    return g.seal({'kind':'klokast.router-generation.v1','role':'router','box':'boxa','generation_id':identity,
        'origin':origin,'engine_commit':'c'*40,'alpine_branch':'v3.23',
        'disk':{'path':'/dev/vg0/lv_router' if origin=='legacy' else '/dev/vg0/routergen_'+identity,
                'uuid': 'old-uuid' if origin=='legacy' else 'new-uuid','bytes':2147483648},
        'boot': {name:{'path':directory+'/'+name,'sha256':'d'*64,'bytes':1234} for name in ('kernel','initramfs')},
        'xen':{'uuid': ('1' if origin=='legacy' else '2')*8+'-1111-4111-8111-111111111111',
               'memory':512,'vcpus':1,'vif':['bridge=br-wan,mac=00:16:3e:00:00:01']},
        'packages':dict.fromkeys((('linux-virt','tailscale','dhcpcd','dnsmasq','nftables','openssh-keygen')
                                  if origin == 'legacy' else
                                  ('linux-virt','dhcpcd','dnsmasq','nftables','openssh-keygen')),'1-r0'),
        **({} if origin == 'legacy' else {'template_operation':'c'*24,
            'release_sha256':'b'*64,'tailscale':{'version':'1.102.4',
            **dict.fromkeys(('sha256','tailscale_sha256','tailscaled_sha256','openrc_sha256'), 'a'*64)}}),
        'kernel_release':'6.18.53-0-virt','accounts':{'dnsmasq_uid':102,'dnsmasq_gid':103,'tailscale_gid':104},
        'configuration_files':dict.fromkeys((
            'etc/network/interfaces', 'etc/dhcpcd.conf', 'etc/dnsmasq.conf',
            'etc/nftables.nft', 'etc/klokast/app-resources/router-forward.nft',
            'etc/klokast/app-resources/router-forward.d/000-empty.nft') if origin == 'legacy'
            else router_personalize.FILES,'e'*64),'evidence_sha256':'f'*64})


def reseal(value):
    value.pop('record_sha256')
    value.update(g.seal(value))


class GenerationTests(unittest.TestCase):
    def test_tailnet_hostname_binds_box_and_exact_generation(self):
        self.assertEqual(g.tailnet_hostname('boxa', 'a' * 24),
                         'boxa-router-' + 'a' * 24)
        self.assertEqual(len(g.tailnet_hostname('b' * 31, 'a' * 24)), 63)
        for box, identity in (('boxa', 'g' * 24), ('boxa', 'a' * 23),
                              ('b' * 32, 'a' * 24)):
            with self.subTest(box=box, identity=identity), self.assertRaises(g.GenerationError):
                g.tailnet_hostname(box, identity)

    def test_legacy_is_distinct_from_template_provenance(self):
        for origin in ('legacy','template'):
            value=generation(origin)
            self.assertEqual(g.generation(value,'boxa'),value)
        legacy=generation('legacy')
        self.assertNotIn('etc/resolv.conf',legacy['configuration_files'])
        legacy['packages'].pop('linux-virt'); reseal(legacy)
        g.generation(legacy,'boxa')
        legacy['configuration_files']['etc/resolv.conf']='e'*64; reseal(legacy)
        with self.assertRaises(g.GenerationError):
            g.generation(legacy,'boxa')
        template=generation()
        template['packages'].pop('linux-virt'); reseal(template)
        with self.assertRaises(g.GenerationError):
            g.generation(template,'boxa')

    def test_legacy_overlay_selectors_are_all_or_none(self):
        value = generation('legacy')
        overlay = ('etc/klokast/overlay-ipv6.nft',
                   'etc/network/if-up.d/91-klokast-ops-ipv6',
                   'etc/sysctl.d/91-klokast-ops-ipv6.conf',
                   'etc/dnsmasq.d/91-klokast-ops-ipv6.conf')
        value['configuration_files'].update(dict.fromkeys(overlay, 'e'*64))
        value['overlay_source_sha256'] = 'f'*64
        reseal(value)
        g.generation(value, 'boxa')
        value['configuration_files'].pop(overlay[-1])
        reseal(value)
        with self.assertRaisesRegex(g.GenerationError, 'complete signed IPv6 repair'):
            g.generation(value, 'boxa')

    def test_template_requires_exact_release_provenance(self):
        for field, value in (('template_operation', 'not-an-operation'),
                             ('release_sha256', 'not-a-checksum')):
            changed = generation()
            changed[field] = value
            reseal(changed)
            with self.subTest(field=field), self.assertRaises(g.GenerationError):
                g.generation(changed, 'boxa')
        for field in ('template_operation', 'release_sha256'):
            changed = generation()
            del changed[field]
            reseal(changed)
            with self.subTest(field=field), self.assertRaises(g.GenerationError):
                g.generation(changed, 'boxa')

    def test_unknown_disk_boot_alias_role_and_configuration_paths_are_rejected(self):
        for mutate in (lambda v:v.update(role='dmz'), lambda v:v.update(box='boxb'),
                       lambda v:v.update(alpine_branch='edge'),
                       lambda v:v['disk'].update(path='/dev/vg0/lv_ops'),
                       lambda v:v['boot']['kernel'].update(path='/mnt/dom0_data/../foreign/kernel'),
                       lambda v:v['configuration_files'].update({'root/.ssh/authorized_keys':'e'*64}),
                       lambda v:v['packages'].update({'openssh-client-default':'1-r0'}),
                       lambda v:v['accounts'].update(dnsmasq_uid=0)):
            value=generation(); mutate(value); reseal(value)
            with self.assertRaises(g.GenerationError):
                g.generation(value,'boxa')

    def test_xen_render_is_fixed_and_contains_only_declared_disk_and_vifs(self):
        value=generation()
        parsed={node.targets[0].id:ast.literal_eval(node.value) for node in ast.parse(g.configuration(value)).body}
        self.assertEqual(parsed['name'],'router')
        self.assertEqual(parsed['uuid'],value['xen']['uuid'])
        self.assertEqual(parsed['disk'],['phy:'+value['disk']['path']+',xvda,w'])
        self.assertEqual(parsed['vif'],value['xen']['vif'])
        self.assertEqual(parsed['extra'],'console=hvc0 root=/dev/xvda3 rw modules=ext4')

    def test_pair_requires_exact_receipts_and_disjoint_live_resources(self):
        old,new=generation('legacy'),generation()
        req={'box':'boxa','engine_commit':new['engine_commit'],
             'old_sha256':old['record_sha256'],'candidate_sha256':new['record_sha256']}
        g.pair(old,new,req)
        for mutate in (lambda v:v['disk'].update(uuid=old['disk']['uuid']),
                       lambda v:v['xen'].update(uuid=old['xen']['uuid']),
                       lambda v:v.update(engine_commit='9'*40),
                       lambda v:v['xen'].update(vif=['bridge=br-other,mac=00:16:3e:00:00:02'])):
            changed=copy.deepcopy(new); mutate(changed); reseal(changed)
            with self.assertRaises(g.GenerationError):
                g.pair(old,changed,{**req,'candidate_sha256':changed['record_sha256']})
        with self.assertRaises(g.GenerationError):
            g.pair(old,new,{**req,'candidate_sha256':'0'*64})

    def test_pair_keeps_the_same_signed_ipv6_repair(self):
        old,new=generation('legacy'),generation()
        overlay=('etc/klokast/overlay-ipv6.nft',
                 'etc/network/if-up.d/91-klokast-ops-ipv6',
                 'etc/sysctl.d/91-klokast-ops-ipv6.conf',
                 'etc/dnsmasq.d/91-klokast-ops-ipv6.conf')
        for item in (old,new):
            item['configuration_files'].update(dict.fromkeys(overlay,'e'*64))
            item['overlay_source_sha256']='f'*64
            reseal(item)
        request={'box':'boxa','engine_commit':new['engine_commit'],
                 'old_sha256':old['record_sha256'],'candidate_sha256':new['record_sha256']}
        g.pair(old,new,request)
        new['overlay_source_sha256']='0'*64
        reseal(new)
        with self.assertRaisesRegex(g.GenerationError,'disjoint disks'):
            g.pair(old,new,{**request,'candidate_sha256':new['record_sha256']})


if __name__ == '__main__':
    unittest.main()
