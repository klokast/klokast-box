"""Log retention deletes only expired log files and whole old evidence entries."""
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))
import log_retention as retention

NOW = 2_000_000_000
OLD = NOW - 30 * 86400
NEW = NOW - 86400


class RetentionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.prefix = os.path.realpath(directory.name)

    def file(self, path, when=OLD, text='x'):
        target = Path(self.prefix + path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        os.utime(target, (when, when))
        return target

    def clean(self, machine='boxa-ops', apply=True):
        return retention.clean(machine, apply, self.prefix, NOW)

    def test_runs_keep_the_newest_runs_and_never_delete_records(self):
        root = '/var/lib/klokast/updates/discovery/router/'
        logs = [self.file(root + '%02d/render.log' % n, OLD + n) for n in range(12)]
        records = [self.file(root + '%02d/arguments.json' % n, OLD + n) for n in range(12)]
        recent = self.file(root + '00/late.log', NEW)
        result = self.clean()
        # The recent file makes run 00 the newest, so runs 01 and 02 are outside the newest 10.
        self.assertEqual(result['deleted']['files'], 2)
        self.assertFalse(logs[1].exists() or logs[2].exists())
        self.assertTrue(all(p.exists() for p in records + logs[3:] + [recent, logs[0]]))

    def test_loose_controller_logs_expire_by_age(self):
        loose = self.file('/home/smith/private/klokast/k002-ops-update-20261114T085049Z.log')
        fresh = self.file('/home/smith/private/klokast/today.log', NEW)
        self.clean()
        self.assertFalse(loose.exists()); self.assertTrue(fresh.exists())

    def test_evidence_is_plain_scratch_and_emptied_directories_go(self):
        base = '/home/smith/private/klokast/evidence/'
        old = self.file(base + 'repro-matrix/out/result.json')
        kept = self.file(base + 'audit/KEEP'); fresh = self.file(base + 'current/new.md', NEW)
        result = self.clean()
        self.assertFalse(old.exists() or kept.exists() or old.parent.parent.exists())
        self.assertTrue(fresh.exists() and Path(self.prefix + base).is_dir())
        self.assertEqual(result['deleted']['files'], 2)

    def test_journal_entries_stay_and_other_journal_files_expire(self):
        journal = '/var/lib/klokast/operations-journal/'
        note = self.file(journal + 'cases/router-updater/20260901T000000Z-XXabCdEf.md')
        forum = self.file(journal + 'forum/router-updater/20260901T000000Z-abandoned-seed.md')
        raw = self.file(journal + 'cases/router-updater/images-plan.log')
        code = self.file(journal + 'cases/vm-nightly/public-tests/internal/README.md')
        fresh = self.file(journal + 'cases/vm-nightly/today.log', NEW)
        recovery = self.file('/var/lib/klokast/updates/recovery/1/step.log')
        result = self.clean()
        self.assertTrue(note.exists() and forum.exists() and fresh.exists() and recovery.exists())
        self.assertFalse(raw.exists() or code.exists() or code.parent.exists())
        groups = {r['rule']: r for r in result['roots'] if r['root'].endswith('/operations-journal')}
        self.assertEqual(groups['audit']['children'], {'cases': 1, 'forum': 1})
        self.assertEqual((groups['age']['files'], groups['age']['expire']), (3, 2))

    def test_dry_run_plans_without_deleting(self):
        loose = self.file('/home/smith/private/klokast/old.log')
        result = self.clean(apply=False)
        self.assertTrue(loose.exists())
        self.assertEqual((result['totals']['expire'], result['deleted']['files']), (1, 0))

    def test_unregistered_logs_are_reported_and_kept(self):
        stray = self.file('/srv/app/debug.log')
        result = self.clean('boxa-bak')
        self.assertTrue(stray.exists())
        self.assertEqual([r['path'] for r in result['unregistered']], [str(stray)])

    def test_router_templates_on_dom0_are_audited_and_test_runs_expire(self):
        template = self.file('/mnt/dom0_data/klokast-router-templates/a/build.log')
        test = self.file('/mnt/dom0_data/klokast-vm-recovery-tests/a/state/operations/b/run.log')
        for n in range(retention.RUNS): self.file('/mnt/dom0_data/klokast-vm-recovery-tests/n%d/run.log' % n, NEW)
        xen = self.file('/var/log/xen/qemu-dm-old.log')
        self.clean('boxa-dom0')
        self.assertTrue(template.exists())
        self.assertFalse(test.exists() or xen.exists())

    def test_air_tool_logs_and_old_handoff_exports_expire(self):
        mcp = self.file('/home/agent/.cache/claude-cli-nodejs/-home-agent-src/mcp-logs-claude-artifacts/a.jsonl')
        npm = self.file('/home/agent/.npm/_logs/2026-10-01-debug-0.log')
        exports = [self.file('/home/agent/.local/state/klokast/handoffs/%024x/export.json' % n, OLD + n)
                   for n in range(retention.RUNS + 1)]
        self.clean('boxa-air')
        self.assertFalse(mcp.exists() or npm.exists() or exports[0].exists())
        self.assertTrue(all(p.exists() for p in exports[1:]))

    def test_links_and_changed_files_are_never_deleted(self):
        outside = self.file('/etc/secret.log')
        link = Path(self.prefix + '/home/smith/private/klokast/link.log')
        link.parent.mkdir(parents=True, exist_ok=True); link.symlink_to(outside)
        self.clean()
        self.assertTrue(outside.exists() and link.is_symlink())
        target = self.file('/home/smith/private/klokast/old.log')
        _, _, rows, units = retention.scan('boxa-ops', self.prefix, NOW)
        os.utime(target, (OLD + 5, OLD + 5))
        with self.assertRaisesRegex(RuntimeError, 'changed after the scan'): retention.remove(units[0], rows)
        self.assertTrue(target.exists())

    def test_check_accepts_only_listed_files(self):
        log = self.file('/var/lib/klokast/ops-nightly/20261101T000000.000000Z.log')
        secret = self.file('/var/lib/klokast/ops-nightly/credentials.json')
        self.assertEqual(retention.check('boxa-ops', str(log), self.prefix)['path'], str(log))
        for path in (str(secret), str(log.parent) + '/../ops-nightly/' + log.name, 'relative.log'):
            with self.assertRaises(ValueError): retention.check('boxa-ops', path, self.prefix)

    def test_large_files_are_flagged(self):
        self.file('/var/log/messages', NEW)
        with patch.object(retention, 'LARGE', 0):
            _, found, rows, _ = retention.scan('boxa-router', self.prefix, NOW)
        self.assertEqual(retention.summary('boxa-router', found, rows)['totals']['large'], 1)

    def test_a_refusal_prints_its_cause_for_the_controller(self):
        import contextlib, io, json
        output = io.StringIO()
        with patch.object(retention.socket, 'gethostname', return_value='boxa-dom0'), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = retention.main(['--machine', 'boxa-dom0', 'check', '/etc/shadow'])
        value = json.loads(output.getvalue())
        self.assertEqual((code, value['state']), (1, 'failed'))
        self.assertIn('not a log file that the retention policy lists: /etc/shadow', value['error'])

    def test_machine_names_select_the_role(self):
        self.assertEqual(retention.role_of('k001-vpn-egress'), 'vpn-egress')
        with self.assertRaises(ValueError): retention.role_of('k001-print')


class CodexSessionTests(unittest.TestCase):
    """Old Codex sessions leave through codex delete; linked or open sessions stay."""
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.prefix = os.path.realpath(directory.name)
        self.home = Path(self.prefix + '/home/agent/.codex')
        (self.home / 'sessions').mkdir(parents=True)
        self.db = sqlite3.connect(self.home / 'state_5.sqlite')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE threads (id TEXT, rollout_path TEXT, updated_at INTEGER, attached_thread_parent_id TEXT)')
        self.deleted = []
        def delete(prefix, session):
            self.deleted.append(session)
            row = self.db.execute('SELECT rollout_path FROM threads WHERE id=?', (session,)).fetchone()
            os.unlink(prefix + row[0])
        mock = patch.object(retention, 'codex_delete', side_effect=delete); mock.start(); self.addCleanup(mock.stop)

    def session(self, number, when, parent=None, attached=None, first=None):
        session = '00000000-0000-0000-0000-%012d' % number
        path = '/home/agent/.codex/sessions/2026/10/01/rollout-%s.jsonl' % session
        meta = {'type': 'session_meta', 'payload': {'id': session}}
        if parent: meta['payload']['history_base'] = {'thread_id': parent}
        target = Path(self.prefix + path); target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((first if first is not None else json.dumps(meta)) + '\n')
        os.utime(target, (when, when))
        self.db.execute('INSERT INTO threads VALUES (?,?,?,?)', (session, path, when, attached)); self.db.commit()
        return session, target

    def clean(self):
        return retention.clean('boxa-air', True, self.prefix, NOW)

    def test_old_sessions_are_deleted_natively(self):
        old, old_file = self.session(1, OLD)
        new, new_file = self.session(2, NEW)
        result = self.clean()
        self.assertEqual((self.deleted, result['deleted']['sessions']), ([old], 1))
        self.assertTrue(new_file.exists())

    def test_fork_and_attachment_links_keep_old_sessions(self):
        parent, _ = self.session(1, OLD)
        self.session(2, NEW, parent=parent)
        ancestor, _ = self.session(3, OLD)
        middle, _ = self.session(4, OLD, parent=ancestor)
        self.session(5, NEW, attached=middle)
        self.clean()
        self.assertEqual(self.deleted, [])

    def test_a_live_writer_keeps_its_session(self):
        session, _ = self.session(1, OLD)
        locks = self.home / 'thread-writer-locks'; locks.mkdir()
        with open(locks / (session + '.lock'), 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.clean()
        self.assertEqual(self.deleted, [])

    def test_unknown_lineage_keeps_every_session(self):
        self.session(1, OLD)
        self.session(2, NEW, first='not json')
        self.clean()
        self.assertEqual(self.deleted, [])

    def test_unindexed_old_rollout_is_a_plain_file(self):
        orphan = Path(self.prefix + '/home/agent/.codex/sessions/2026/09/01/rollout-orphan.jsonl')
        orphan.parent.mkdir(parents=True); orphan.write_text('{}\n'); os.utime(orphan, (OLD, OLD))
        self.clean()
        self.assertFalse(orphan.exists() or orphan.parent.exists())
        self.assertEqual(self.deleted, [])

    def test_a_delete_that_keeps_the_file_is_an_error(self):
        self.session(1, OLD)
        with patch.object(retention, 'codex_delete'):
            result = self.clean()
        self.assertIn('codex delete kept the session file', result['errors'][0])


class CodexCommandTests(unittest.TestCase):
    def test_the_native_command_runs_as_the_runner_account(self):
        with patch.object(retention.os, 'geteuid', return_value=0), \
                patch.object(retention.subprocess, 'run', return_value=type('R', (), {'returncode': 0})()) as run:
            retention.codex_delete('', '00000000-0000-0000-0000-000000000001')
        self.assertEqual(run.call_args.args[0][:6], ['su', '-s', '/bin/sh', 'agent', '-c', 'exec "$0" "$@"'])
        self.assertEqual(run.call_args.args[0][6:], [retention.CODEX, 'delete', '--force', '00000000-0000-0000-0000-000000000001'])


if __name__ == '__main__': unittest.main()
