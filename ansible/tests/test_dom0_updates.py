import importlib.util
from importlib.machinery import SourceFileLoader
import hashlib
import io
import json
import re
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))
import dom0_updates
import dom0_images
from platform_updates import UpdateError


def module():
    loader = SourceFileLoader('dom0_native_test', str(ROOT / 'ansible/roles/dom0-update/files/dom0_update.py'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    value = importlib.util.module_from_spec(spec); loader.exec_module(value)
    return value


class Dom0Updates(unittest.TestCase):
    def setUp(self):
        self.native = module()

    def bundle(self, path, *, unsafe=None, change=False, qualified=True):
        data = {name: b'public boot bytes' for name in (
            'boot/xen.gz', 'boot/vmlinuz-lts', 'boot/initramfs-lts', 'boot/modloop-lts')}
        if unsafe: data[unsafe] = b'unsafe'
        metadata = {'kind': 'klokast.dom0-bundle.v1', 'branch': 'v3.24',
                    'offline_reconstruction_verified': qualified,
                    'files': {name: {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
                              for name, value in data.items()}}
        data['bundle.json'] = json.dumps(metadata).encode()
        if change: data['boot/xen.gz'] = b'changed bytes!!!!'
        with tarfile.open(path, 'w') as archive:
            for name, value in data.items():
                item = tarfile.TarInfo(name); item.size = len(value)
                archive.addfile(item, io.BytesIO(value))
        return self.native.sha(path)

    def test_upstream_payload_is_verified_before_any_runtime_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'runtime'
            generation = Path(directory) / 'generation'
            root.mkdir()
            records = {}
            for relative in self.native.TAILSCALE_FILES:
                source = generation / 'tailscale' / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b'new upstream bytes')
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'old bytes')
                records['tailscale/' + relative] = {'sha256': self.native.sha(source)}
            bundle = {'files': records}
            bad = generation / 'tailscale/etc/init.d/tailscale'
            bad.write_bytes(b'changed')
            with patch.object(self.native, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'bytes changed'):
                    self.native.install_tailscale(generation, bundle, root)
                run.assert_not_called()
            for relative in self.native.TAILSCALE_FILES:
                self.assertEqual((root / relative).read_bytes(), b'old bytes')
            bad.write_bytes(b'new upstream bytes')
            listing = root / 'etc/apk/protected_paths.d/lbu.list'; listing.parent.mkdir(parents=True)
            listing.write_text('+var/lib/tailscale\n+usr/sbin/tailscaled\n')
            runlevel = Path(directory) / 'runlevel-link'
            with patch.object(self.native, 'run') as run, patch.object(self.native, 'TAILSCALE_RUNLEVEL', runlevel):
                self.native.install_tailscale(generation, bundle, root)
            # Only the small files enter the overlay; the binaries leave it.
            calls = [[str(v) for v in c.args[0]] for c in run.call_args_list]
            self.assertEqual(calls, [['lbu', 'include', str(root / 'etc/init.d/tailscale')],
                                     ['lbu', 'include', str(root / 'usr/lib/modules-load.d/tailscale.conf')],
                                     ['lbu', 'include', '-r', str(root / 'usr/sbin/tailscaled')],
                                     # lbu saves only symlinks below etc/init.d by default.
                                     ['lbu', 'include', str(root / self.native.TAILSCALE_SERVICE)],
                                     ['rc-update', 'add', 'klokast-dom0-tailscale', 'default']])
            for relative, mode in self.native.TAILSCALE_FILES.items():
                target = root / relative
                self.assertEqual(target.read_bytes(), b'new upstream bytes')
                self.assertEqual(target.stat().st_mode & 0o777, mode)
            service = root / self.native.TAILSCALE_SERVICE
            self.assertEqual(service.read_text(), self.native.TAILSCALE_SERVICE_TEXT)
            self.assertEqual(service.stat().st_mode & 0o777, 0o755)

    def test_upstream_daemon_and_installed_hashes_must_match_selected_release(self):
        record = {'version': '1.104.7', 'tailscale_sha256': 'a',
                  'tailscaled_sha256': 'b', 'openrc_sha256': 'c'}
        for hashes, version, passes in ((['a', 'b', 'c'], '1.104.7-test', True),
                                        (['a', 'b', 'c'], '1.102.0', False),
                                        (['a', 'wrong', 'c'], '1.104.7', False)):
            with self.subTest(version=version, hashes=hashes), \
                    patch.object(self.native, 'sha', side_effect=hashes), \
                    patch.object(self.native, 'run', return_value=json.dumps({'Version': version})):
                if passes:
                    self.native.verify_tailscale({'tailscale': record})
                else:
                    with self.assertRaises(RuntimeError):
                        self.native.verify_tailscale({'tailscale': record})

    def test_dom0_profile_requires_upstream_resolver_and_excludes_alpine_tailscale(self):
        import vm_template_inputs
        profile = dom0_images.profile(ROOT)
        self.assertFalse({'tailscale', 'tailscale-openrc'} & set(profile['packages']))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(UpdateError, 'requires the upstream Tailscale resolver'):
                vm_template_inputs.freeze(Path(directory) / 'inputs', profile, 'v3.24', 'a' * 40,
                                          expected_profile=dom0_images.PROFILE)
            profile['packages'].append('tailscale')
            with self.assertRaisesRegex(UpdateError, 'must not be requested from Alpine APK'):
                vm_template_inputs.freeze(Path(directory) / 'inputs', profile, 'v3.24', 'a' * 40,
                                          expected_profile=dom0_images.PROFILE,
                                          component_resolver=lambda directory: {})

    def test_selected_bundle_requires_exact_bytes_and_offline_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.tar'
            checksum = self.bundle(path)
            metadata, size = self.native.inspect_bundle(path, checksum)
            self.assertEqual(metadata['branch'], 'v3.24')
            self.assertEqual(size, 4 * len(b'public boot bytes'))
            for options in ({'change': True}, {'qualified': False}, {'unsafe': '../escape'}, {'unsafe': '/escape'}):
                checksum = self.bundle(path, **options)
                with self.subTest(options=options), self.assertRaises(RuntimeError):
                    self.native.inspect_bundle(path, checksum)
            self.bundle(path)
            with self.assertRaises(RuntimeError): self.native.inspect_bundle(path, '0' * 64)

    def test_bundle_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.tar'
            checksum = self.bundle(path)
            link = Path(directory) / 'link'; link.symlink_to(path)
            with self.assertRaises(RuntimeError): self.native.inspect_bundle(link, checksum)

    def test_no_overlay_restore_after_a_new_boot_or_started_reboot(self):
        for boot, state in (('new', 'selected'), ('old', 'rebooting')):
            with patch.object(self.native, 'boot_id', return_value=boot), self.assertRaisesRegex(RuntimeError, 'forbidden'):
                self.native.abort({'boot_before': 'old', 'state': state})

    def test_no_reboot_without_a_persisted_selection(self):
        with patch.object(self.native, 'boot_id', return_value='old'), \
                patch.object(self.native.subprocess, 'Popen') as start:
            with self.assertRaises(RuntimeError):
                self.native.reboot({'boot_before': 'old', 'state': 'prepared'})
            start.assert_not_called()

    def test_unrelated_post_boot_configuration_is_never_committed(self):
        current = {'tailscale_id': 'node', 'domains': {}}
        with patch.object(self.native, 'identity', return_value='node'), \
                patch.object(self.native, 'domains', return_value={}), \
                patch.object(self.native, 'run', return_value='U etc/shadow') as run:
            with self.assertRaisesRegex(RuntimeError, 'unrelated post-boot'):
                self.native.persist_runtime(current)
            run.assert_called_once_with(['lbu', 'status'])

    def test_upstream_tailscale_profile_data_persists_and_its_logs_and_caches_do_not(self):
        status = ('A var/lib/tailscale/profile-data/62be/favorites\n'
                  'A var/lib/tailscale/profile-data/62be/service-client-prefs/736572\n'
                  'A var/lib/tailscale/tailscaled.log1.txt\nA var/lib/tailscale/tailscaled.log2.txt\n'
                  'A var/lib/tailscale/dns-cache\nA var/lib/tailscale/dns-cache/dns-controlplane.tailscale.com.json\n'
                  'U var/lib/tailscale/tailscaled.state')
        exists = lambda path: str(path).startswith('/var/lib/tailscale/')
        outputs = iter([status, ''])
        answer = lambda argv: next(outputs) if argv == ['lbu', 'status'] else ''
        with patch.object(self.native, 'run', side_effect=answer) as run, \
                patch.object(Path, 'exists', exists), patch.object(Path, 'glob', return_value=[]):
            self.native.commit_boot_outputs({'operation': 'a' * 24})
        self.assertEqual([c.args[0] for c in run.call_args_list[1:-1]],
                         [['lbu', 'exclude', '/' + relative] for relative in self.native.TAILSCALE_REGENERATED])
        with patch.object(self.native, 'run', return_value='A var/lib/tailscale/other'):
            with self.assertRaisesRegex(RuntimeError, 'unrelated post-boot'):
                self.native.commit_boot_outputs({'operation': 'a' * 24})

    def test_reboot_stops_when_the_saved_overlay_cannot_start_tailscale(self):
        needed = [r for r in self.native.TAILSCALE_FILES if r not in self.native.TAILSCALE_BINARIES]
        needed += [self.native.TAILSCALE_SERVICE, 'etc/runlevels/default/klokast-dom0-tailscale',
                   'etc/runlevels/default/tailscale', 'etc/klokast/dom0-update.json']
        def overlay(path, names):
            with tarfile.open(path, 'w:gz') as archive:
                for name in names:
                    item = tarfile.TarInfo(name); archive.addfile(item, io.BytesIO(b''))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'boxa-dom0.apkovl.tar.gz'
            overlay(path, needed); self.native.check_overlay(path)
            overlay(path, [name for name in needed if name != self.native.TAILSCALE_SERVICE])
            with self.assertRaisesRegex(RuntimeError, 'lacks boot files.*klokast-dom0-tailscale'):
                self.native.check_overlay(path)
            overlay(path, needed + ['usr/sbin/tailscaled'])
            with self.assertRaisesRegex(RuntimeError, 'carries the Tailscale binaries'):
                self.native.check_overlay(path)
        source = (ROOT / 'ansible/roles/dom0-update/files/dom0_update.py').read_text()
        select = source[source.index('def select('):source.index('def verify(')]
        self.assertLess(select.index("run(['lbu', 'commit', '-d'])"), select.index('check_overlay('))
        self.assertLess(select.index('check_overlay('), select.index('trial_grub('))

    def test_boot_identity_change_blocks_persistence(self):
        with patch.object(self.native, 'identity', return_value='other'), \
                patch.object(self.native, 'run') as run:
            with self.assertRaises(RuntimeError):
                self.native.persist_runtime({'tailscale_id': 'node', 'domains': {}})
            run.assert_not_called()

    def test_reboot_test_refuses_an_unverified_host(self):
        from types import SimpleNamespace
        accepted = {'tailscale_id': 'node', 'domains': {'air': 'uuid'},
                    'bundle': {'kernel_release': 'test-lts'}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.native, 'BASE', Path(directory)), \
                patch.object(self.native, 'read', return_value=accepted), \
                patch.object(self.native, 'clean'), \
                patch.object(self.native, 'identity', return_value='wrong-node'), \
                patch.object(self.native, 'save') as save:
            with self.assertRaises(RuntimeError):
                self.native.test_reboot(SimpleNamespace(operation='a' * 24))
            save.assert_not_called()

    def test_unstopped_guest_never_causes_a_forced_host_reboot(self):
        record = {'boot_before': 'old', 'state': 'rebooting'}
        with patch.object(self.native, 'boot_id', return_value='old'), \
                patch.object(self.native, 'domains', return_value={'busy': 'uuid'}), \
                patch.object(self.native.time, 'monotonic', side_effect=[0, 301]), \
                patch.object(self.native.subprocess, 'Popen') as start:
            with self.assertRaisesRegex(RuntimeError, 'did not shut down'): self.native.reboot(record)
            start.assert_not_called()

    def test_grub_selects_generation_specific_boot_and_offline_repositories(self):
        value = self.native.grub(Path('/media/disk/klokast-dom0/abc'), Path('/media/disk'), 'rescue')
        self.assertIn('multiboot2 /klokast-dom0/abc/boot/xen.gz', value)
        self.assertIn('modloop=/klokast-dom0/abc/boot/modloop-lts', value)
        self.assertIn('alpine_repo=/media/disk/klokast-dom0/abc/repos/0,/media/disk/klokast-dom0/abc/repos/1', value)
        self.assertTrue(value.endswith('rescue'))

    def test_active_controller_box_is_never_a_reboot_target(self):
        view = {'instance': {'boxes': {'boxa': {}, 'boxb': {}}}, 'controllers': {'active': 'boxa', 'standby': 'boxb'}}
        with patch.object(dom0_updates.platform_source, 'snapshot', return_value=view):
            self.assertEqual(dom0_updates.authority('boxb'), view)
            with self.assertRaises(RuntimeError): dom0_updates.authority('boxa')

    def test_unqualified_host_is_skipped_before_any_prepare(self):
        view = {'instance': {'airunners': []}, 'controllers': {'active': 'boxa'}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dom0_updates, 'STATE', Path(directory)), \
                patch.object(dom0_updates, 'authority', return_value=view), \
                patch.object(dom0_updates, 'step', return_value={'pending': None, 'accepted': None}), \
                patch.object(dom0_updates.infrastructure_images, 'local_action') as build:
            result = dom0_updates.execute('boxb', lock_held=True)
            self.assertEqual(result['state'], 'skipped'); build.assert_not_called()

    def test_another_pending_host_operation_returns_the_exact_resume_id(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dom0_updates, 'STATE', Path(directory)), \
                patch.object(dom0_updates, 'authority', return_value={}), \
                patch.object(dom0_updates, 'step', return_value={'pending': {'operation': 'a' * 24}}):
            with self.assertRaisesRegex(RuntimeError, '--resume ' + 'a' * 24):
                dom0_updates.execute('boxb', image='b' * 24, lock_held=True)

    def test_bundle_reuse_requires_equal_versions_and_host_checksum_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = root / 'dom0-builds' / ('a' * 24); previous.mkdir(parents=True)
            result = root / 'request'; result.mkdir()
            saved = {'state': 'bundle-built', 'box': 'boxa', 'operation_id': 'a' * 24,
                     'bundle': {'sha256': 'b' * 64, 'bytes': 1024}}
            frozen = {'profile_sha256': 'c' * 64, 'branch': 'v3.24',
                      'packages': [{'name': 'test', 'version': '1-r0'}]}
            (previous / 'build-result.json').write_text(json.dumps(saved))
            (previous / 'inputs.json').write_text(json.dumps(frozen))
            with patch.object(dom0_images, 'STATE', root), patch.object(dom0_images, 'command') as verify:
                reused = dom0_images.reuse('boxa', frozen, result, root / 'inventory')
                self.assertEqual(reused['state'], 'bundle-reused')
                self.assertEqual(reused['operation_id'], saved['operation_id'])
                verify.assert_called_once()
                verify.reset_mock()
                changed = dict(frozen, packages=[{'name': 'test', 'version': '2-r0'}])
                self.assertIsNone(dom0_images.reuse('boxa', changed, result, root / 'inventory'))
                verify.assert_not_called()
                upstream_changed = dict(frozen, tailscale={'version': '1.104.7'})
                self.assertIsNone(dom0_images.reuse('boxa', upstream_changed, result, root / 'inventory'))
                verify.assert_not_called()
                verify.side_effect = UpdateError('bundle checksum differs')
                with self.assertRaises(UpdateError): dom0_images.reuse('boxa', frozen, result, root / 'inventory')

    def test_prepare_rearms_recovery_on_the_running_generation_and_reclaims_the_rest(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary)
            running, operation = 'a' * 24, 'c' * 24
            keep = mount / 'klokast-dom0' / running; (keep / 'cache').mkdir(parents=True)
            (mount / 'klokast-dom0' / ('b' * 24)).mkdir(); (mount / 'klokast-dom0' / 'notes').mkdir()
            (mount / 'klokast-dom0-recovery' / ('d' * 24)).mkdir(parents=True)
            (mount / 'klokast-dom0-recovery' / operation).mkdir()
            (mount / 'cache').mkdir(); (mount / 'boot/grub').mkdir(parents=True)
            for name in ('xen.gz', 'vmlinuz-lts', 'initramfs-lts', 'modloop-lts'):
                (mount / 'boot' / name).write_text('legacy')
            legacy = ('menuentry "Recovery: Alpine Linux (Xen, from RAM)" {\n    insmod multiboot2\n'
                      '    multiboot2 /boot/xen.gz dom0_mem=1024M,max:1024M loglvl=all guest_loglvl=all\n'
                      '    module2 /boot/vmlinuz-lts modules=loop init=/bin/sh quiet modloop=/boot/modloop-lts '
                      'apkovl=/dev/nvme0n1p1:klokast-dom0-recovery/' + 'd' * 24 + '/overlay.tar.gz\n'
                      '    module2 /boot/initramfs-lts\n}\n')
            config = mount / 'boot/grub/grub.cfg'
            config.write_text(self.native.grub(keep, mount, legacy))
            policy = mount / 'policy.json'; policy.write_text('{}')
            link = mount / 'apk-cache'; link.symlink_to(keep / 'cache')
            real = Path.resolve
            resolve = lambda path, *a: real(link) if str(path) == '/etc/apk/cache' else real(path, *a)
            accepted = {'generation': str(keep)}
            with patch.object(self.native, 'POLICY', policy), patch.object(Path, 'resolve', resolve):
                self.native.rearm(mount, accepted, operation)
                text = config.read_text()
                self.assertNotRegex(text, r'[ =]/boot/')
                recovery = text[text.index('menuentry "Recovery: '):]
                self.assertIn('/klokast-dom0/' + running + '/boot/modloop-lts', recovery)
                self.assertIn('init=/bin/sh', recovery)
                self.assertIn('apkovl=' + str(mount) + '/klokast-dom0-recovery/' + operation + '/overlay.tar.gz', recovery)
                # The candidate's Recovery entry is then the running generation.
                self.assertEqual(self.native.recovery_entry(text, operation, mount), recovery)
                self.native.reclaim(mount, accepted, operation)
                self.assertEqual(sorted(p.name for p in mount.iterdir()),
                                 ['apk-cache', 'boot', 'klokast-dom0', 'klokast-dom0-recovery', 'policy.json'])
                self.assertEqual(sorted(p.name for p in (mount / 'boot').iterdir()), ['grub'])
                self.assertEqual(sorted(p.name for p in keep.parent.iterdir()), [running, 'notes'])
                self.assertEqual([p.name for p in (mount / 'klokast-dom0-recovery').iterdir()], [operation])
                with self.assertRaisesRegex(RuntimeError, 'explicit recovery'):
                    self.native.reclaim(mount, accepted, 'e' * 24)
                config.write_text(self.native.grub(mount / 'klokast-dom0' / ('b' * 24), mount, ''))
                with self.assertRaisesRegex(RuntimeError, 'does not boot the accepted'):
                    self.native.rearm(mount, accepted, operation)
                link.unlink(); link.symlink_to(mount / 'boot')
                with self.assertRaisesRegex(RuntimeError, 'explicit recovery'):
                    self.native.reclaim(mount, accepted, operation)

    def trial(self, mount):
        old, new, operation = mount / 'klokast-dom0' / ('a' * 24), mount / 'klokast-dom0' / ('b' * 24), 'c' * 24
        before = self.native.grub(old, mount, self.native.recovery_entry(self.native.grub(old, mount, ''), 'd' * 24, mount))
        record = {'operation': operation, 'image': 'b' * 24, 'generation': str(new), 'boot_before': 'old',
                  'trial': True, 'tailscale_id': 'node', 'box': 'boxa'}
        return before, record, self.native.trial_grub(new, mount, before, operation)

    @staticmethod
    def cmdline(config, title):
        entry = config[config.index('menuentry "' + title):]
        line = next(line for line in entry.splitlines() if 'vmlinuz-lts' in line)
        return line.split(None, 2)[2]

    def test_trial_boot_is_one_time_and_the_accepted_generation_stays_default(self):
        mount = Path('/media/disk')
        before, record, config = self.trial(mount)
        entries = re.findall(r'(?m)^menuentry "([^"]+)"', config)
        self.assertEqual(entries, ['Alpine Linux (Xen, from RAM)', 'Trial: Alpine Linux (Xen, from RAM)',
                                   'Recovery: Alpine Linux (Xen, from RAM)'])
        header = config[:config.index('menuentry')]
        self.assertIn('set default=0\n', header)
        self.assertIn('set default=1\n    set next_entry=\n    save_env --file ${prefix}/grubenv next_entry', header)
        accepted, candidate = self.cmdline(config, entries[0]), self.cmdline(config, entries[1])
        self.assertIn('/klokast-dom0/' + 'a' * 24 + '/boot/modloop-lts', accepted)
        self.assertIn('apkovl=/media/disk/klokast-dom0-recovery/' + 'c' * 24 + '/overlay.tar.gz', accepted.split())
        self.assertNotIn('init=', accepted)
        self.assertIn('/klokast-dom0/' + 'b' * 24 + '/boot/modloop-lts', candidate)
        self.assertNotIn('apkovl=', candidate)
        self.assertIn('init=/bin/sh', self.cmdline(config, entries[2]))
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.native, 'POLICY', Path(directory) / 'policy.json'):
            self.native.save(self.native.POLICY, {'generation': str(mount / 'klokast-dom0' / ('a' * 24))})
            self.assertTrue(self.native.fallback(record, accepted))
            self.assertFalse(self.native.fallback(record, candidate))
            self.assertFalse(self.native.fallback(record, self.cmdline(config, entries[2])))
            # The candidate overlay names the new generation.
            self.native.save(self.native.POLICY, {'generation': record['generation']})
            self.assertFalse(self.native.fallback(record, accepted))

    def test_grub_environment_is_one_exact_block(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = Path(directory); (mount / 'boot/grub').mkdir(parents=True)
            self.native.grubenv(mount, [('next_entry', 'trial')])
            block = (mount / 'boot/grub/grubenv').read_bytes()
            self.assertEqual(len(block), 1024)
            self.assertTrue(block.startswith(b'# GRUB Environment Block\n'))
            self.assertTrue(self.native.trial_pending(mount))
            # GRUB save_env leaves an empty value after the trial starts.
            (mount / 'boot/grub/grubenv').write_bytes(block.replace(b'next_entry=trial\n', b'next_entry=\n#####'))
            self.assertFalse(self.native.trial_pending(mount))
            with self.assertRaises(RuntimeError):
                self.native.grubenv(mount, [('next_entry', 'x' * 1024)])
            self.assertEqual(sorted(p.name for p in (mount / 'boot/grub').iterdir()), ['grubenv'])

    def test_reboot_refuses_a_changed_trial_selection_before_guests_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = Path(directory); (mount / 'boot/grub').mkdir(parents=True)
            before, record, config = self.trial(mount)
            recovery = Path(directory) / 'base/operations' / record['operation']; recovery.mkdir(parents=True)
            (recovery / 'before-grub.cfg').write_text(before)
            (mount / 'boot/grub/grub.cfg').write_text(before)
            with patch.object(self.native, 'BASE', Path(directory) / 'base'), \
                    patch.object(self.native, 'media', return_value=mount), \
                    patch.object(self.native, 'boot_id', return_value='old'), \
                    patch.object(self.native, 'run') as run, \
                    patch.object(self.native.subprocess, 'Popen') as start:
                with self.assertRaisesRegex(RuntimeError, 'trial selection'):
                    self.native.reboot(dict(record, state='selected'))
                run.assert_not_called(); start.assert_not_called()
                (mount / 'boot/grub/grub.cfg').write_text(config)
                with patch.object(self.native, 'clean'), patch.object(self.native, 'domains', side_effect=[{}, {}]), \
                        patch.object(self.native, 'save'):
                    self.native.reboot(dict(record, state='selected', domains={}))
                self.assertTrue(self.native.trial_pending(mount))
                start.assert_called_once()

    def test_fallback_boot_rolls_back_without_reapplying_the_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = Path(directory) / 'esp'; (mount / 'boot/grub').mkdir(parents=True)
            before, record, config = self.trial(mount)
            base = Path(directory) / 'base'; recovery = base / 'operations' / record['operation']
            recovery.mkdir(parents=True)
            (recovery / 'before-grub.cfg').write_text(before)
            (recovery / 'before.apkovl.tar.gz').write_bytes(b'accepted overlay')
            (mount / 'boot/grub/grub.cfg').write_text(config)
            (mount / 'boxa-dom0.apkovl.tar.gz').write_bytes(b'candidate overlay')
            self.native.save(base / 'pending.json', record)
            policy = Path(directory) / 'policy.json'
            self.native.save(policy, {'generation': str(mount / 'klokast-dom0' / ('a' * 24))})
            proc = Path(directory) / 'cmdline'; proc.write_text(self.cmdline(config, 'Alpine'))
            real = Path.read_text
            text = lambda path, *a, **k: real(proc) if str(path) == '/proc/cmdline' else real(path, *a, **k)
            with patch.object(self.native, 'BASE', base), patch.object(self.native, 'POLICY', policy), \
                    patch.object(self.native, 'media', return_value=mount), \
                    patch.object(self.native, 'boot_id', return_value='new'), \
                    patch.object(self.native, 'identity', return_value='node'), \
                    patch.object(self.native, 'commit_boot_outputs') as commit, \
                    patch.object(Path, 'read_text', text):
                self.assertEqual(self.native.verify(dict(record, state='rebooting'))['state'], 'fallback')
                with self.assertRaisesRegex(RuntimeError, 'trial boot did not complete'):
                    self.native.accept(dict(record, state='rebooting'))
                self.assertEqual(self.native.abort(dict(record, state='rebooting'))['state'], 'rolled-back')
                commit.assert_called_once()
            self.assertEqual((mount / 'boot/grub/grub.cfg').read_text(), before)
            self.assertEqual((mount / 'boxa-dom0.apkovl.tar.gz').read_bytes(), b'accepted overlay')
            self.assertFalse((base / 'pending.json').exists())
            self.assertEqual(self.native.read(recovery / 'record.json')['state'], 'rolled-back')

    def test_accept_makes_the_trial_generation_the_default(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = Path(directory); (mount / 'boot/grub').mkdir(parents=True)
            before, record, config = self.trial(mount)
            recovery = Path(directory) / 'base/operations' / record['operation']; recovery.mkdir(parents=True)
            (recovery / 'before-grub.cfg').write_text(before)
            (mount / 'boot/grub/grub.cfg').write_text(config)
            with patch.object(self.native, 'BASE', Path(directory) / 'base'), \
                    patch.object(self.native, 'media', return_value=mount), \
                    patch.object(self.native, 'verify', side_effect=lambda value: dict(value, state='boot-verified')):
                self.native.save(self.native.BASE / 'pending.json', record)
                self.assertEqual(self.native.accept(dict(record, state='rebooting'))['state'], 'accepted')
            value = (mount / 'boot/grub/grub.cfg').read_text()
            self.assertTrue(value.startswith('set timeout=5\nset default=0\nmenuentry "Alpine'))
            self.assertIn('/klokast-dom0/' + 'b' * 24 + '/boot/modloop-lts', value[:value.index('Recovery:')])
            self.assertFalse(self.native.trial_pending(mount))

    def test_controller_records_the_rollback_after_a_fallback_boot(self):
        operation = 'c' * 24
        pending = {'operation': operation, 'state': 'rebooting', 'trial': True}
        results = {'status': {'pending': pending}, 'select': pending, 'reboot': {'state': 'started'},
                   'verify': {'state': 'fallback', 'image': 'b' * 24}, 'abort': {'state': 'rolled-back'}}
        actions = []
        def step(box, op, action, directory, *a):
            actions.append(action); return results[action]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dom0_updates, 'STATE', Path(directory)), \
                patch.object(dom0_updates, 'authority', return_value={'instance': {'airunners': []}}), \
                patch.object(dom0_updates, 'step', side_effect=step), \
                patch.object(dom0_updates, 'health') as health:
            with self.assertRaisesRegex(RuntimeError, 'runs its accepted generation again'):
                dom0_updates.execute('boxa', resume=operation, lock_held=True)
        self.assertEqual(actions, ['status', 'select', 'reboot', 'verify', 'abort'])
        health.assert_called_once()

    def test_selection_drops_the_getty_that_shares_the_visible_console(self):
        with tempfile.TemporaryDirectory() as directory:
            inittab = Path(directory) / 'inittab'
            inittab.write_text('tty1::respawn:/sbin/getty 38400 tty1\n'
                               'hvc0::respawn:/sbin/getty -L 0 hvc0 vt100\n'
                               'tty0::respawn:/sbin/getty -L 0 tty0 vt100\n')
            self.native.drop_console_getty(inittab)
            self.assertEqual(inittab.read_text(), 'tty1::respawn:/sbin/getty 38400 tty1\n'
                                                  'hvc0::respawn:/sbin/getty -L 0 hvc0 vt100\n')
            before = inittab.stat().st_mtime_ns
            self.native.drop_console_getty(inittab)
            self.assertEqual(inittab.stat().st_mtime_ns, before)
        source = (ROOT / 'ansible/roles/dom0-update/files/dom0_update.py').read_text()
        select = source[source.index('def select('):source.index('def verify(')]
        self.assertLess(select.index('drop_console_getty()'), select.index("run(['lbu', 'commit', '-d'])"))

    def test_abort_restore_replaces_a_running_binary_by_rename(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / 'before', Path(temporary) / 'tailscaled'
            source.write_bytes(b'alpine'); source.chmod(0o755); target.write_bytes(b'upstream')
            running = target.stat().st_ino
            self.native.restore(source, target)
            self.assertEqual(target.read_bytes(), b'alpine')
            self.assertNotEqual(target.stat().st_ino, running)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            self.assertEqual(sorted(p.name for p in Path(temporary).iterdir()), ['before', 'tailscaled'])

    def test_failed_step_reports_the_remote_cause_and_log(self):
        import platform_update_runtime
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'prepare.log'
            with path.open('w') as log:
                log.write('fatal: [boxa-dom0]: FAILED! =>\n    msg: The command exited with a non-zero return code.\n'
                          '    stderr: |-\n        RuntimeError: EFI storage cannot hold this bundle\n')
                message = platform_update_runtime.failure(
                    ['ansible-playbook', '-i', 'hosts', ROOT / 'ansible/playbooks/74-dom0-update.yml'], 2, log)
            self.assertEqual(message, 'ansible-playbook 74-dom0-update.yml failed with exit status 2: '
                             'EFI storage cannot hold this bundle; full log on the controller: ' + str(path))
            self.assertEqual(platform_update_runtime.failure(['/usr/bin/xl'], 1, None), 'xl failed with exit status 1')


if __name__ == '__main__': unittest.main()
