"""The boot history: the dom0 recorder, the nightly check, the reader and kk boots."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))
sys.path.insert(0, str(ROOT / 'klokast-dev/lib'))
import boot_history  # noqa: E402
import platform_client as client  # noqa: E402

ROLE = ROOT / 'ansible/roles/dom0-boot-history'
A, B, C = ('%s-0000-4000-8000-000000000000' % (letter * 8) for letter in 'abc')


def load_recorder():
    loader = importlib.machinery.SourceFileLoader('boot_recorder', str(ROLE / 'files/klokast-boot-history'))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader('boot_recorder', loader))
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try: loader.exec_module(module)
    finally: sys.dont_write_bytecode = writes
    return module


class RecorderFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.m = load_recorder()
        self.m.DATA, self.m.ROOT, self.m.PENDING = self.data, self.data / 'history', self.data / 'pending.json'
        for target, name, value in ((self.m.os.path, 'ismount', lambda path: True),
                                    (self.m, 'local', lambda: ('boxa-dom0', A))):
            mock = patch.object(target, name, value); mock.start(); self.addCleanup(mock.stop)

    def lines(self, machine='boxa-dom0'):
        path = self.m.ROOT / (machine + '.jsonl')
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def read(self, machine='boxa-dom0'):
        path = self.m.ROOT / (machine + '.jsonl')
        return boot_history.history([machine + ' ' + line for line in path.read_text().splitlines()])[machine]


class Recorder(RecorderFixture):
    def test_one_boot_gives_one_line_that_any_account_can_read(self):
        previous = os.umask(0o077); self.addCleanup(os.umask, previous)
        self.m.start(); self.m.start(); self.m.stop()
        self.assertEqual([(e['event'], e['boot_id']) for e in self.lines()], [('boot', A), ('stop', A)])
        path = self.m.ROOT / 'boxa-dom0.jsonl'
        self.assertEqual((path.stat().st_mode & 0o777, self.m.ROOT.stat().st_mode & 0o777), (0o644, 0o755))
        self.assertIn('stopped_at', self.read()[0])

    def test_the_pending_operation_is_the_direct_evidence_of_the_cause(self):
        self.m.PENDING.write_text(json.dumps({'operation': 'c' * 24, 'reboot_test': True}))
        self.m.start()
        self.assertEqual((self.read()[0]['operation'], self.read()[0]['work']), ('c' * 24, 'reboot'))

    def test_an_unreadable_pending_operation_does_not_hide_the_boot(self):
        for text in ('{', '[]', '{"operation": 5}', '{"operation": "../x"}'):
            with self.subTest(text=text):
                (self.m.ROOT / 'boxa-dom0.jsonl').unlink(missing_ok=True)
                self.m.PENDING.write_text(text); self.m.start()
                self.assertNotIn('operation', self.lines()[0])

    def test_nothing_is_written_to_the_ram_root(self):
        with patch.object(self.m.os.path, 'ismount', lambda path: False):
            with self.assertRaisesRegex(RuntimeError, 'is not mounted'): self.m.start()
        self.assertFalse(self.m.ROOT.exists())

    def test_a_partial_line_of_a_power_loss_hides_no_other_boot(self):
        self.m.ROOT.mkdir(); (self.m.ROOT / 'boxa-dom0.jsonl').write_text('{"event": "bo')
        self.m.start()
        self.assertEqual([boot['boot_id'] for boot in self.read()], [A])

    def test_the_check_adds_only_new_boots(self):
        boots = {'boxa-dmz': {'boot_id': B, 'booted_at': 1000}}
        self.assertEqual([self.m.observe(boots), self.m.observe(boots)], [1, 0])
        self.assertEqual((self.lines('boxa-dmz')[0]['at'], self.read('boxa-dmz')[0]['source']), ('1970-01-01T00:16:40Z', 'check'))

    def test_the_check_corrects_a_wrong_boot_clock_once(self):
        self.m.start()
        at = self.m.seconds(self.lines()[0]['at'])
        observe = lambda booted: self.m.observe({'boxa-dom0': {'boot_id': A, 'booted_at': booted}})
        self.assertEqual([observe(at + 30), observe(at + 500), observe(at + 900)], [0, 1, 0])
        self.assertEqual([(b['at'], b['corrected'], b['source']) for b in self.read()], [(at + 500, True, 'boot')])

    def test_the_check_refuses_another_box_and_invalid_values(self):
        for machine, boot_id, booted in (('boxb-dmz', B, 1), ('../boxa-dmz', B, 1), ('boxa-dmz', 'x', 1), ('boxa-dmz', B, '1')):
            with self.subTest(machine=machine, boot_id=boot_id), self.assertRaisesRegex(RuntimeError, 'invalid boot'):
                self.m.observe({machine: {'boot_id': boot_id, 'booted_at': booted}})
        self.assertFalse(self.m.ROOT.exists())

    def test_the_service_never_delays_guests_and_records_only_a_machine_stop(self):
        text = (ROLE / 'files/klokast-boot-history.openrc').read_text()
        self.assertIn('need localmount', text)
        self.assertIn('after ntpd xendomains', text)
        self.assertNotIn('before ', text)
        self.assertIn('yesno "${RC_GOINGDOWN:-no}" || return 0', text)
        self.assertNotIn('eend 1', text)

    def test_the_installation_saves_the_service_and_provisioning_has_it(self):
        tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
        includes = next(t for t in tasks if 'loop' in t)['loop']
        self.assertEqual(includes, ['usr/local/sbin/klokast-boot-history', 'etc/init.d/klokast-boot-history'])
        self.assertIn('dom0-boot-history', (ROOT / 'ansible/playbooks/23-dom0-xen-host.yml').read_text())


class Controller(unittest.TestCase):
    def test_a_machine_states_only_its_own_boot(self):
        good = 'hostname boxa-dmz\nboot %s\nclock 2000\nuptime 500\n' % A
        with patch.object(boot_history.machine_status, 'run_probe', return_value=good):
            self.assertEqual(boot_history.current('boxa-dmz'), {'boot_id': A, 'booted_at': 1500})
            with self.assertRaisesRegex(RuntimeError, 'reports the hostname'): boot_history.current('boxa-iot')
        for bad in (good + 'extra 1\n', good.replace('500', '\x1b[31m'), good.replace('clock 2000\n', ''), good + good):
            with self.subTest(bad=bad), patch.object(boot_history.machine_status, 'run_probe', return_value=bad), \
                    self.assertRaisesRegex(RuntimeError, 'boot status'):
                boot_history.current('boxa-dmz')

    def check(self, boots, errors=None, code=0):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv); return type('R', (), {'returncode': code, 'stdout': '', 'stderr': ''})()
        declared = {'boxa-dom0': 'running', 'boxa-dmz': 'running', 'boxa-iot': 'stopped', 'boxb-dom0': 'running', 'boxb-dmz': 'running'}
        with patch.object(boot_history, 'require_active'), patch.object(boot_history.subprocess, 'run', run), \
                patch.object(boot_history.platform_source, 'snapshot', return_value={'instance': {}}), \
                patch.object(boot_history.machine_status, 'machines', return_value=declared), \
                patch.object(boot_history, 'observe', return_value=(boots, errors or {})) as observe:
            value = boot_history.check()
        self.assertNotIn('boxa-iot', observe.call_args.args[0])
        return value, calls

    def test_the_check_writes_each_boot_to_its_own_dom0(self):
        boot = {'boot_id': A, 'booted_at': 1}
        value, calls = self.check({n: boot for n in ('boxa-dom0', 'boxa-dmz', 'boxb-dom0', 'boxb-dmz')})
        self.assertEqual(value, {'state': 'complete', 'observed': 4})
        variables = json.loads(calls[0][-1])['boot_observations']
        self.assertEqual({host: sorted(v) for host, v in variables.items()},
                         {'boxa-dom0': ['boxa-dmz', 'boxa-dom0'], 'boxb-dom0': ['boxb-dmz', 'boxb-dom0']})

    def test_an_unreachable_dom0_does_not_stop_the_other_box(self):
        boot = {'boot_id': A, 'booted_at': 1}
        value, calls = self.check({n: boot for n in ('boxa-dmz', 'boxb-dom0')}, {'boxa-dom0': 'no answer'})
        self.assertEqual(value['state'], 'incomplete')
        self.assertEqual(calls[0][calls[0].index('--limit') + 1], 'boxb-dom0')
        value, _ = self.check({'boxb-dom0': boot}, code=2)
        self.assertEqual(value['state'], 'failed')

    def test_the_standby_does_not_check(self):
        with patch.object(boot_history, 'require_active', side_effect=RuntimeError('standby')), \
                patch.object(boot_history, 'check') as check:
            self.assertEqual(boot_history.nightly()['state'], 'skipped')
        check.assert_not_called()

    def test_the_reader_refuses_unsafe_values(self):
        line = 'boxa-dom0 ' + json.dumps({'event': 'boot', 'boot_id': A, 'at': '2026-01-01T00:00:00Z', 'source': 'boot', 'kernel': '6.18'})
        self.assertEqual(len(boot_history.history([line, line])['boxa-dom0']), 1)
        for old, new in (('6.18', '\\u001b[0m'), (A, 'x'), ('"boot",', '"other",'), ('"event": "boot"', '"event": "x"'), ('2026', '20x6')):
            with self.subTest(new=new), self.assertRaisesRegex(RuntimeError, 'invalid boot history'):
                boot_history.history([line.replace(old, new)])


class Describe(unittest.TestCase):
    def ends(self, target, boots, live=None, updates=()):
        return [row['end'] for row in boot_history.describe(target, boots, live, updates)]

    def test_only_a_recorded_dom0_boot_proves_a_stop_without_record(self):
        live = {'boot_id': C, 'booted_at': 300}
        boots = [dict(boot_id=A, at=100, source='boot', stopped_at=190), dict(boot_id=B, at=200, source='boot')]
        self.assertEqual(self.ends('boxa-dom0', boots, live), ['clean', 'no stop record', 'running'])
        # The check saw this boot, so the service was possibly not installed.
        self.assertEqual(self.ends('boxa-dom0', [boots[0], dict(boots[1], source='check')], live), ['clean', 'unknown', 'running'])
        # A VM never proves it.
        self.assertEqual(self.ends('boxa-dmz', [dict(boots[1], source='check')], live), ['unknown', 'running'])
        # Without a later boot the machine can still run.
        self.assertEqual(self.ends('boxa-dom0', boots), ['clean', 'unknown'])

    def test_a_current_boot_that_the_check_did_not_see_is_shown(self):
        rows = boot_history.describe('boxa-dmz', [], {'boot_id': C, 'booted_at': 300}, [])
        self.assertEqual([(r['source'], r['end']) for r in rows], [('live', 'running')])

    def test_an_update_explains_only_the_boot_that_the_check_saw_after_it(self):
        update = {'operation': 'd' * 24, 'at': 500, 'seeded': False}
        def operation(boot, update=update): return boot_history.describe('boxa-dmz', [boot], None, [update])[0].get('operation')
        boot = dict(boot_id=A, at=300, source='check', seen=900)
        self.assertEqual(operation(boot), 'd' * 24)
        # The check saw this boot before the update, so the update started another boot.
        self.assertIsNone(operation(dict(boot, seen=400)))
        self.assertIsNone(operation(boot, dict(update, seeded=True)))
        self.assertIsNone(operation(boot, dict(update, at=200)))
        self.assertIsNone(operation(dict(boot, seen=9000), dict(update, at=8000)))


class Show(unittest.TestCase):
    def test_selected_machines_come_from_the_declaration(self):
        declared = {'boxa-dom0': 'running', 'boxa-dmz': 'running', 'boxb-dom0': 'running'}
        with patch.object(boot_history, 'require_active'), \
                patch.object(boot_history.platform_source, 'snapshot', return_value={'instance': {}}), \
                patch.object(boot_history.machine_status, 'machines', return_value=declared), \
                patch.object(boot_history.machine_status, 'run_probe', side_effect=RuntimeError('no answer')):
            with self.assertRaisesRegex(RuntimeError, 'boxc-dmz: not a declared machine'): boot_history.show(['boxc-dmz'])
            value = boot_history.show(['boxa-dmz'])['machines']
        self.assertEqual([(m['target'], m['history_error'], m['error']) for m in value],
                         [('boxa-dmz', 'boxa-dom0: no answer', 'no answer')])


class KkBoots(unittest.TestCase):
    def run_kk(self, machines, *argv):
        calls, out = [], io.StringIO()
        def remote(user, host, command, **kwargs):
            calls.append(command); return {'observed_at': 1, 'machines': machines}
        with patch.object(client, 'controller', return_value='boxa'), patch.object(client, 'remote', side_effect=remote), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = client.main(['--instance', 'instance', 'boots', *argv])
        return code, out.getvalue(), calls

    BOOTS = [dict(at=1000 + i, end='no stop record', source='boot', corrected=False) for i in range(5)]

    def test_selected_machines_show_all_boots_newest_first(self):
        machine = dict(target='boxa-dom0', declared='running', boots=[*self.BOOTS,
            dict(at=9000, end='clean', stopped_at=9600, source='boot', operation='c' * 24, work='reboot', kernel='6.18', corrected=True),
            dict(at=12000, end='running', source='live')])
        code, out, calls = self.run_kk([machine], 'boxa-dom0', 'boxa-dmz')
        self.assertEqual((code, calls[0][1:]), (0, ['show', 'boxa-dom0', 'boxa-dmz']))
        rows = [line.split(None, 1)[1] for line in out.splitlines() if line.startswith('boxa-dom0')]
        self.assertEqual(len(rows), 7)
        self.assertRegex(rows[0], '^1970-01-01 03:20  running .*not recorded yet')
        self.assertRegex(rows[1], '^1970-01-01 02:30  clean stop 1970-01-01 02:40  reboot c{24}; clock corrected; kernel 6.18$')
        self.assertIn('NO STOP RECORD', rows[2])

    def test_without_a_machine_the_list_is_short(self):
        code, out, _ = self.run_kk([dict(target=t, declared='running', boots=self.BOOTS) for t in ('boxa-dom0', 'boxb-dom0')])
        self.assertEqual((code, sum(line.startswith('box') for line in out.splitlines())), (0, 6))

    def test_an_unread_source_needs_attention_and_hides_nothing_else(self):
        code, out, _ = self.run_kk([
            dict(target='boxa-dmz', declared='running', boots=[], error='no answer', history_error='boxa-dom0: no answer'),
            dict(target='boxb-iot', declared='stopped', boots=[])])
        self.assertEqual(code, 1)
        self.assertIn('boxa-dmz did not report its current boot: no answer', out)
        self.assertIn('The boot history was not read from boxa-dom0: no answer', out)
        self.assertIn('no recorded boot; declared stopped', out)

    def test_an_invalid_name_does_not_connect(self):
        code, out, calls = self.run_kk([], '--', '-x;y')
        self.assertEqual((code, calls), (2, []))


if __name__ == '__main__':
    unittest.main()
