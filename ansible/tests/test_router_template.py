"""Router template isolation, package pinning, and failed-qualification gates."""
import copy
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'ansible/lib'))
import router_template_inputs
import router_updates
import xen_build_runtime
from platform_updates import UpdateError
from test_router_updates import inputs, PROFILE, ENGINE


def module(name):
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'),
        str(REPO / 'ansible/roles/router-alpine-rootfs/files' / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    result = importlib.util.module_from_spec(spec)
    loader.exec_module(result)
    return result


class GenericTests(unittest.TestCase):
    def setUp(self):
        self.guest = module('router-template-guest')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = inputs()
        self.put('etc/shadow', 'root::0:0:99999:7:::\n')
        self.put('lib/apk/db/installed', '\n\n'.join('P:' + p['name'] + '\nV:' + p['version']
                                                  for p in self.manifest['packages']))
        for services in self.guest.RUNLEVELS.values():
            for service in services:
                self.put('etc/init.d/' + service, '#!/sbin/openrc-run\n')
        (self.root / 'etc/runlevels/default').mkdir(parents=True)
        (self.root / 'etc/runlevels/default/sshd').symlink_to('/etc/init.d/sshd')
        self.guest.baseline(self.root, self.manifest)

    def put(self, path, data):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(data)

    def test_generic_recipe_is_inactive_and_exactly_pinned(self):
        self.assertEqual(self.guest.verify_generic(self.root, self.manifest),
                         {'identity_absent': True, 'exact_packages': True})
        self.assertEqual(list((self.root / 'etc/runlevels/default').iterdir()), [])
        self.assertTrue((self.root / 'etc/shadow').read_text().startswith('root:!:'))
        self.assertEqual((self.root / 'etc/apk/world').read_text().splitlines(),
                         [name + '=1-r0' for name in self.manifest['world']])

    def test_every_retained_identity_is_forbidden_in_generic_disk(self):
        for name in (*self.guest.ABSENT, 'var/lib/tailscale/tailscaled.state',
                     'var/lib/tailscale/ssh/ssh_host_ed25519_key', 'var/lib/dhcpcd/duid',
                     'var/lib/dhcpcd/secret', 'var/lib/dhcpcd/eth0.lease',
                     'etc/ssh/ssh_host_ed25519_key'):
            with self.subTest(path=name):
                self.put(name, 'synthetic identity')
                with self.assertRaises(RuntimeError):
                    self.guest.verify_generic(self.root, self.manifest)
                (self.root / name).unlink()
                # A service state directory must be empty, including empty subdirs.
                for parent in (self.root / name).parents:
                    if parent == self.root:
                        break
                    if parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()
                    else:
                        break

    def test_topology_and_new_boot_services_fail_absence_gate(self):
        self.put('etc/network/interfaces', 'auto eth0\niface eth0 inet dhcp\n')
        with self.assertRaisesRegex(RuntimeError, 'box-specific'):
            self.guest.verify_generic(self.root, self.manifest)
        self.put('etc/network/interfaces', 'auto lo\niface lo inet loopback\n')
        (self.root / 'etc/runlevels/default/dnsmasq').symlink_to('/etc/init.d/dnsmasq')
        with self.assertRaisesRegex(RuntimeError, 'unexpected boot service'):
            self.guest.verify_generic(self.root, self.manifest)

    def test_missing_extra_changed_and_duplicate_installed_packages_fail(self):
        original = (self.root / 'lib/apk/db/installed').read_text()
        for database in (original.replace('V:1-r0', 'V:2-r0', 1),
                         original + '\n\nP:unexpected\nV:1-r0', '',
                         original + '\n\nP:alpine-base\nV:1-r0'):
            with self.subTest(database=database[-40:]):
                self.put('lib/apk/db/installed', database)
                with self.assertRaises(RuntimeError):
                    self.guest.verify_generic(self.root, self.manifest)

    def test_recipe_cannot_write_through_parent_symlink(self):
        (self.root / 'escape').symlink_to('/tmp')
        with self.assertRaisesRegex(RuntimeError, 'parent is a symlink'):
            self.guest.put(self.root, 'escape/router-template-unsafe', 'test')


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.host = module('router-template-dom0')

    def value(self):
        return {'kind': 'klokast.router-template-request.v1', 'box': 'boxa', 'role': 'router',
                'operation_id': 'a' * 24, 'inputs_sha256': inputs()['inputs_sha256'],
                'capsule': {'sha256': 'b' * 64, 'bytes': 10240},
                'bootstrap': {n: {'sha256': 'c' * 64, 'bytes': 10} for n in ('kernel', 'initramfs')}}

    def test_build_and_both_boots_have_no_production_vif_or_disk(self):
        for mode in ('build', 'test', 'openrc'):
            with self.subTest(mode=mode):
                text = self.host.configuration(Path('/operation'), self.value(), 'router-' + mode,
                    '11111111-1111-4111-8111-111111111111', ['phy:/dev/loop1,xvda,r'], mode)
                self.assertIn('vif = []', text)
                self.assertNotIn('/dev/vg', text)
                self.assertNotIn('/etc/xen/auto', text)
                self.assertNotIn('qdisk', text)
                if mode == 'openrc':
                    self.assertIn('init=/sbin/init', text)
                elif mode == 'test':
                    self.assertIn('init=/usr/local/libexec/router-template-test', text)

    def test_wrong_role_box_operation_and_changed_bytes_refuse(self):
        for field, replacement in (('role', 'dmz'), ('box', 'boxb'), ('operation_id', 'd' * 24),
                                   ('kind', 'klokast.vm-template-build-request.v1'), ('inputs_sha256', '../bad')):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                value = self.value(); value[field] = replacement
                (work / 'request.json').write_text(json.dumps(value))
                with patch.object(self.host, 'safe_file'), self.assertRaises(RuntimeError):
                    self.host.request(work, 'boxa', 'a' * 24)
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / 'request.json').write_text(json.dumps(self.value()))
            (work / 'capsule.tar').write_bytes(b'changed')
            with patch.object(self.host, 'safe_file'), self.assertRaisesRegex(RuntimeError, 'bytes differ'):
                self.host.request(work, 'boxa', 'a' * 24)

    def test_partial_failed_or_wrong_kernel_evidence_is_not_success(self):
        expected = dict.fromkeys(('identity_absent', 'exact_packages'), True)
        good = {'kind': 'klokast.router-template-build.v1', 'success': True,
                'operation_id': 'a' * 24, 'inputs_sha256': self.value()['inputs_sha256'],
                'kernel_release': '6.18.1-virt', 'tests': expected}
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            for field, replacement in (('success', False), ('tests', {'identity_absent': True}),
                                       ('operation_id', 'b' * 24), ('inputs_sha256', 'f' * 64),
                                       ('kernel_release', '../kernel')):
                (work / 'result.slot').write_text(json.dumps({**good, field: replacement}))
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    self.host.result(work, self.value(), good['kind'], expected)

    def test_failed_build_never_creates_candidate_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            with patch.object(self.host, 'SLOTS', {name: 4096 for name in self.host.SLOTS}), \
                    patch.object(self.host, 'domain', return_value=None), \
                    patch.object(self.host, 'run', return_value=SimpleNamespace(stdout='free_memory : 8192\n')), \
                    patch.object(self.host.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                    patch.object(self.host, 'attach_loop', return_value='/dev/loop1'), \
                    patch.object(self.host, 'detach_loop') as detach, \
                    patch.object(self.host, 'boot_guest', side_effect=RuntimeError('test failure')):
                with self.assertRaisesRegex(RuntimeError, 'test failure'):
                    self.host.execute(work, self.value())
                self.assertEqual(detach.call_count, 5)
            self.assertFalse((work / 'candidate.json').exists())
            self.assertTrue((work / 'os.slot').exists())
            self.assertEqual(json.loads((work / 'lifecycle.json').read_text())['stage'], 'detached')

    def test_guest_that_cannot_stop_keeps_every_attachment(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            running = []
            def fail(*a, **kw):
                running.append(True)
                raise RuntimeError('uncertain guest stop')
            with patch.object(self.host, 'SLOTS', {name: 4096 for name in self.host.SLOTS}), \
                    patch.object(self.host, 'domain', side_effect=lambda name: {} if running else None), \
                    patch.object(self.host, 'run', return_value=SimpleNamespace(stdout='free_memory : 8192\n')), \
                    patch.object(self.host.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                    patch.object(self.host, 'attach_loop', return_value='/dev/loop1'), \
                    patch.object(self.host, 'detach_loop') as detach, \
                    patch.object(self.host, 'boot_guest', side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, 'retain all operation disks'):
                    self.host.execute(work, self.value())
                detach.assert_not_called()
            self.assertFalse((work / 'candidate.json').exists())
            self.assertTrue((work / 'os.slot').exists())

    def test_release_requires_every_native_generic_test_and_exact_operation(self):
        candidate = {'kind': 'klokast.router-template-candidate.v1', 'box': 'boxa', 'role': 'router',
                     'operation_id': 'a' * 24, 'inputs_sha256': inputs()['inputs_sha256'],
                     'kernel_release': '6.18.1-virt', 'replacement_authorized': False,
                     'generic_tests': dict.fromkeys(('identity_absent', 'exact_packages', 'kernel_modules', 'openrc'), True),
                     'artifacts': {n: {'sha256': 'd' * 64, 'bytes': 4096} for n in ('os', 'kernel', 'initramfs')}}
        receipt = router_template_inputs.release(candidate, inputs(), PROFILE, ENGINE, 'boxa', 'a' * 24,
                                                approved_engine=ENGINE)
        router_updates.validate_release(receipt, PROFILE, ENGINE)
        with self.assertRaisesRegex(UpdateError, 'controller-approved'):
            router_template_inputs.release(candidate, inputs(), PROFILE, ENGINE, 'boxa', 'a' * 24,
                                           approved_engine='f' * 40)
        for field, value in (('box', 'boxb'), ('role', 'dmz'), ('operation_id', 'e' * 24),
                             ('replacement_authorized', True), ('generic_tests', {})):
            with self.subTest(field=field), self.assertRaises(UpdateError):
                router_template_inputs.release({**candidate, field: value}, inputs(), PROFILE, ENGINE,
                                               'boxa', 'a' * 24, approved_engine=ENGINE)


if __name__ == '__main__':
    unittest.main()
