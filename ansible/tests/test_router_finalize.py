"""Finalization must remove only the qualified bootstrap package closure."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_finalize as f
import router_updates as r
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
                       lambda v:v['runtime_packages'].pop('tailscale'),
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
        return SimpleNamespace(returncode=0)

    def apply(self):
        with patch.object(f.router_personalize, 'environment'), patch.object(f.subprocess, 'run', side_effect=self.retire):
            return f.finalize(self.root,self.manifest)

    def test_native_retirement_records_exact_runtime_versions(self):
        result=self.apply()
        self.assertNotIn('openssh',result['packages'])
        self.assertTrue(all(result['tests'].values()))
        self.assertEqual(result['removed_packages'],['openssh'])

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


if __name__ == '__main__':
    unittest.main()
