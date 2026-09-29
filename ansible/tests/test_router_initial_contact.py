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
            if argv[0] == 'chroot' and argv[2] == '/usr/bin/ssh-keygen' and '-A' in argv:
                for name in ('rsa','ecdsa','ed25519'):
                    fixture.put('etc/ssh/ssh_host_' + name + '_key', 'synthetic private key\n')
                    fixture.put('etc/ssh/ssh_host_' + name + '_key.pub', 'synthetic public key\n')
                    (fixture.root / ('etc/ssh/ssh_host_' + name + '_key')).chmod(0o600)
                    (fixture.root / ('etc/ssh/ssh_host_' + name + '_key.pub')).chmod(0o644)
            return SimpleNamespace(returncode=0)

        with patch.object(contact.personalize, 'environment'), patch.object(contact.subprocess, 'run', side_effect=native):
            result = contact.seed(fixture.root, job=job, key=key, personalization=fixture.request,
                                  backend_address='192.0.2.2', backend_prefix=24)
        self.assertEqual(result['kind'], 'klokast.router-first-contact.v1')
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
                patch.object(contact.router_state, 'snapshot', return_value={}), \
                patch.object(contact.router_state, 'evidence', return_value=state), \
                patch.object(contact.router_finalize, 'finalize',
                    return_value={'kind':'klokast.router-finalization.v1'}) as finalize:
            retired = contact.retire(fixture.root, manifest=fixture.manifest,
                personalization=fixture.request,
                runtime_packages={k:v for k,v in fixture.request['packages'].items() if k != 'openssh'},
                accounts={'dnsmasq_uid':65,'dnsmasq_gid':65,'tailscale_gid':103},
                enrolled_state_sha256=state_hash, first_contact=result,
                backend_address='192.0.2.2', backend_prefix=24,
                backend_source_address='192.0.2.1')
        self.assertEqual(retired, {'kind':'klokast.router-finalization.v1'})
        finalize.assert_called_once()
        self.assertFalse((fixture.root / 'root/.ssh').exists())
        self.assertFalse((fixture.root / 'etc/runlevels/default/sshd').exists())
        self.assertFalse((fixture.root / 'etc/ssh/sshd_config.d/10-klokast-bootstrap.conf').exists())
        self.assertEqual((fixture.root / 'etc/network/interfaces').read_text(),
                         fixture.request['files']['etc/network/interfaces'])
        self.assertEqual((fixture.root / 'etc/nftables.nft').read_text(),
                         fixture.request['files']['etc/nftables.nft'])


if __name__ == '__main__':
    unittest.main()
