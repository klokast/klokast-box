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
                '/dev/vg0/new-os': 3, '/dev/vg0/new-data': 4,
                '/dev/vg0/next-os': 5, '/dev/vg0/next-data': 6}[path]

    def lv(self, path):
        side = 'old' if path in self.request['old_disks'] else 'new'
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
        self.running = 'new' if self.request['new_uuid'] in path.read_text() else 'old'
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
                'firewall', 'no_application_qualification', 'data', 'application_images_unchanged', 'quarantine')}})

    def boot(self):
        tx = self.tx(); tx.arm(); tx.step('stop'); tx.step('start'); return tx

    def liveness_request(self):
        self.request.update(kind='klokast.vm-switch.v2', controller_timeout_seconds=90)
        (self.work / 'request.json').write_text(json.dumps(self.request))

    def test_controller_heartbeat_never_extends_the_replacement_deadline(self):
        self.liveness_request()
        tx = self.tx(); tx.arm()
        deadline = tx.journal['deadline']
        self.now += 30
        tx.step('heartbeat')
        self.assertEqual(tx.journal['deadline'], deadline)
        self.assertEqual(tx.journal['controller_seen_at'], self.now)
        self.now += 89
        self.assertFalse(t.controller_lost(tx.journal, self.now))
        self.now += 1
        self.assertTrue(t.controller_lost(tx.journal, self.now))
        for action in ('heartbeat', 'stop', 'start', 'accept'):
            with self.assertRaisesRegex(t.Refused, 'liveness expired'):
                tx.step(action)
        self.assertEqual(self.tx().recover(), 'recovered')

    def test_controller_loss_before_acceptance_recovers_but_after_acceptance_preserves_writes(self):
        self.liveness_request()
        tx = self.boot(); self.checks(); tx.step('tested'); tx.step('accept')
        self.now += 2000
        self.assertFalse(t.controller_lost(self.tx().journal, self.now))
        self.assertEqual(self.tx().recover(), 'preserve-production-data')
        self.assertEqual(self.backend.running, 'new')
        with self.assertRaisesRegex(t.Refused, 'live pre-acceptance'):
            self.tx().step('heartbeat')

    def test_controller_loss_watchdog_dispatches_recovery_without_waiting_for_hard_deadline(self):
        self.liveness_request()
        tx = self.boot(); self.now += 90
        with patch.object(t, 'require_dom0'), patch.object(t.time, 'time', return_value=self.now), \
                patch.object(t, 'invoke', side_effect=[tx.journal, {'stage':'recovered'}]) as invoke:
            self.assertEqual(t.main(['watch', '--operation-id', self.work.name]), 0)
        self.assertEqual(invoke.call_args_list[-1].args, (self.work.name, 'recover'))

    def test_liveness_refuses_clock_reversal_invalid_timeout_and_tampered_journal(self):
        self.liveness_request()
        tx = self.tx(); tx.arm(); self.now -= 1
        self.assertTrue(t.controller_lost(tx.journal, self.now))
        with self.assertRaisesRegex(t.Refused, 'liveness expired'):
            tx.step('heartbeat')
        tx.record('armed', controller_seen_at=float('nan'))
        with self.assertRaisesRegex(t.Refused, 'liveness evidence'):
            self.tx()
        self.request['controller_timeout_seconds'] = 1800
        (self.work / 'request.json').write_text(json.dumps(self.request))
        with self.assertRaisesRegex(t.Refused, '90-second'):
            self.tx()

    def test_legacy_request_does_not_gain_a_heartbeat_interface(self):
        tx = self.tx(); tx.arm()
        with self.assertRaisesRegex(t.Refused, 'live pre-acceptance'):
            tx.step('heartbeat')

    def test_full_switch_acceptance_and_no_data_rollback(self):
        tx = self.boot(); self.checks(); tx.step('tested'); tx.step('accept'); tx.step('complete')
        self.assertEqual(self.tx().journal['stage'], 'complete')
        self.assertEqual(self.tx().recover(), 'preserve-production-data')
        self.assertEqual(self.backend.running, 'new')
        self.assertNotIn(('start', 'old'), self.backend.calls)
        self.assertIn('new-os', (self.xen / 'bak.cfg').read_text())
        self.assertIn('name = "bak"', (self.xen / 'bak.cfg').read_text())

    def assignment_status(self):
        with patch.object(t.socket, 'gethostname', return_value='boxa-dom0'), \
                patch.object(t, 'operation_lock', return_value=nullcontext()):
            return t.assignment_status('bak', self.backend)

    def source_status(self):
        with patch.object(t.socket, 'gethostname', return_value='boxa-dom0'), \
                patch.object(t, 'operation_lock', return_value=nullcontext()):
            return t.source_status('bak', self.backend)

    def test_unmanaged_source_receipt_binds_live_disk_and_boot_artifacts(self):
        before = list(self.backend.calls)
        value = self.source_status()
        self.assertEqual(value['kind'], 'klokast.vm-unmanaged-source.v1')
        self.assertEqual(value['vm_uuid'], self.request['old_uuid'])
        self.assertEqual(value['configuration_sha256'], self.request['old_config_sha256'])
        self.assertEqual(value['disk_mappings'], {'/dev/vg0/old-os': 'xvda',
                                                  '/dev/vg0/old-data': 'xvdb'})
        self.assertEqual(value['disks'], self.request['old_disks'])
        self.assertEqual(value['artifacts'], self.request['old_artifacts'])
        self.assertEqual(value['runtime'], 'running')
        self.assertFalse(value['autostart'])
        self.assertEqual(self.backend.calls, before)
        (self.xen / 'auto/bak.cfg').symlink_to('../bak.cfg')
        self.assertTrue(self.source_status()['autostart'])

    def test_unmanaged_source_rejects_changed_or_shared_disk_and_assignment(self):
        self.backend.bad_uuid = True
        with self.assertRaisesRegex(t.Refused, 'configuration UUID differs'):
            self.source_status()
        self.backend.bad_uuid = False
        with patch.object(self.backend, 'device', side_effect=lambda path: 1):
            with self.assertRaisesRegex(t.Refused, 'device identities overlap'):
                self.source_status()
        self.backend.running = None
        with self.assertRaisesRegex(t.Refused, 'exactly one running guest'):
            self.source_status()
        self.backend.running = 'old'
        t.store(self.base / 'active/bak.json', {'operation_id': self.work.name,
                                               'request_sha256': t.digest(self.request)})
        with self.assertRaisesRegex(t.Refused, 'unmanaged guest'):
            self.source_status()

    def test_current_assignment_distinguishes_unmanaged_pending_and_accepted(self):
        self.assertFalse(self.assignment_status()['managed'])
        tx = self.boot()
        pending = self.assignment_status()
        self.assertEqual(pending['selection'], 'pending')
        self.assertIsNone(pending['release_sha256'])
        self.assertNotIn('boot_configuration', pending)
        self.checks(); tx.step('tested'); tx.step('accept')
        before = list(self.backend.calls)
        value = self.assignment_status()
        self.assertEqual(value['selection'], 'accepted')
        self.assertEqual(value['release_sha256'], self.request['release_sha256'])
        self.assertFalse(value['configuration_drift']); self.assertFalse(value['autostart_drift'])
        self.assertEqual(value['runtime'], 'running')
        self.assertEqual(self.backend.calls, before)
        line = (self.xen / 'bak.cfg').read_text().splitlines()[0]
        provenance = json.loads(line.removeprefix('# klokast-provenance: '))
        self.assertEqual(provenance['request_sha256'], t.digest(self.request))
        self.assertEqual(provenance['release_sha256'], value['release_sha256'])
        self.assertEqual(provenance['profile'], 'shared-alpine-v1')

    def test_assignment_reports_local_edits_without_promoting_or_repairing_them(self):
        tx = self.boot(); self.checks(); tx.step('tested'); tx.step('accept')
        path = self.xen / 'bak.cfg'
        changed = path.read_text().replace('new-kernel', 'obsolete-kernel')
        path.write_text(changed)
        (self.xen / 'auto/bak.cfg').unlink()
        value = self.assignment_status()
        self.assertTrue(value['configuration_drift']); self.assertTrue(value['autostart_drift'])
        self.assertEqual(value['boot_configuration']['kernel'], '/mnt/dom0_data/new-kernel')
        self.assertEqual(path.read_text(), changed)
        with patch.object(self.backend, 'artifact', return_value={'sha256': '0' * 64, 'bytes': 4096}):
            with self.assertRaisesRegex(t.Refused, 'boot artifact'): self.assignment_status()

    def test_recovered_assignment_does_not_claim_the_candidate_release(self):
        tx = self.boot(); tx.recover()
        value = self.assignment_status()
        self.assertEqual(value['selection'], 'recorded-previous')
        self.assertIsNone(value['release_sha256']); self.assertIsNone(value['profile'])
        self.assertEqual(value['boot_configuration']['kernel'], '/mnt/dom0_data/old-kernel')
        self.assertFalse(value['configuration_drift'])

    def reconcile(self, state, request_sha256=None):
        with patch.object(t.socket, 'gethostname', return_value='boxa-dom0'), \
                patch.object(t, 'operation_lock', return_value=nullcontext()):
            return t.reconcile_assignment('bak', request_sha256 or t.digest(self.request), state,
                                          'e' * 64, self.backend)

    def test_runtime_reconcile_preserves_release_and_survives_boot_recovery(self):
        tx = self.boot(); self.checks(); tx.step('tested'); tx.step('accept'); tx.step('complete')
        original = (self.work / 'request.json').read_bytes()
        result = self.reconcile('stopped')
        self.assertTrue(result['changed']); self.assertFalse(result['autostart'])
        self.assertEqual(result['runtime'], 'stopped')
        self.tx().recover()
        self.assertIsNone(self.backend.running)
        self.assertFalse((self.xen / 'auto/bak.cfg').exists())
        self.assertFalse(self.reconcile('stopped')['changed'])
        result = self.reconcile('running')
        self.assertTrue(result['autostart']); self.assertEqual(self.backend.running, 'new')
        self.assertEqual(result['release_sha256'], self.request['release_sha256'])
        self.assertEqual((self.work / 'request.json').read_bytes(), original)
        self.assertFalse(self.reconcile('running')['changed'])

    def test_runtime_reconcile_repairs_generated_config_from_protected_input(self):
        tx = self.boot(); self.checks(); tx.step('tested'); tx.step('accept'); tx.step('complete')
        path = self.xen / 'bak.cfg'
        path.write_text(path.read_text().replace('new-kernel', 'obsolete-kernel'))
        result = self.reconcile('running')
        self.assertTrue(result['changed']); self.assertFalse(result['configuration_drift'])
        self.assertIn('new-kernel', path.read_text()); self.assertNotIn('obsolete-kernel', path.read_text())
        path.unlink()
        self.assertTrue(self.assignment_status()['configuration_drift'])
        self.assertTrue(self.reconcile('running')['changed'])

    def test_runtime_reconcile_refuses_pending_or_changed_assignment(self):
        tx = self.boot()
        with self.assertRaisesRegex(t.Refused, 'completed operation'): self.reconcile('stopped')
        self.checks(); tx.step('tested'); tx.step('accept')
        with self.assertRaisesRegex(t.Refused, 'completed operation'): self.reconcile('stopped')
        tx.step('complete')
        before = list(self.backend.calls)
        with self.assertRaisesRegex(t.Refused, 'changed or is absent'): self.reconcile('stopped', '0' * 64)
        self.assertEqual(self.backend.calls, before)

    def test_runtime_intent_rejects_corrupt_or_foreign_state(self):
        tx = self.boot(); tx.recover()
        self.reconcile('stopped')
        path = self.work / 'runtime.json'
        original = t.read(path)
        for fields in ({'request_sha256': '0' * 64}, {'runtime_state': 'restart'}, {'extra': True}):
            t.store(path, dict(original, **fields))
            with self.assertRaisesRegex(t.Refused, 'runtime intent'): self.tx().recover()
        t.store(path, original)
        self.reconcile('running')
        self.assertEqual(self.backend.running, 'old')

    def next_generation(self):
        tx = self.boot(); self.checks(); tx.step('tested'); tx.step('accept'); tx.step('complete')
        work = self.base / 'operations' / ('b' * 24); work.mkdir()
        request = dict(self.request, operation_id=work.name, release_sha256='f' * 64,
                       old_uuid=self.request['new_uuid'], new_uuid='33333333-3333-4333-8333-333333333333')
        old = (self.xen / 'bak.cfg').read_bytes()
        new = old.decode().replace(self.request['new_uuid'], request['new_uuid']).replace('new-', 'next-').encode()
        for side, content in (('old', old), ('new', new)):
            (work / (side + '.cfg')).write_bytes(content)
            request[side + '_config_sha256'] = hashlib.sha256(content).hexdigest()
            request[side + '_disks'] = {(k.replace('new-', 'next-') if side == 'new' else k): v
                                      for k, v in self.request['new_disks'].items()}
            request[side + '_artifacts'] = {(k.replace('new-', 'next-') if side == 'new' else k): v
                                          for k, v in self.request['new_artifacts'].items()}
        t.store(work / 'request.json', request)
        backend = Xen(request)
        return work, request, backend

    def test_next_generation_recovery_retains_previous_release_identity(self):
        work, request, backend = self.next_generation()
        tx = t.Transaction(work, backend, lambda: self.now)
        tx.arm(); tx.step('stop'); tx.step('start'); tx.recover()
        previous = tx.journal['previous_assignment']
        self.assertEqual(previous['operation_id'], self.work.name)
        self.assertEqual(previous['release_sha256'], self.request['release_sha256'])
        result = t.assignment_report(tx)
        self.assertEqual(result['selection'], 'recorded-previous')
        self.assertEqual(result['release_sha256'], self.request['release_sha256'])
        self.assertEqual(result['vm_uuid'], request['old_uuid'])
        provenance = json.loads((self.xen / 'bak.cfg').read_text().splitlines()[0].split(': ', 1)[1])
        self.assertEqual(provenance['release_sha256'], self.request['release_sha256'])

    def test_next_generation_refuses_drift_as_previous_accepted_configuration(self):
        work, request, backend = self.next_generation()
        content = (work / 'old.cfg').read_bytes().replace(b'new-kernel', b'obsolete-kernel')
        (work / 'old.cfg').write_bytes(content)
        (self.xen / 'bak.cfg').write_bytes(content)
        request['old_config_sha256'] = hashlib.sha256(content).hexdigest()
        request['old_artifacts'] = {k.replace('new-kernel', 'obsolete-kernel'): v
                                    for k, v in request['old_artifacts'].items()}
        t.store(work / 'request.json', request)
        with self.assertRaisesRegex(t.Refused, 'protected previous assignment'):
            t.Transaction(work, backend, lambda: self.now).arm()
        self.assertEqual(backend.calls, [])
        self.assertEqual(t.read(self.base / 'active/bak.json')['operation_id'], self.work.name)

    def test_assignment_pointer_conflicts_and_other_box_fail_closed(self):
        tx = self.boot(); self.checks(); tx.step('tested'); tx.step('accept')
        pointer = self.base / 'active/bak.json'
        original = t.read(pointer)
        t.store(pointer, {**original, 'request_sha256': '0' * 64})
        with self.assertRaisesRegex(t.Refused, 'differs'): self.assignment_status()
        t.store(pointer, original)
        with patch.object(t.socket, 'gethostname', return_value='other-dom0'), \
                patch.object(t, 'operation_lock', return_value=nullcontext()):
            with self.assertRaisesRegex(t.Refused, 'differs'): t.assignment_status('bak', self.backend)

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

    def test_recovery_finishes_pending_old_shutdown_before_restart(self):
        tx = self.tx(); tx.arm()
        with patch.object(self.backend, 'stop', side_effect=InterruptedError('shutdown request sent')):
            with self.assertRaises(InterruptedError): tx.step('stop')
        self.assertEqual(self.backend.running, 'old')
        self.assertEqual(self.tx().recover(), 'recovered')
        self.assertIn(('stop', 'old', True), self.backend.calls)
        self.assertIn(('start', 'old'), self.backend.calls)
        self.assertFalse(self.tx().journal['old_shutdown_pending'])

    def test_pending_old_shutdown_never_forces_or_duplicates_old_guest(self):
        tx = self.tx(); tx.arm()
        with patch.object(self.backend, 'stop') as stop:
            with self.assertRaisesRegex(t.Refused, 'still holds'): tx.step('stop')
            with self.assertRaisesRegex(t.Refused, 'still pending'): self.tx().recover()
            self.assertTrue(all(call.args[1] is True for call in stop.call_args_list))
        self.assertEqual(self.backend.running, 'old')
        self.assertEqual(self.tx().journal['stage'], 'recovery-failed')
        self.assertNotIn(('start', 'old'), self.backend.calls)

    def test_crash_after_recovery_restart_does_not_stop_old_guest_again(self):
        tx = self.tx(); tx.arm(); tx.record('stopping')
        # Reproduce power loss, which cannot be caught as a normal command
        # error, immediately after the recovery start has reached Xen.
        original = self.backend.start
        def crash(path):
            original(path)
            raise SystemExit('power loss')
        with patch.object(self.backend, 'start', side_effect=crash):
            with self.assertRaises(SystemExit): self.tx().recover()
        count = len(self.backend.calls)
        self.assertEqual(self.tx().recover(), 'recovered')
        self.assertNotIn(('stop', 'old', True), self.backend.calls[count:])

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
