"""The runner closure protects target work without requiring a spare runner."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import air_maintenance as maintenance


class MaintenanceTest(unittest.TestCase):
    def test_idle_single_runner_can_close_for_its_replacement(self):
        with patch.object(maintenance,'remote',return_value={'operation':'a'*24,'owner':'air'}) as remote, \
                patch.object(maintenance.subprocess,'run',side_effect=AssertionError('unexpected peer probe')):
            self.assertEqual(maintenance.before_stop('boxa','a'*24,'air'),{'operation':'a'*24,'owner':'air'})
        remote.assert_called_once_with('boxa','image-begin',operation='a'*24,owner='air')

    def test_final_activity_check_still_blocks_shutdown(self):
        with patch.object(maintenance,'remote',side_effect=RuntimeError('runner busy')) as remote:
            with self.assertRaisesRegex(RuntimeError,'runner busy'):
                maintenance.before_stop('boxa','a'*24,'dom0')
        remote.assert_called_once_with('boxa','image-begin',operation='a'*24,owner='dom0')

    def test_reopen_releases_only_a_closure_of_the_ended_owner(self):
        closed={'operation':'a'*24,'owner':'air'}
        with patch.object(maintenance,'release',return_value={'cleared':True,'workspace':'restored'}) as release, \
                patch.object(maintenance,'progress') as progress:
            self.assertIsNone(maintenance.reopen('boxa','air',closed))
            release.assert_called_once_with('boxa','a'*24)
            release.reset_mock()
            # A closure of another owner stays; its own journal decides about it.
            self.assertEqual(maintenance.reopen('boxa','dom0',closed),closed)
            self.assertIsNone(maintenance.reopen('boxa','air',None))
            # A closure of an older helper names no owner and stays for its recorded operation.
            legacy={'operation':'a'*24,'phase':'pending'}
            self.assertEqual(maintenance.reopen('boxa','air',legacy),legacy)
            release.assert_not_called()
        self.assertIn('closing operation '+'a'*24+' has ended',progress.call_args_list[0].args[0])

    def test_reopen_reports_a_workspace_that_did_not_return(self):
        with patch.object(maintenance,'release',return_value={'cleared':True,'workspace':'not restored: checkpoint missing'}), \
                patch.object(maintenance,'progress') as progress:
            maintenance.reopen('boxa','dom0',{'operation':'a'*24,'owner':'dom0'})
        self.assertIn('not restored: checkpoint missing',progress.call_args_list[-1].args[0])

    def test_runner_operations_are_controller_operation_ids(self):
        with self.assertRaisesRegex(RuntimeError,'invalid runner operation ID'):
            maintenance.remote('boxa','complete',operation='YOUR-ID')


if __name__=='__main__':unittest.main()
