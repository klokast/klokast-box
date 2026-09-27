"""A new router clone must preserve package provenance and reject existing identity."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_personalize as p
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
        self.request['packages']['tailscale'] = 'different-r0'
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
