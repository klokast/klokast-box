"""Log retention deletes only expired log files and whole old evidence entries."""
import os
from pathlib import Path
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

    def test_age_rule_covers_only_files_directly_in_the_root(self):
        loose = self.file('/home/smith/private/klokast/k002-ops-update-20261114T085049Z.log')
        fresh = self.file('/home/smith/private/klokast/today.log', NEW)
        self.clean()
        self.assertFalse(loose.exists()); self.assertTrue(fresh.exists())

    def test_evidence_entries_expire_as_a_whole_unless_kept(self):
        base = '/home/smith/private/klokast/evidence/'
        old = self.file(base + 'repro-matrix/out/result.json')
        kept = self.file(base + 'audit/inventory.md'); self.file(base + 'audit/KEEP')
        mixed = self.file(base + 'current/old.md'); self.file(base + 'current/new.md', NEW)
        result = self.clean()
        self.assertFalse(old.parent.parent.exists())
        self.assertTrue(kept.exists() and mixed.exists())
        self.assertEqual(result['deleted']['entries'], 1)

    def test_journal_and_recovery_records_are_audited_only(self):
        note = self.file('/var/lib/klokast/operations-journal/cases/router-updater/20260901T000000Z-a.md')
        forum = self.file('/var/lib/klokast/operations-journal/forum/router-updater/20260901T000000Z-b.md')
        raw = self.file('/var/lib/klokast/operations-journal/cases/router-updater/images-plan.log')
        recovery = self.file('/var/lib/klokast/updates/recovery/1/step.log')
        result = self.clean()
        self.assertTrue(note.exists() and forum.exists() and raw.exists() and recovery.exists())
        journal = [r for r in result['roots'] if r['root'].endswith('/operations-journal')][0]
        self.assertEqual((journal['rule'], journal['children'], journal['logs']), ('audit', {'cases': 2, 'forum': 1}, 1))

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

    def test_air_conversations_stay_and_tool_logs_expire(self):
        session = self.file('/home/agent/.codex/sessions/2026/10/15/rollout.jsonl')
        mcp = self.file('/home/agent/.cache/claude-cli-nodejs/-home-agent-src/mcp-logs-claude-artifacts/a.jsonl')
        npm = self.file('/home/agent/.npm/_logs/2026-10-01-debug-0.log')
        self.clean('boxa-air')
        self.assertTrue(session.exists())
        self.assertFalse(mcp.exists() or npm.exists())

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

    def test_machine_names_select_the_role(self):
        self.assertEqual(retention.role_of('k001-vpn-egress'), 'vpn-egress')
        with self.assertRaises(ValueError): retention.role_of('k001-print')


if __name__ == '__main__': unittest.main()
