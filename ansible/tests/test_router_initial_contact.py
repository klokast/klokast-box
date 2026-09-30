import copy
import importlib.util
import subprocess
import unittest
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_initial_contact as contact
import test_router_personalize as personalization


class RouterInitialContactTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('jinja2'), 'native controller renderer requires Jinja')
    def test_backend_parser_uses_the_actual_common_interfaces_template(self):
        import jinja2
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
        environment.filters['bool'] = bool
        source = Path(__file__).resolve().parents[1] / 'roles/router/templates/interfaces.j2'
        variables = {'router_wan_interface':'eth0'}
        for number, zone in enumerate(('dmz', 'backend', 'iot', 'usr', 'ops'), start=2):
            variables.update({f'router_{zone}_interface':f'eth{number}',
                              f'router_{zone}_ipv4_address':f'192.0.{number}.2',
                              f'router_{zone}_ipv4_netmask':'255.255.255.0'})
        rendered = environment.from_string(source.read_text()).render(**variables)
        self.assertEqual(contact.backend_interface(rendered), ('192.0.3.2', 24))
        self.assertEqual(contact.backend_interface(contact.first_contact_interfaces('192.0.2.2', 24)),
                         ('192.0.2.2', 24))
        for invalid in (rendered.replace('netmask 255.255.255.0', ''),
                        rendered + rendered,
                        rendered.replace('address 192.0.3.2', 'address 192.0.3.2\n    address 192.0.3.3'),
                        rendered.replace('192.0.3.2', '192.0.3.2/24'),
                        rendered.replace('255.255.255.0', '255.0.255.0'),
                        rendered.replace('eth3', 'eth9')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                contact.backend_interface(invalid)

    def test_public_host_pin_works_with_native_openssh_and_rejects_changed_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / 'synthetic-host'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'fixture', '-f', str(key)],
                           check=True, capture_output=True, timeout=30)
            public = key.with_suffix('.pub').read_text()
            receipt = {'host_key_public':{'ed25519':public},
                       'host_key_public_sha256':{'ed25519':contact.digest_bytes(public.encode())}}
            alias, content = contact.known_hosts(receipt, 'a'*24)
            pinned = Path(temporary) / 'known_hosts'
            pinned.write_text(content)
            native = subprocess.run(['ssh-keygen', '-F', alias, '-f', str(pinned)],
                                    check=True, capture_output=True, text=True, timeout=30)
            self.assertIn(content, native.stdout)
            fingerprint = subprocess.run(['ssh-keygen', '-lf', str(pinned)], check=True,
                                         capture_output=True, text=True, timeout=30)
            self.assertIn('ED25519', fingerprint.stdout)
            changed = copy.deepcopy(receipt)
            changed['host_key_public_sha256']['ed25519'] = '0'*64
            with self.assertRaisesRegex(ValueError, 'recorded checksum'):
                contact.known_hosts(changed, 'a'*24)
            for invalid in (public + public, public.replace('ssh-ed25519', 'ssh-rsa'),
                            public.replace(' fixture', '\rfixture'), 'ssh-ed25519 !!!!\n'):
                changed = {'host_key_public':{'ed25519':invalid},
                           'host_key_public_sha256':{'ed25519':contact.digest_bytes(invalid.encode())}}
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    contact.known_hosts(changed, 'a'*24)
            with self.assertRaisesRegex(ValueError, 'exact operation'):
                contact.known_hosts(receipt, 'host\n*')

    def test_backend_address_is_a_single_ipv4_host(self):
        self.assertEqual(
            contact.first_contact_interfaces('192.0.2.8', 24),
            'auto lo\niface lo inet loopback\n\n'
            'auto eth0\niface eth0 inet dhcp\n\n'
            'auto eth3\niface eth3 inet static\n'
            '    address 192.0.2.8/24\n',
        )

    def test_backend_rejects_invalid_or_unbounded_prefix(self):
        for address, prefix in (('192.0.2.8', 0), ('192.0.2.8', 33),
                                ('192.0.2.0/24', 24), ('not-an-address', 24)):
            with self.subTest(address=address, prefix=prefix):
                with self.assertRaises(ValueError):
                    contact.first_contact_interfaces(address, prefix)

    def test_temporary_firewall_allows_only_the_dom0_backend_source_to_router_ssh(self):
        rules = ('table inet filter {\n    chain input {\n'
                 '        type filter hook input priority 0; policy drop;\n    }\n}\n')
        result = contact.first_contact_firewall(rules, '192.0.2.1', '192.0.2.2', 24)
        self.assertIn('ip saddr 192.0.2.1 ip daddr 192.0.2.2 iifname "eth3" tcp dport 22 accept', result)
        self.assertNotIn('tcp dport 22 accept\n        ct state', result)
        for source, base in (('192.0.3.1', rules), ('192.0.2.1', rules + ' # klokast-first-contact-ssh\n')):
            with self.subTest(source=source), self.assertRaises(ValueError):
                contact.first_contact_firewall(base, source, '192.0.2.2', 24)

    def test_effective_sshd_settings_must_restrict_backend_and_disable_extra_access(self):
        settings = ('port 22\nlistenaddress 192.0.2.2:22\n'
            'passwordauthentication no\nkbdinteractiveauthentication no\n'
            'pubkeyauthentication yes\npermitrootlogin prohibit-password\n'
            'allowtcpforwarding no\nallowagentforwarding no\nx11forwarding no\n'
            'permittunnel no\npermittty no\npermituserenvironment no\n')
        self.assertTrue(contact.verify_effective_sshd_config(settings, '192.0.2.2'))
        for changed in (settings.replace('listenaddress 192.0.2.2:22', 'listenaddress 0.0.0.0:22'),
                        settings.replace('permittty no', 'permittty yes'),
                        settings.replace('allowtcpforwarding no', 'allowtcpforwarding yes'),
                        settings + 'listenaddress 0.0.0.0:22\n'):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'effective OpenSSH settings'):
                contact.verify_effective_sshd_config(changed, '192.0.2.2')

    def test_public_key_parser_rejects_malformed_key_material(self):
        for key in ('ssh-ed25519 YQ==', 'ssh-ed25519 !!!! comment',
                    'ssh-rsa YQ== comment\nssh-ed25519 YQ=='):
            with self.subTest(key=key):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    (root / 'etc/ssh').mkdir(parents=True)
                    with patch.object(contact.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
                        with self.assertRaises(ValueError):
                            contact._validate_public_key(root, key)

    def test_seed_records_exact_access_and_generates_only_guest_host_keys(self):
        fixture = personalization.PersonalizationTests('test_personalization_keeps_packages_and_has_no_identity_or_bootstrap_key')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.request['files']['etc/network/interfaces'] = (
            'auto lo\niface lo inet loopback\n\n'
            'auto eth0\niface eth0 inet dhcp\n\n'
            'auto eth3\niface eth3 inet static\n    address 192.0.2.2/24\n')
        fixture.request['files']['etc/nftables.nft'] = (
            'table inet filter {\n    chain input {\n'
            '        type filter hook input priority 0; policy drop;\n    }\n}\n')
        fixture.put('etc/init.d/sshd', '#!/sbin/openrc-run\n')
        fixture.apply()
        key = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGFwcHJvdmVkLXRlc3Q= test-key'
        job = {'kind':'klokast.router-candidate-job.v1','mode':'initial-install','box':'boxa',
               'role':'router','operation_id':'a'*24,'engine_commit':fixture.manifest['engine_commit'],
               'inputs_sha256':fixture.manifest['inputs_sha256'],'kernel_release':'6.12.1-virt',
               'personalization':fixture.request,
               'runtime_packages':{k:v for k,v in fixture.request['packages'].items() if k != 'openssh'},
               'first_contact':{'key':key,'backend_address':'192.0.2.2','backend_prefix':24,
                                'backend_source_address':'192.0.2.1'}}

        native_calls = []
        def native(argv, **kwargs):
            native_calls.append(argv)
            if argv[0] == 'chroot' and argv[2] == '/usr/sbin/sshd' and '-T' in argv:
                return SimpleNamespace(returncode=0, stdout=(
                    'port 22\nlistenaddress 192.0.2.2:22\n'
                    'passwordauthentication no\nkbdinteractiveauthentication no\n'
                    'pubkeyauthentication yes\npermitrootlogin prohibit-password\n'
                    'allowtcpforwarding no\nallowagentforwarding no\nx11forwarding no\n'
                    'permittunnel no\npermittty no\npermituserenvironment no\n'))
            if argv[0] == 'chroot' and argv[2] == '/usr/bin/ssh-keygen' and '-A' in argv:
                for name in ('rsa','ecdsa','ed25519'):
                    fixture.put('etc/ssh/ssh_host_' + name + '_key', 'synthetic private key\n')
                    fixture.put('etc/ssh/ssh_host_' + name + '_key.pub',
                        {'rsa':'ssh-rsa', 'ecdsa':'ecdsa-sha2-nistp256', 'ed25519':'ssh-ed25519'}[name] + ' YQ== fixture\n')
                    (fixture.root / ('etc/ssh/ssh_host_' + name + '_key')).chmod(0o600)
                    (fixture.root / ('etc/ssh/ssh_host_' + name + '_key.pub')).chmod(0o644)
            return SimpleNamespace(returncode=0)

        with patch.object(contact.personalize, 'environment'), patch.object(contact.subprocess, 'run', side_effect=native):
            result = contact.seed(fixture.root, job=job, key=key, personalization=fixture.request,
                                  backend_address='192.0.2.2', backend_prefix=24)
        self.assertEqual(result['kind'], 'klokast.router-first-contact.v2')
        self.assertEqual(result['authorized_key_sha256'], contact.digest_bytes((key + '\n').encode()))
        self.assertEqual(result['interfaces_sha256'], contact.digest_bytes(
            contact.first_contact_interfaces('192.0.2.2', 24).encode()))
        self.assertEqual(result['firewall_sha256'], contact.digest_bytes(
            (fixture.root / 'etc/nftables.nft').read_bytes()))
        self.assertEqual(result['sshd_config_sha256'], contact.digest_bytes(
            (fixture.root / 'etc/ssh/sshd_config.d/10-klokast-bootstrap.conf').read_bytes()))
        self.assertIn('ListenAddress 192.0.2.2\n',
            (fixture.root / 'etc/ssh/sshd_config.d/10-klokast-bootstrap.conf').read_text())
        self.assertEqual(set(result['host_key_public_sha256']), {'rsa','ecdsa','ed25519'})
        self.assertEqual((fixture.root / 'root/.ssh/authorized_keys').read_text(), key + '\n')
        self.assertTrue((fixture.root / 'etc/runlevels/default/sshd').is_symlink())
        self.assertFalse(list((fixture.root / 'etc/ssh').glob('.router-first-contact-*')))
        self.assertIn(['chroot', str(fixture.root), 'nft', '-c', '-f', '/etc/nftables.nft'], native_calls)

        firewall_path = fixture.root / 'etc/nftables.nft'
        temporary_firewall = firewall_path.read_text()
        firewall_path.write_text(temporary_firewall + '# changed\n')
        with patch.object(contact.personalize, 'environment'):
            with self.assertRaisesRegex(ValueError, 'firewall changed before retirement'):
                contact.retire(fixture.root, manifest=fixture.manifest,
                    personalization=fixture.request,
                    runtime_packages={k:v for k,v in fixture.request['packages'].items() if k != 'openssh'},
                    accounts={'dnsmasq_uid':65,'dnsmasq_gid':65,'tailscale_gid':103},
                    enrolled_state_sha256='a'*64, first_contact=result,
                    backend_address='192.0.2.2', backend_prefix=24,
                    backend_source_address='192.0.2.1')
        self.assertTrue((fixture.root / 'root/.ssh/authorized_keys').exists())
        self.assertTrue((fixture.root / 'etc/runlevels/default/sshd').is_symlink())
        firewall_path.write_text(temporary_firewall)

        state = {'fixture':'enrolled'}
        state_hash = contact.personalize.digest(state)
        with patch.object(contact.personalize, 'environment'), \
                patch.object(contact.router_state, 'enrolled_snapshot', return_value={}), \
                patch.object(contact.router_state, 'evidence', return_value=state), \
                patch.object(contact.router_finalize, 'finalize',
                    side_effect=[ValueError('simulated offline package interruption'),
                                 {'kind':'klokast.router-finalization.v1'}]) as finalize:
            with self.assertRaisesRegex(ValueError, 'simulated offline package interruption'):
                contact.retire(fixture.root, manifest=fixture.manifest,
                    personalization=fixture.request,
                    runtime_packages={k:v for k,v in fixture.request['packages'].items() if k != 'openssh'},
                    accounts={'dnsmasq_uid':65,'dnsmasq_gid':65,'tailscale_gid':103},
                    enrolled_state_sha256=state_hash, first_contact=result,
                    backend_address='192.0.2.2', backend_prefix=24,
                    backend_source_address='192.0.2.1')
            self.assertFalse((fixture.root / 'root/.ssh/authorized_keys').exists())
            self.assertFalse((fixture.root / 'etc/runlevels/default/sshd').exists())
            retired = contact.retire(fixture.root, manifest=fixture.manifest,
                personalization=fixture.request,
                runtime_packages={k:v for k,v in fixture.request['packages'].items() if k != 'openssh'},
                accounts={'dnsmasq_uid':65,'dnsmasq_gid':65,'tailscale_gid':103},
                enrolled_state_sha256=state_hash, first_contact=result,
                backend_address='192.0.2.2', backend_prefix=24,
                backend_source_address='192.0.2.1')
        self.assertEqual(retired, {'kind':'klokast.router-finalization.v1'})
        self.assertEqual(finalize.call_count, 2)
        self.assertFalse((fixture.root / 'root/.ssh').exists())
        self.assertFalse((fixture.root / 'etc/runlevels/default/sshd').exists())
        self.assertFalse((fixture.root / 'etc/ssh/sshd_config.d/10-klokast-bootstrap.conf').exists())
        self.assertEqual((fixture.root / 'etc/network/interfaces').read_text(),
                         fixture.request['files']['etc/network/interfaces'])
        self.assertEqual((fixture.root / 'etc/nftables.nft').read_text(),
                         fixture.request['files']['etc/nftables.nft'])


if __name__ == '__main__':
    unittest.main()
