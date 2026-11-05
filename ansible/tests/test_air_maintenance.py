"""Stop gates protect target work without requiring a spare runner."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import air_maintenance as maintenance


class MaintenanceTest(unittest.TestCase):
    def test_idle_single_runner_can_begin_image_replacement(self):
        with patch.object(maintenance,'remote',return_value={'phase':'pending'}) as remote, \
                patch.object(maintenance.subprocess,'run',side_effect=AssertionError('unexpected peer probe')):
            self.assertEqual(maintenance.before_stop('boxa','a'*24,image=True),{'phase':'pending'})
        remote.assert_called_once_with('boxa','image-begin',operation='a'*24,reservation=None)

    def test_final_activity_check_still_blocks_shutdown(self):
        with patch.object(maintenance,'remote',side_effect=RuntimeError('runner busy')) as remote:
            with self.assertRaisesRegex(RuntimeError,'runner busy'):
                maintenance.before_stop('boxa','a'*24,image=True)
        remote.assert_called_once_with('boxa','image-begin',operation='a'*24,reservation=None)

    def test_reservation_and_legacy_activity_gate_remain(self):
        with patch.object(maintenance,'remote',return_value={'phase':'pending'}) as remote:
            maintenance.before_stop('boxa','a'*24,'planned')
        remote.assert_called_once_with('boxa','begin',operation='a'*24,reservation='planned')


if __name__=='__main__':unittest.main()
