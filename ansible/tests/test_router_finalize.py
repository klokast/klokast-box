"""Finalization must remove only the qualified bootstrap package closure."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_finalize as f
import router_updates as r
import router_state
from platform_updates import UpdateError
from test_router_updates import inputs, release, PROFILE, ENGINE, reseal
import test_router_personalize as personalization


class PackageTests(unittest.TestCase):
    def test_finalization_refuses_added_upgraded_or_removed_runtime_packages(self):
        before = {p['name']: p['version'] for p in inputs()['packages']}
        after = {k: v for k, v in before.items() if k != 'openssh'}
        f.validate_packages(before, after, inputs()['world'])
        for mutate in (lambda p:p.update(tailscale='2-r0'), lambda p:p.pop('dhcpcd'),
                       lambda p:p.update(unexpected='1-r0'), lambda p:p.update(openssh='1-r0')):
            candidate = copy.deepcopy(after)
            mutate(candidate)
            with self.assertRaises(ValueError):
                f.validate_packages(before, candidate, inputs()['world'])

    def test_unknown_dependency_removal_requires_a_profile_change(self):
        before = {'openssh':'1-r0','linux-virt':'1-r0','dependency':'1-r0'}
        with self.assertRaises(ValueError):
            f.validate_packages(before, {'linux-virt':'1-r0'}, ['openssh','linux-virt'])

    def test_release_cannot_hide_missing_runtime_tests_or_retain_server(self):
        for mutate in (lambda v:v['runtime_tests'].update(no_openssh_server=False),
                       lambda v:v['runtime_packages'].update(openssh='1-r0'),
                       lambda v:v['runtime_packages'].pop('dhcpcd'),
                       lambda v:v.update(kind='klokast.router-release.v1')):
            value=release()
            mutate(value); reseal(value)
            with self.assertRaises(UpdateError):
                r.validate_release(value, PROFILE, ENGINE)


class FinalizationTests(unittest.TestCase):
    put = personalization.PersonalizationTests.put

    def setUp(self):
        personalization.PersonalizationTests.setUp(self)
        self.put('etc/apk/world', '\n'.join(n+'='+self.request['packages'][n]
                                          for n in self.manifest['world'])+'\n')

    def retire(self, argv, **kwargs):
        self.assertEqual(argv, ['chroot', str(self.root), 'apk', '--no-network', '--no-cache', 'del', 'openssh'])
        p=self.root/'lib/apk/db/installed'
        p.write_text('\n\n'.join(block for block in p.read_text().split('\n\n')
                                 if 'P:openssh\n' not in block)+'\n')
        p=self.root/'etc/apk/world'
        p.write_text('\n'.join(s for s in p.read_text().splitlines() if not s.startswith('openssh='))+'\n')
        (self.root/'usr/sbin/sshd').unlink()
        if not any((self.root/'etc/ssh').iterdir()):
            (self.root/'etc/ssh').rmdir()  # APK removes an empty package directory.
        return SimpleNamespace(returncode=0)

    def apply(self):
        with patch.object(f.router_personalize, 'environment'), patch.object(f.subprocess, 'run', side_effect=self.retire):
            return f.finalize(self.root,self.manifest)

    def test_native_retirement_records_exact_runtime_versions(self):
        result=self.apply()
        self.assertNotIn('openssh',result['packages'])
        self.assertTrue(all(result['tests'].values()))
        self.assertEqual(result['removed_packages'],['openssh'])
        self.assertTrue((self.root/'etc/ssh').is_dir())
        fallback = self.root/'var/lib/tailscale/ssh'
        self.assertEqual(fallback.stat().st_mode & 0o777, 0o700)
        self.assertEqual(list(fallback.iterdir()), [])

    def test_existing_identity_or_changed_world_stops_before_apk(self):
        for relative in ('root/.ssh/authorized_keys','var/lib/tailscale/tailscaled.state'):
            self.put(relative,'synthetic identity\n')
            with patch.object(f.router_personalize,'environment'), patch.object(f.subprocess,'run') as command:
                with self.assertRaisesRegex(ValueError,'existing machine'):
                    f.finalize(self.root,self.manifest)
                command.assert_not_called()
            (self.root/relative).unlink()
        self.put('etc/apk/world','openssh\n')
        with patch.object(f.router_personalize,'environment'), patch.object(f.subprocess,'run') as command:
            with self.assertRaisesRegex(ValueError,'frozen generic world'):
                f.finalize(self.root,self.manifest)
            command.assert_not_called()

    def seed_enrolled(self):
        for relative in (*router_state.REQUIRED, *router_state.OPTIONAL,
                         *('etc/ssh/ssh_host_' + kind + '_key' for kind in router_state.KEY_TYPES)):
            self.put(relative, 'synthetic state\n')
            (self.root/relative).chmod(0o600)
        original = os.fstat
        def ownership(fd):
            value = original(fd)
            return SimpleNamespace(**{name:getattr(value, name) for name in (
                'st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns','st_atime_ns')}, st_uid=0, st_gid=0)
        owner = patch.object(router_state.os, 'fstat', side_effect=ownership)
        owner.start()
        self.addCleanup(owner.stop)
        return {'enrolled_accounts': {'dnsmasq_uid':65,'dnsmasq_gid':65,'tailscale_gid':103},
                'runtime_packages': {k:v for k,v in self.request['packages'].items() if k != 'openssh'},
                'enrolled_state_sha256': f.router_personalize.digest(router_state.evidence(router_state.snapshot(self.root)))}

    def test_enrolled_cleanup_preserves_identity_and_resumes_without_apk(self):
        arguments = self.seed_enrolled()
        expected = router_state.evidence(router_state.snapshot(self.root, **arguments['enrolled_accounts']))
        with patch.object(f.router_personalize, 'environment'), patch.object(f.subprocess, 'run', side_effect=self.retire) as command:
            result = f.finalize(self.root, self.manifest, **arguments)
            self.assertEqual(result['packages'], arguments['runtime_packages'])
            self.assertTrue(result['enrolled_state_preserved'])
            self.assertEqual(result, f.finalize(self.root, self.manifest, **arguments))
            command.assert_called_once()
        self.assertEqual(router_state.evidence(router_state.snapshot(self.root, **arguments['enrolled_accounts'])), expected)
        self.assertNotIn('synthetic state', str(result))

    def test_enrolled_cleanup_refuses_remaining_key_or_missing_state_before_apk(self):
        arguments = self.seed_enrolled()
        self.put('root/.ssh/authorized_keys', 'synthetic first-contact key\n')
        with patch.object(f.router_personalize, 'environment'), patch.object(f.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'first-contact key'):
                f.finalize(self.root, self.manifest, **arguments)
            (self.root/'root/.ssh/authorized_keys').unlink()
            (self.root/'var/lib/dhcpcd/secret').unlink()
            with self.assertRaises(router_state.StateError):
                f.finalize(self.root, self.manifest, **arguments)
            command.assert_not_called()

    def test_enrolled_cleanup_detects_changed_state(self):
        arguments = self.seed_enrolled()
        def mutate(argv, **kwargs):
            result = self.retire(argv, **kwargs)
            (self.root/'var/lib/dhcpcd/duid').write_text('changed identity\n')
            return result
        with patch.object(f.router_personalize, 'environment'), patch.object(f.subprocess, 'run', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'changed enrolled identity'):
                f.finalize(self.root, self.manifest, **arguments)
        with patch.object(f.router_personalize, 'environment'), patch.object(f.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'recorded bootstrap state'):
                f.finalize(self.root, self.manifest, **arguments)
            command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
