"""Nightly command parsing must not run the MacBook installer."""
import importlib.util
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'klokast-dev/lib'))
import platform_client as client

class NightlyClientTests(unittest.TestCase):
    def test_save_and_read(self):
        for argv,tail in [(['setup','--update-time','04:30'],['update-time','04:30']),(['status','--update-time'],['update-time'])]:
            with patch.object(client,'controller',return_value='boxa'), patch.object(client,'remote',return_value={'time':'04:30'}) as remote, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(client.main(['--instance','/fixture',*argv]),0)
                self.assertEqual(remote.call_args.args[2][1:],tail)
    def test_invalid_time_and_conflicting_target_do_not_connect(self):
        for argv in [['setup','--update-time','24:00'],['setup','--update-time','4:30'],['status','boxa-ops','--update-time']]:
            with patch.object(client,'controller') as controller,contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(client.main(argv),0)
                controller.assert_not_called()
