"""Native activity and wrapper decisions must preserve active work."""
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import air_image_workspace as image
import codex_workspace as workspace

class NativeActivity(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        for name,value in (('ROOT',Path(temporary.name)),):
            mock=patch.object(image.handoff,name,value);mock.start();self.addCleanup(mock.stop)
        mock=patch.object(image.handoff,'codex_binary',return_value='/test/codex');mock.start();self.addCleanup(mock.stop)

    def test_active_approval_and_error_threads_block_shutdown(self):
        for status in ('active','systemError','notLoaded'):
            rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
            with patch.object(image.subprocess,'check_output',return_value='{"status":"running"}'), \
                    patch.object(image,'LiveRPC',return_value=rpc),patch.object(workspace,'loaded',return_value=[{'id':'t','status':{'type':status}}]):
                with self.assertRaisesRegex(RuntimeError,'active or needs attention'):image.inspect()
    def test_background_jobs_block_shutdown(self):
        rpc=Mock();rpc.__enter__=Mock(return_value=rpc);rpc.__exit__=Mock(return_value=False)
        rpc.call.return_value={'data':[{'id':'job'}]}
        with patch.object(image.subprocess,'check_output',return_value='{"status":"running"}'), \
                patch.object(image,'LiveRPC',return_value=rpc),patch.object(workspace,'loaded',return_value=[{'id':'t','updatedAt':10,'status':{'type':'idle'}}]):
            with self.assertRaisesRegex(RuntimeError,'background terminals'):image.inspect()
    def test_no_daemon_needs_no_rpc(self):
        with patch.object(image.subprocess,'check_output',return_value='{"status":"stopped"}'),patch.object(image,'LiveRPC') as rpc:
            self.assertEqual(image.inspect()['threads'],[]);rpc.assert_not_called()
    def test_activity_after_fencing_never_stops_daemon(self):
        with tempfile.TemporaryDirectory() as d,patch.object(image.handoff,'ROOT',Path(d)), \
                patch.object(image,'inspect',side_effect=[{'threads':['t']},RuntimeError('new turn')]), \
                patch.object(workspace,'inventory',return_value={'sessions':[],'discussions':['t']}), \
                patch.object(workspace,'frozen_tuis',return_value=contextlib.nullcontext()),patch.object(image.subprocess,'run') as stop:
            with self.assertRaisesRegex(RuntimeError,'new turn'):image.checkpoint('a'*24)
            stop.assert_not_called()
    def test_empty_tmux_server_can_be_saved_without_codex(self):
        with patch.object(workspace,'tmux',return_value=Mock(returncode=1,stderr='no server running',stdout='')),patch.object(workspace,'LiveRPC') as rpc:
            value=workspace.inventory('a'*24,same_host=True,persist=False)
            self.assertEqual(value['sessions'],[]);rpc.assert_not_called()
    def test_checkpoint_does_not_close_unrepresented_discussions(self):
        with tempfile.TemporaryDirectory() as d,patch.object(image.handoff,'ROOT',Path(d)), \
                patch.object(image,'inspect',return_value={'threads':['external']}), \
                patch.object(workspace,'inventory',return_value={'discussions':[]}),patch.object(image.subprocess,'run') as stop:
            with self.assertRaisesRegex(RuntimeError,'outside'):image.checkpoint('a'*24)
            stop.assert_not_called()

class MacCommands(unittest.TestCase):
    def setUp(self):
        import importlib.util
        root=Path(__file__).resolve().parents[2]
        sys.path.insert(0,str(root/'klokast-dev/lib'))
        spec=importlib.util.spec_from_file_location('air_update_mac',root/'klokast-dev/lib/platform_client.py')
        self.client=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.client)
    def test_rejects_other_roles_before_connecting(self):
        import argparse
        args=argparse.Namespace(instance='/fixture',target='boxa-ops',resume=None,status=False)
        with patch.object(self.client.air,'declarations',return_value=('tailnet',['boxa'])),patch.object(self.client,'controller') as controller:
            with self.assertRaisesRegex(ValueError,'declared BOX-air'):self.client.image_update(args)
            controller.assert_not_called()
    def test_resume_reuses_controller_operation(self):
        import argparse
        args=argparse.Namespace(instance='/fixture',target='boxa-air',resume='b'*24,status=False)
        with patch.object(self.client.air,'declarations',return_value=('tailnet',['boxa'])), \
                patch.object(self.client,'controller',return_value='boxb'),patch.object(self.client,'remote',return_value={'state':'waiting'}) as remote:
            self.client.image_update(args)
        self.assertEqual(remote.call_args.args[0:2],('smith','boxb-ops'))
        self.assertEqual(remote.call_args.args[2][-2:],['--operation','b'*24])
    def test_status_does_not_start_a_job(self):
        import argparse
        args=argparse.Namespace(instance='/fixture',target='boxa-air',resume=None,status=True)
        with patch.object(self.client.air,'declarations',return_value=('tailnet',['boxa'])), \
                patch.object(self.client,'controller',return_value='boxb'),patch.object(self.client,'remote',return_value={}) as remote:
            self.client.image_update(args)
        self.assertEqual(remote.call_args.args[2][1],'status')

class ControllerJobs(unittest.TestCase):
    def setUp(self):
        import air_image_update
        self.job=air_image_update
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        for obj,name,kwargs in ((self.job.cycle,'ROOT',{'new':Path(temporary.name)}),
                (self.job.cycle.source,'require_controller',{'return_value':None}),
                (self.job.cycle.source,'snapshot',{'return_value':{'instance':{'airunners':['boxa-air','boxb-air']}}})):
            m=patch.object(obj,name,**kwargs);m.start();self.addCleanup(m.stop)
    def test_target_mismatch_cannot_overwrite_an_operation(self):
        with patch.object(self.job.subprocess,'Popen'):
            self.job.start('boxa','a'*24)
            with self.assertRaisesRegex(RuntimeError,'another AIR target'):self.job.start('boxb','a'*24)
    def test_completed_job_is_not_relaunched(self):
        directory=self.job.cycle.ROOT/('a'*24);directory.mkdir()
        (directory/'image-job.json').write_text(json.dumps({'state':'complete','box':'boxa','created_at':1}))
        with patch.object(self.job.subprocess,'Popen') as launch:
            self.assertEqual(self.job.start('boxa','a'*24)['state'],'complete');launch.assert_not_called()
    def test_versions_compare_numerically_and_reject_unverified_channels(self):
        self.assertGreater(self.job.version('0.170.0'),self.job.version('0.99.9'))
        with self.assertRaisesRegex(RuntimeError,'unsupported'):self.job.version('0.170.0-alpha')
