"""Both router lifecycles bind exact clone inputs and reject residual authority."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_candidate as candidate
import router_personalize as personalize
import test_router_finalize as finalization


class CandidateTests(unittest.TestCase):
    put = finalization.FinalizationTests.put
    retire = finalization.FinalizationTests.retire

    def setUp(self):
        finalization.FinalizationTests.setUp(self)
        self.put('etc/passwd', (self.root/'etc/passwd').read_text() +
                 'dnsmasq:x:65:65:dnsmasq:/var/lib/misc:/sbin/nologin\n')
        self.put('etc/group', (self.root/'etc/group').read_text() + 'tailscale:x:103:\n')
        (self.root/'lib/modules/6.12.1-virt').mkdir(parents=True)
        self.put('etc/init.d/tailscale', (Path(__file__).resolve().parents[1] /
                 'roles/router-alpine-rootfs/files/tailscale-openrc').read_text())
        for relative in ('usr/local/bin/tailscale', 'usr/local/sbin/tailscaled', 'etc/init.d/tailscale'):
            (self.root / relative).chmod(0o755)
        self.job = {'kind':'klokast.router-candidate-job.v1', 'mode':'replacement',
                    'box':'boxa', 'role':'router', 'operation_id':'b'*24,
                    'engine_commit':self.manifest['engine_commit'],
                    'inputs_sha256':self.manifest['inputs_sha256'], 'kernel_release':'6.12.1-virt',
                    'personalization':self.request,
                    'runtime_packages':{k:v for k,v in self.request['packages'].items() if k != 'openssh'}}

    def command(self, argv, **kwargs):
        if 'apk' in argv:
            return self.retire(argv, **kwargs)
        self.assertIn(argv[2], ('dnsmasq', 'nft'))
        self.assertEqual(argv[:2], ['chroot', str(self.root)])
        return SimpleNamespace(returncode=0)

    def prepare(self):
        with patch.object(personalize, 'environment'), patch.object(personalize.os, 'chown'), \
                patch.object(candidate.subprocess, 'run', side_effect=self.command):
            return candidate.prepare(self.root, self.job)

    def test_replacement_retires_only_first_contact_and_has_no_identity(self):
        value = self.prepare()
        self.assertEqual(value['packages'], self.job['runtime_packages'])
        self.assertTrue(value['identity_absent'])
        self.assertTrue(value['service_syntax'])
        self.assertFalse(value['replacement_authorized'])
        self.assertFalse((self.root/'usr/sbin/sshd').exists())
        self.assertEqual(value['accounts'], {'dnsmasq_uid':65, 'dnsmasq_gid':65, 'tailscale_gid':103})

    def test_initial_recipe_keeps_frozen_bootstrap_packages_without_enrollment(self):
        self.job['mode'] = 'initial-install'
        self.job['first_contact'] = {'key':'ssh-ed25519 YQ==', 'backend_address':'192.0.2.2',
                                     'backend_prefix':24,'backend_source_address':'192.0.2.1'}
        value = self.prepare()
        self.assertEqual(value['packages'], self.request['packages'])
        self.assertTrue((self.root/'usr/sbin/sshd').is_file())
        self.assertFalse((self.root/'root/.ssh/authorized_keys').exists())
        self.assertFalse((self.root/'etc/runlevels/default/sshd').exists())
        self.assertFalse(value['replacement_authorized'])

    def test_replacement_with_first_contact_keeps_frozen_packages_until_enrollment(self):
        self.job['first_contact'] = {'key':'ssh-ed25519 YQ==', 'backend_address':'192.0.2.2',
                                     'backend_prefix':24,'backend_source_address':'192.0.2.1'}
        value = self.prepare()
        self.assertEqual(value['packages'], self.request['packages'])
        self.assertTrue((self.root/'usr/sbin/sshd').is_file())
        self.assertTrue(value['identity_absent'])

    def test_initial_install_requires_one_bounded_first_contact_descriptor(self):
        self.job['mode'] = 'initial-install'
        with self.assertRaisesRegex(ValueError, 'first-contact key and backend address'):
            candidate.validate(self.job)
        self.job['first_contact'] = {'key':'ssh-ed25519 YQ==', 'backend_address':'192.0.2.2',
                                     'backend_prefix':24,'backend_source_address':'192.0.2.1'}
        candidate.validate(self.job)
        for field, value in (('backend_address','192.0.2.0/24'), ('backend_prefix',33),
                             ('key','line one\nline two')):
            invalid = copy.deepcopy(self.job)
            invalid['first_contact'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'first-contact key'):
                candidate.validate(invalid)

    def test_wrong_target_engine_manifest_mode_and_kernel_refuse(self):
        for field, value in (('box','boxb'), ('role','bak'), ('mode','adopt'),
                             ('engine_commit','f'*40), ('inputs_sha256','f'*64),
                             ('kernel_release','../../escape')):
            saved = copy.deepcopy(self.job)
            self.job[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare()
            self.job = saved
            self.assertEqual((self.root/'etc/hostname').read_text(), 'klokast-router-template\n')

    def test_reprepare_does_not_reset_a_personalized_clone(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual((self.root/'etc/hostname').read_text(), 'boxa-router\n')

    def test_extra_config_identity_keys_and_world_changes_fail_verification(self):
        self.prepare()
        for relative in ('etc/dnsmasq.d/unmanaged.conf',
                         'etc/klokast/app-resources/router-forward.d/extra.nft',
                         'var/lib/tailscale/tailscaled.state', 'var/lib/dhcpcd/secret',
                         'root/.ssh/authorized_keys', 'etc/ssh/ssh_host_ed25519_key',
                         'var/lib/tailscale/ssh/ssh_host_ed25519_key'):
            self.put(relative, 'synthetic extra bytes\n')
            with self.subTest(path=relative), self.assertRaises(ValueError):
                candidate.verify(self.root, self.job)
            (self.root/relative).unlink()
        self.put('etc/apk/world', 'tailscale\n')
        with self.assertRaisesRegex(ValueError, 'package world'):
            candidate.verify(self.root, self.job)

    def test_changed_modes_accounts_password_and_modules_fail_verification(self):
        self.prepare()
        path = self.root/'etc/hostname'
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'metadata'):
            candidate.verify(self.root, self.job)
        path.chmod(0o644)
        path = self.root/'etc/shadow'
        saved = path.read_text()
        path.write_text(saved.replace('neo:!:', 'neo::'))
        with self.assertRaisesRegex(ValueError, 'locked'):
            candidate.verify(self.root, self.job)
        path.write_text(saved)
        (self.root/'lib/modules/unexpected').mkdir()
        with self.assertRaisesRegex(ValueError, 'modules'):
            candidate.verify(self.root, self.job)

    def test_changed_upstream_binary_or_service_fails_verification(self):
        self.prepare()
        for relative in ('usr/local/bin/tailscale', 'usr/local/sbin/tailscaled', 'etc/init.d/tailscale'):
            path = self.root / relative
            before = path.read_bytes()
            path.write_bytes(before + b'changed')
            with self.subTest(path=relative), self.assertRaisesRegex(ValueError, 'upstream Tailscale'):
                candidate.verify(self.root, self.job)
            path.write_bytes(before)

    def test_upstream_binary_bound_allows_signed_static_binary_size(self):
        path = self.root / 'usr/local/sbin/tailscaled'
        with path.open('r+b') as stream:
            stream.truncate(17 * 1024 * 1024)
        self.assertEqual(candidate.upstream_binary(self.root, 'usr/local/sbin/tailscaled'), path)
        with path.open('r+b') as stream:
            stream.truncate(65 * 1024 * 1024)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            candidate.upstream_binary(self.root, 'usr/local/sbin/tailscaled')

    def test_no_receipt_after_native_syntax_failure(self):
        self.job['mode'] = 'initial-install'
        self.job['first_contact'] = {'key':'ssh-ed25519 YQ==', 'backend_address':'192.0.2.2',
                                     'backend_prefix':24,'backend_source_address':'192.0.2.1'}
        with patch.object(personalize, 'environment'), patch.object(personalize.os, 'chown'), \
                patch.object(candidate.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ValueError, 'native service syntax'):
                candidate.prepare(self.root, self.job)


if __name__ == '__main__':
    unittest.main()
