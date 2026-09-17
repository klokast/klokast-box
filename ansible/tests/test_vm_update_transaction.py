#!/usr/bin/env python3
"""Execute the recovery state machine against a strict simulated Xen backend."""
import copy
from contextlib import nullcontext
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import signal
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
loader = SourceFileLoader('vm_update_transaction', str(REPO / 'ansible/roles/vm-update-recovery/files/vm-update-transaction'))
spec = importlib.util.spec_from_loader(loader.name, loader)
t = importlib.util.module_from_spec(spec)
loader.exec_module(t)


class Xen:
    def __init__(self, request):
        self.request = request
        self.running = 'old'
        self.calls = []
        self.crash = None
        self.bad_uuid = False
        self.alive = True

    def inventory(self):
        records = [{'domid': 0, 'config': {'c_info': {'name': 'Domain-0'}}}]
        if self.running:
            records.append({'domid': 7, 'config': {'c_info': {'name': 'bak',
                'uuid': 'unexpected' if self.bad_uuid else self.request[self.running + '_uuid']},
                'disks': [{'pdev_path': p, 'vdev': 'xvd' + letter, 'readwrite': 1}
                          for p, letter in zip(self.request[self.running + '_disks'], 'ab')]}})
        return records

    def device(self, path):
        return {'/dev/vg0/old-os': 1, '/dev/vg0/old-data': 2,
                '/dev/vg0/new-os': 3, '/dev/vg0/new-data': 4}[path]

    def lv(self, path):
        side = 'old' if 'old-' in path else 'new'
        return {**self.request[side + '_disks'][path], 'device': self.device(path)}

    def check_unmounted(self, disks):
        pass

    def artifact(self, path):
        return {'sha256': 'd' * 64, 'bytes': 4096}

    def stop(self, record, graceful):
        self.calls.append(('stop', self.running, graceful))
        self.running = None
        if self.crash == 'stop':
            raise InterruptedError('crash after Xen shutdown')

    def start(self, path):
        self.running = 'new' if 'new.cfg' in str(path) else 'old'
        self.calls.append(('start', self.running))
        if self.crash == 'start':
            raise InterruptedError('crash after Xen create')

    def persist(self):
        self.calls.append(('persist',))
        if self.crash == 'persist':
            raise InterruptedError('crash before lbu completion')

    def watch(self, operation, work):
        self.calls.append(('watch', operation))
        return {'pid': 1234, 'start_ticks': '9', 'boot_id': 'test-boot'}

    def watcher_alive(self, identity, operation):
        return identity['pid'] == 1234 and operation == self.request['operation_id'] and self.alive


class Transactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.base, self.xen = root / 'state', root / 'xen'
        self.work = self.base / 'operations' / ('a' * 24)
        self.work.mkdir(parents=True)
        (self.base / 'active').mkdir()
        (self.xen / 'auto').mkdir(parents=True)
        for name, value in [('BASE', self.base), ('XEN', self.xen)]:
            p = patch.object(t, name, value); p.start(); self.addCleanup(p.stop)
        # Tests run as the developer account. Production still requires root
        # ownership through secure() and require_dom0().
        p = patch.object(t, 'secure', side_effect=lambda path, directory=False: path)
        p.start(); self.addCleanup(p.stop)
        self.request = {'kind': 'klokast.vm-switch.v1', 'operation_id': self.work.name,
            'box': 'boxa', 'role': 'bak', 'engine_commit': 'a' * 40, 'policy_sha256': 'b' * 64,
            'release_sha256': 'c' * 64, 'autostart': True,
            'old_uuid': '11111111-1111-4111-8111-111111111111',
            'new_uuid': '22222222-2222-4222-8222-222222222222'}
        for side in ('old', 'new'):
            paths = ['/dev/vg0/' + side + '-os', '/dev/vg0/' + side + '-data']
            self.request[side + '_disks'] = {p: {'uuid': side + '-' + str(i) + '-identity', 'bytes': 4096} for i, p in enumerate(paths)}
            self.request[side + '_artifacts'] = {'/mnt/dom0_data/' + side + '-' + name:
                {'sha256': 'd' * 64, 'bytes': 4096} for name in ('kernel', 'initramfs')}
            config = ('name = "bak"\ntype = "pvh"\nuuid = ' + repr(self.request[side + '_uuid']) +
                      '\nkernel = "/mnt/dom0_data/' + side + '-kernel"\nramdisk = "/mnt/dom0_data/' + side + '-initramfs"\n' +
                      'disk = ' + repr(['phy:' + paths[0] + ',xvda,w', 'phy:' + paths[1] + ',xvdb,w']) + '\n')
            (self.work / (side + '.cfg')).write_text(config)
            self.request[side + '_config_sha256'] = hashlib.sha256(config.encode()).hexdigest()
        (self.xen / 'bak.cfg').write_bytes((self.work / 'old.cfg').read_bytes())
        (self.work / 'request.json').write_text(json.dumps(self.request))
        self.backend = Xen(self.request)
        self.now = 10000

    def tx(self):
        return t.Transaction(self.work, self.backend, lambda: self.now)

    def checks(self):
        t.store(self.work / 'checks.json', {'request_sha256': t.digest(self.request),
            'checks': {k: True for k in ('boot', 'kernel_modules', 'tailscale', 'rootless_podman',
                'firewall', 'application_compatibility', 'data', 'application_images_unchanged', 'quarantine')}})

    def boot(self):
        tx = self.tx(); tx.arm(); tx.step('stop'); tx.step('start'); return tx

    def test_full_switch_acceptance_and_no_data_rollback(self):
        tx = self.boot(); self.checks(); tx.step('tested'); tx.step('accept'); tx.step('complete')
        self.assertEqual(self.tx().journal['stage'], 'complete')
        self.assertEqual(self.tx().recover(), 'preserve-production-data')
        self.assertEqual(self.backend.running, 'new')
        self.assertNotIn(('start', 'old'), self.backend.calls)
        self.assertIn('new-os', (self.xen / 'bak.cfg').read_text())
        self.assertIn('name = "bak"', (self.xen / 'bak.cfg').read_text())

    def test_recovery_at_every_preaccept_stage(self):
        for stage in ('armed', 'stopping', 'stopped', 'starting', 'booted', 'tested'):
            with self.subTest(stage=stage):
                (self.work / 'journal.json').unlink(missing_ok=True)
                (self.base / 'active/bak.json').unlink(missing_ok=True)
                (self.xen / 'bak.cfg').write_bytes((self.work / 'old.cfg').read_bytes())
                self.backend.running = 'old'
                tx = self.tx(); tx.arm()
                tx.record(stage)
                self.backend.running = ('old' if stage == 'armed' else None if stage in {'stopping', 'stopped'} else 'new')
                self.assertEqual(self.tx().recover(), 'recovered')
                self.assertEqual(self.backend.running, 'old')
                self.assertIn('old-os', (self.xen / 'bak.cfg').read_text())

    def test_crash_after_shutdown_and_after_create_recovers_from_disk(self):
        for failure in ('stop', 'start'):
            with self.subTest(failure=failure):
                (self.work / 'journal.json').unlink(missing_ok=True)
                (self.base / 'active/bak.json').unlink(missing_ok=True)
                (self.xen / 'bak.cfg').write_bytes((self.work / 'old.cfg').read_bytes())
                self.backend.running = 'old'; self.backend.crash = None
                tx = self.tx(); tx.arm()
                if failure == 'start': tx.step('stop')
                self.backend.crash = failure
                with self.assertRaises(InterruptedError): tx.step(failure)
                self.backend.crash = None
                self.assertEqual(self.tx().recover(), 'recovered')
                self.assertEqual(self.backend.running, 'old')

    def test_crash_after_acceptance_does_not_restore_old_disks(self):
        tx = self.boot(); self.checks(); tx.step('tested')
        self.backend.crash = 'persist'
        with self.assertRaises(InterruptedError): tx.step('accept')
        self.assertEqual(self.tx().journal['stage'], 'accepted')
        self.backend.crash = None
        self.assertEqual(self.tx().recover(), 'preserve-production-data')

    def test_accepted_boot_checks_new_disks_without_requiring_retired_old_disks(self):
        tx = self.boot(); self.checks(); tx.step('tested'); tx.step('accept'); tx.step('complete')
        original = self.backend.lv
        def retired(path):
            if 'old-' in path: raise FileNotFoundError('retired old disk')
            return original(path)
        with patch.object(self.backend, 'lv', side_effect=retired):
            self.assertEqual(self.tx().recover(), 'preserve-production-data')
        with patch.object(self.backend, 'lv', side_effect=lambda p: {**original(p), 'uuid': 'replacement-id'}):
            with self.assertRaisesRegex(t.Refused, 'replaced or resized'): self.tx().recover()
        self.assertEqual(self.tx().journal['stage'], 'complete')

    def test_later_boot_validates_recovered_assignment_after_original_budget(self):
        tx = self.boot(); tx.recover()
        # A completed recovery's old deadline must not disable this guest on
        # every subsequent boot. Only an unfinished recovery uses that budget.
        with patch.object(t, 'Native', return_value=self.backend), \
                patch.object(t.socket, 'gethostname', return_value='boxa-dom0'), \
                patch.object(t, 'operation_lock', return_value=nullcontext()):
            result = t.invoke(self.work.name, 'boot-recover')
        self.assertEqual(result['stage'], 'recovered')
        self.assertEqual(self.backend.running, 'old')

    def test_native_watchdog_identity_is_bound_to_process_and_operation(self):
        helper = Path(self.temp.name) / 'watch-test.py'
        helper.write_text('import time\ntime.sleep(15)\n')
        backend = t.Native()
        with patch.object(t, 'HELPER', str(helper)):
            identity = backend.watch(self.work.name, self.work)
            try:
                self.assertTrue(backend.watcher_alive(identity, self.work.name))
                self.assertFalse(backend.watcher_alive(identity, 'f' * 24))
                self.assertFalse(backend.watcher_alive({**identity, 'start_ticks': '0'}, self.work.name))
                self.assertFalse(backend.watcher_alive({**identity, 'boot_id': 'other'}, self.work.name))
            finally:
                os.kill(identity['pid'], 15)
                backend._watch_process.wait(timeout=3)

    def test_watchdog_expiry_recovers_but_accepted_watch_exits(self):
        for stage in ('booted', 'accepted'):
            with patch.object(t, 'require_dom0'), patch.object(t, 'invoke', return_value={
                    'stage': stage, 'deadline': 0}) as invoke:
                t.main(['watch', '--operation-id', self.work.name])
            self.assertEqual([call.args[1] for call in invoke.call_args_list],
                             ['status', 'recover'] if stage == 'booted' else ['status'])

    def test_deadline_missing_watch_and_unknown_uuid_refuse_mutation(self):
        tx = self.tx(); tx.arm()
        self.backend.alive = False
        with self.assertRaisesRegex(t.Refused, 'not running'): tx.step('stop')
        self.backend.alive = True; self.now += 1800
        with self.assertRaisesRegex(t.Refused, '30 minutes'): self.tx().step('stop')
        self.backend.bad_uuid = True
        with self.assertRaisesRegex(t.Refused, 'identity changed'): self.tx().recover()
        self.assertEqual(self.backend.running, 'old')
        self.assertEqual(self.tx().journal['stage'], 'recovery-failed')

    def test_wrong_configuration_and_changed_disk_are_rejected(self):
        with (self.work / 'new.cfg').open('a') as stream: stream.write('print("unsafe")\n')
        with self.assertRaisesRegex(t.Refused, 'checksum'): self.tx()
        with self.assertRaises(t.Refused): t.configuration('import os\n', 'bak', self.request['old_uuid'])

    def test_candidate_must_have_explicit_uuid_before_old_guest_stops(self):
        path = self.work / 'new.cfg'
        content = '\n'.join(line for line in path.read_text().splitlines() if not line.startswith('uuid =')) + '\n'
        path.write_text(content)
        self.request['new_config_sha256'] = hashlib.sha256(content.encode()).hexdigest()
        t.store(self.work / 'request.json', self.request)
        with self.assertRaisesRegex(t.Refused, 'exact recorded UUID'):
            self.tx().arm()
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.backend.running, 'old')

    def test_changed_boot_artifact_and_lv_uuid_block_start(self):
        tx = self.tx(); tx.arm(); tx.step('stop')
        with patch.object(self.backend, 'artifact', return_value={'sha256': 'e' * 64, 'bytes': 4096}):
            with self.assertRaisesRegex(t.Refused, 'boot artifact'): tx.step('start')
        self.assertIsNone(self.backend.running)
        original = self.backend.lv
        with patch.object(self.backend, 'lv', side_effect=lambda p: {**original(p), 'uuid': 'changed-id'}):
            with self.assertRaisesRegex(t.Refused, 'replaced or resized'): tx.step('start')
        self.assertEqual(self.tx().recover(), 'recovered')

    def test_expired_recovery_fails_without_starting_another_guest(self):
        tx = self.boot(); tx.record('recovering', recovery_started_at=self.now - 1800)
        with self.assertRaisesRegex(t.Refused, '30-minute budget'): self.tx().recover()
        self.assertEqual(self.tx().journal['stage'], 'recovery-failed')
        self.assertNotIn(('start', 'old'), self.backend.calls)

    def test_foreign_guest_holding_new_disk_blocks_arm(self):
        original = self.backend.inventory
        foreign = {'domid': 8, 'config': {'c_info': {'name': 'unknown', 'uuid': self.request['new_uuid']},
                    'disks': [{'pdev_path': '/dev/vg0/new-os'}]}}
        with patch.object(self.backend, 'inventory', side_effect=lambda: original() + [foreign]):
            with self.assertRaisesRegex(t.Refused, 'another Xen guest'): self.tx().arm()
        self.assertEqual(self.backend.calls, [])

    def test_swapped_readonly_and_duplicate_live_disk_mappings_refuse_arm(self):
        for change in ('swapped', 'readonly', 'duplicate', 'missing-mode'):
            rows = self.backend.inventory()
            disks = rows[1]['config']['disks']
            if change == 'swapped':
                disks[0]['vdev'], disks[1]['vdev'] = disks[1]['vdev'], disks[0]['vdev']
            elif change == 'readonly':
                disks[1]['readwrite'] = 0
            elif change == 'duplicate':
                disks.append(copy.deepcopy(disks[0]))
            else:
                del disks[1]['readwrite']
            with self.subTest(change=change), patch.object(self.backend, 'inventory', return_value=rows):
                with self.assertRaisesRegex(t.Refused, 'device names or write modes'):
                    self.tx().arm()
            self.assertEqual(self.backend.calls, [])

    def test_autostart_fencing_preserves_router_and_controller(self):
        for role in ('bak', 'dmz', 'iot', 'router', 'ops'):
            (self.xen / 'auto' / (role + '.cfg')).symlink_to('../' + role + '.cfg')
        t.fence_autostart()
        self.assertEqual({p.name for p in (self.xen / 'auto').iterdir()}, {'router.cfg', 'ops.cfg'})

    def test_wrong_role_pointer_blocks_boot(self):
        self.boot()
        (self.base / 'active/bak.json').rename(self.base / 'active/dmz.json')
        with patch.object(t, 'require_dom0'), patch.object(t, 'invoke') as invoke:
            with self.assertRaisesRegex(t.Refused, 'different guest'): t.main(['boot-recover'])
        invoke.assert_not_called()

    def test_replay_and_missing_checks_are_rejected(self):
        tx = self.boot()
        with self.assertRaisesRegex(t.Refused, 'already been armed'): self.tx().arm()
        with self.assertRaisesRegex(t.Refused, 'skip'): tx.step('accept')
        self.checks(); v = t.read(self.work / 'checks.json'); v['checks']['application_images_unchanged'] = False
        t.store(self.work / 'checks.json', v)
        with self.assertRaisesRegex(t.Refused, 'incomplete'): tx.step('tested')

    def test_boot_recovery_reads_only_active_pointers(self):
        tx = self.boot()
        # Historical garbage must not overwrite the active assignment.
        other = self.base / 'operations' / ('f' * 24); other.mkdir()
        (other / 'journal.json').write_text('{"stage":"accepted"}')
        with patch.object(t, 'require_dom0'), patch.object(t, 'invoke', return_value={}) as invoke:
            t.main(['boot-recover'])
        invoke.assert_called_once_with(self.work.name, 'boot-recover')


class Budgets(unittest.TestCase):
    def tearDown(self):
        signal.setitimer(signal.ITIMER_REAL, 0)

    def test_nested_budget_cannot_extend_outer_lock_wait_deadline(self):
        with self.assertRaisesRegex(t.Refused, 'time budget expired'):
            with t.budget(0.05):
                with t.budget(10):
                    time.sleep(0.2)

    def test_nested_return_restores_remaining_outer_budget(self):
        with self.assertRaisesRegex(t.Refused, 'time budget expired'):
            with t.budget(0.1):
                with t.budget(10):
                    time.sleep(0.02)
                self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
                time.sleep(0.2)


class PersistentStorage(unittest.TestCase):
    def verify(self, mount, state_device=253, lv_device=253):
        def read(path):
            return 'control_d\n' if str(path) == '/proc/xen/capabilities' else mount
        with patch.object(t.os, 'geteuid', return_value=0), patch.object(t, 'secure'), \
                patch.object(t.Path, 'read_text', read), \
                patch.object(t.Path, 'stat', return_value=SimpleNamespace(st_dev=state_device)), \
                patch.object(t.Native, 'lv', return_value={'device': lv_device}):
            t.require_dom0()

    def test_only_persistent_writable_data_lv_accepts_journals(self):
        device = os.makedev(253, 0)
        good = '39 27 253:0 / /mnt/dom0_data rw,relatime - ext4 /dev/vg0/lv_dom0_data rw\n'
        self.verify(good, device, device)
        for bad in ('', good + good, good.replace('ext4', 'tmpfs'),
                    good.replace(' / /mnt', ' /bound /mnt'),
                    good.replace('rw,relatime', 'ro,relatime')):
            with self.subTest(mount=bad), self.assertRaises(t.Refused):
                self.verify(bad, device, device)
        with self.assertRaisesRegex(t.Refused, 'mounted persistent'):
            self.verify(good, device + 1, device)
        with self.assertRaisesRegex(t.Refused, 'mounted persistent'):
            self.verify(good, device, device + 1)


if __name__ == '__main__':
    unittest.main()
