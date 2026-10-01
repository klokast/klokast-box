"""Daily pointer release requires verified cleanup and a durable exact archive."""
from pathlib import Path
import unittest
from unittest import mock

import test_router_daily_cutover as cutover_tests


class DailyCleanupTests(unittest.TestCase):
    launch = cutover_tests.DailyCutoverTests.launch
    saved = cutover_tests.DailyCutoverTests.saved
    completion = cutover_tests.DailyCutoverTests.completion

    def setUp(self):
        cutover_tests.DailyCutoverTests.setUp(self)
        self.result = {'kind': 'klokast.router-cleanup-result.v1', 'box': 'k001',
            'operation_id': 'e' * 24, 'status': 'exact-resources-retired',
            'cleanup': {'record_sha256': 'f' * 64}, 'evidence_directory': str(self.state / ('e' * 24))}
        self.cleanup = self.stack.enter_context(mock.patch.object(self.module, 'cleanup_replacement_locked',
            return_value=self.result))

    def test_release_happens_only_after_cleanup_and_exact_archive(self):
        self.module.daily_cutover()
        result = self.module.cleanup_daily()
        self.assertEqual(result['status'], 'reconciled')
        self.assertFalse(self.pointer.exists())
        archive = self.module.transport.load(self.state / ('e' * 24) / 'daily-preparation-complete.json')
        self.assertEqual(archive['results']['cleanup'], self.result)
        self.assertEqual((archive['phase'], archive['status']), ('cleanup-completed', 'reconciled'))
        self.cleanup.assert_called_once_with('k001', 'e' * 24)

    def test_native_cleanup_failure_keeps_the_barrier_and_never_archives(self):
        self.module.daily_cutover()
        saved = self.saved()
        self.cleanup.side_effect = self.module.UpdateError('native cleanup incomplete')
        with self.assertRaisesRegex(self.module.UpdateError, 'cleanup incomplete'):
            self.module.cleanup_daily()
        self.assertEqual(self.saved(), saved)
        self.assertFalse((self.state / ('e' * 24) / 'daily-preparation-complete.json').exists())
        with self.assertRaises(self.module.UpdateError):
            self.module.daily_cutover()
        self.launcher.assert_called_once()

    def test_process_loss_after_archive_rechecks_cleanup_before_releasing_pointer(self):
        self.module.daily_cutover()
        unlink = Path.unlink
        def interrupted(path, *args, **kwargs):
            if path == self.pointer:
                raise KeyboardInterrupt
            return unlink(path, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', interrupted), self.assertRaises(KeyboardInterrupt):
            self.module.cleanup_daily()
        self.assertEqual(self.saved()['status'], 'reconciled')
        self.assertTrue((self.state / ('e' * 24) / 'daily-preparation-complete.json').exists())
        self.assertEqual(self.module.cleanup_daily()['status'], 'reconciled')
        self.assertEqual(self.cleanup.call_count, 2)
        self.assertFalse(self.pointer.exists())
        self.launcher.assert_called_once()

    def test_rollback_requires_explicit_cleanup_and_reports_the_failed_outcome(self):
        self.outcome = 'rolled-back'
        with self.assertRaises(self.module.UpdateError):
            self.module.daily_cutover()
        self.assertEqual(self.saved()['status'], 'rolled-back-needs-cleanup')
        self.assertEqual(self.module.cleanup_daily()['outcome'], 'rolled-back')
        self.assertFalse(self.pointer.exists())
        self.launcher.assert_called_once()


if __name__ == '__main__':
    unittest.main()
