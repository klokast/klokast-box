"""A new router clone must preserve package provenance and reject existing identity."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_personalize as p
import router_overlay_ipv6 as overlay
import test_router_template as generic


class PersonalizationTests(unittest.TestCase):
    put = generic.GenericTests.put

    def setUp(self):
        generic.GenericTests.setUp(self)
        self.put('etc/passwd', 'root:x:0:0:root:/root:/bin/ash\n'
                 'tailscale:x:103:103:tailscale:/var/lib/tailscale:/sbin/nologin\n'
                 'dhcpcd:x:104:104:dhcpcd:/var/lib/dhcpcd:/sbin/nologin\n')
        self.put('etc/group', 'root:x:0:\nwheel:x:10:root\n')
        self.put('etc/klokast-router-inputs.json', json.dumps(self.manifest))
        self.put('usr/local/libexec/router-template-test', '# synthetic qualification hook\n')
        (self.root / 'etc/ssh').mkdir(exist_ok=True)
        for name in p.SERVICES:
            self.put('etc/init.d/' + name, '#!/sbin/openrc-run\n')
        files = {name: '# synthetic\n' for name in p.FILES}
        files.update({'etc/hostname': 'boxa-router\n',
                      'etc/dnsmasq.conf': 'dhcp-leasefile=/var/lib/misc/dnsmasq.leases\n',
                      'etc/klokast/overlay-ipv6.nft': '# Ops IPv6 downstream is disabled.\n'})
        self.request = {'kind': 'klokast.router-personalization.v1', 'box': 'boxa', 'role': 'router',
                        'inputs_sha256': self.manifest['inputs_sha256'], 'files': files,
                        'packages': {v['name']: v['version'] for v in self.manifest['packages']}}

    def apply(self):
        with patch.object(p, 'environment'), patch.object(p.os, 'chown'):
            return p.personalize(self.root, self.request)

    def enable_signed_overlay(self):
        source = {'kind': 'klokast.router-overlay-ipv6-source.v1', 'box': 'boxa',
                  'peer_box': 'boxb', 'delegated_prefix': '2001:4860:1234:5678::/64',
                  'router_next_hop': overlay.stable_next_hop('boxa'),
                  'freebox_gateway_id_sha256': 'a'*64, 'delegation_slot': 2,
                  'authority_state_sha256': 'b'*64, 'repair_receipt_sha256': 'c'*64,
                  'intent_sha256': 'd'*64}
        source['source_sha256'] = overlay.digest(source)
        selected = {'source':source, 'wan':'eth0', 'ops':'eth5',
                    'ops_ipv4':'192.168.100.10'}
        self.request['overlay_ipv6'] = selected
        self.request['files'].update(overlay.files(source, 'boxa', wan='eth0', ops='eth5',
                                                    ops_ipv4='192.168.100.10'))

    def test_signed_overlay_writes_exact_fragments_and_rejects_tampering(self):
        self.enable_signed_overlay()
        self.request['files']['etc/dnsmasq.d/91-klokast-ops-ipv6.conf'] += 'dhcp-option=6,8.8.8.8\n'
        with self.assertRaisesRegex(ValueError, 'signed ops IPv6 recipe'):
            self.apply()
        self.request['files']['etc/dnsmasq.d/91-klokast-ops-ipv6.conf'] = (
            'enable-ra\ndhcp-range=::,constructor:eth5,ra-only,64,12h\n')
        result = self.apply()
        self.assertEqual(result['files']['etc/klokast/overlay-ipv6.nft'],
                         hashlib.sha256(self.request['files']['etc/klokast/overlay-ipv6.nft'].encode()).hexdigest())
        self.assertEqual((self.root/'etc/network/if-up.d/91-klokast-ops-ipv6').stat().st_mode & 0o777,
                         0o755)

    def test_personalization_keeps_packages_and_has_no_identity_or_bootstrap_key(self):
        before = (self.root / 'lib/apk/db/installed').read_bytes()
        result = self.apply()
        self.assertTrue(result['packages_unchanged'])
        self.assertFalse(result['replacement_authorized'])
        self.assertEqual((self.root / 'lib/apk/db/installed').read_bytes(), before)
        self.assertEqual((self.root / 'etc/hostname').read_text(), 'boxa-router\n')
        self.assertIn('neo:!:0:', (self.root / 'etc/shadow').read_text())
        self.assertEqual({v.name for v in (self.root / 'etc/runlevels/default').iterdir()}, set(p.SERVICES))
        self.assertEqual(list((self.root / 'var/lib/tailscale').iterdir()), [])
        self.assertFalse((self.root / 'root/.ssh').exists())
        self.assertFalse((self.root / 'usr/local/libexec/router-template-test').exists())
        with self.assertRaisesRegex(ValueError, 'generic input'):
            self.apply()

    def test_wrong_role_box_unknown_file_and_unsupported_state_refuse(self):
        original = copy.deepcopy(self.request)
        for mutate in (
                lambda v: v.update(role='dmz'), lambda v: v.update(box='boxb'),
                lambda v: v['files'].update({'root/.ssh/authorized_keys': 'key\n'}),
                lambda v: v['files'].update({'etc/klokast/overlay-ipv6.nft': 'accept\n'}),
                lambda v: v['files'].update({'etc/dnsmasq.conf': 'dhcp-leasefile=/other\n'})):
            self.request = copy.deepcopy(original)
            mutate(self.request)
            with self.assertRaises(ValueError):
                self.apply()
            self.assertEqual((self.root / 'etc/hostname').read_text(), 'klokast-router-template\n')

    def test_changed_package_or_provenance_cannot_be_personalized(self):
        self.request['packages']['dhcpcd'] = 'different-r0'
        with self.assertRaisesRegex(ValueError, 'generic input'):
            self.apply()

    def test_keyed_compiler_include_is_written_and_bound_to_evidence(self):
        import hashlib
        name = 'etc/klokast/app-resources/router-forward.d/probe_app.nft'
        content = '# Synthetic compiler-owned rule.\n'
        self.request['files'][name] = content
        result = self.apply()
        self.assertEqual((self.root / name).read_text(), content)
        self.assertEqual(result['files'][name], hashlib.sha256(content.encode()).hexdigest())

    def test_include_extension_cannot_escape_its_fixed_directory(self):
        original = copy.deepcopy(self.request)
        for path in ('etc/klokast/app-resources/router-forward.d/../escape.nft',
                     'etc/klokast/app-resources/router-forward.d/nested/rule.nft',
                     'etc/dnsmasq.d/injected.conf', 'etc/init.d/injected',
                     '/etc/klokast/app-resources/router-forward.d/absolute.nft'):
            self.request = copy.deepcopy(original)
            self.request['files'][path] = '# unsafe selector\n'
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.apply()
        self.assertEqual((self.root/'etc/hostname').read_text(), 'klokast-router-template\n')

    def test_existing_service_identity_stops_before_hostname_write(self):
        self.put('var/lib/tailscale/tailscaled.state', 'synthetic existing identity')
        with self.assertRaisesRegex(ValueError, 'existing service identity'):
            self.apply()
        self.assertEqual((self.root / 'etc/hostname').read_text(), 'klokast-router-template\n')

    def test_linked_parent_and_hardlinked_config_are_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'etc/klokast').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'parent is unsafe'):
            self.apply()
        self.assertEqual(list(outside.iterdir()), [])

    def test_hardlinked_existing_configuration_is_not_overwritten(self):
        os.link(self.root / 'etc/hostname', self.root / 'hostname-copy')
        with self.assertRaisesRegex(ValueError, 'file is unsafe'):
            self.apply()

    def test_declared_service_owner_does_not_relax_parent_or_write_checks(self):
        path = self.root / 'var/lib/tailscale'
        path.mkdir(parents=True, exist_ok=True)
        original = Path.lstat

        def owned(item, *args, **kwargs):
            info = original(item, *args, **kwargs)
            if item == path:
                values = list(info)
                values[4:6] = [103, 103]
                return os.stat_result(values)
            return info

        with patch.object(Path, 'lstat', owned):
            self.assertEqual(p.service_directory(self.root, 'var/lib/tailscale'), path)
            with self.assertRaisesRegex(ValueError, 'parent is unsafe'):
                p.directory(self.root, 'var/lib/tailscale')
            path.chmod(0o770)
            with self.assertRaisesRegex(ValueError, 'parent is unsafe'):
                p.service_directory(self.root, 'var/lib/tailscale')


if __name__ == '__main__':
    unittest.main()
