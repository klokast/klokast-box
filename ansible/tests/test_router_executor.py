"""A dead worker cannot leave native children running while recovery starts."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_executor as e
from router_transaction import TransactionError
import test_router_dom0 as dom0_tests


class WorkerTests(unittest.TestCase):
    def test_dead_worker_children_are_stopped_before_its_pid_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'child.pid'
            code=('import subprocess,sys; from pathlib import Path; '
                  'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                  'Path(sys.argv[1]).write_text(str(p.pid)); sys.exit(3)')
            process=subprocess.Popen([sys.executable,'-c',code,str(path)],start_new_session=True)
            self.assertEqual(e.wait_worker(process,5),3)
            identity=int(path.read_text())
            limit=time.monotonic()+2
            while time.monotonic()<limit:
                status=Path('/proc')/str(identity)/'stat'
                if not status.exists() or status.read_text().rsplit(')',1)[1].split()[0]=='Z':
                    break
                time.sleep(.01)
            else:
                self.fail('native child remains running after worker reaping')

    def test_hung_worker_is_stopped_at_its_fixed_deadline(self):
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
        started=time.monotonic()
        self.assertEqual(e.wait_worker(process,.15),-signal.SIGKILL)
        self.assertLess(time.monotonic()-started,2)


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        dom0_tests.Dom0Tests.setUp(self)

    def test_worker_death_runs_durable_recovery_without_replaying_cutover(self):
        pending={**self.pending,'phase':'awaiting-acceptance','candidate_started':True}
        self.records.persist(pending)
        self.host.live='candidate'
        with mock.patch.object(e.subprocess,'Popen'),mock.patch.object(e,'wait_worker',return_value=-9), \
             mock.patch.object(e,'adapter',return_value=self.adapter):
            self.assertEqual(e.supervise(self.records,self.request['operation_id'],self.request['engine_commit']),'rolled-back')
        self.assertIn(('candidate','old'),self.copy.events)
        self.assertEqual(self.host.live,'old')
        self.assertIsNone(self.records.pending())

    def test_preflight_worker_failure_does_not_guess_a_recovery_generation(self):
        with mock.patch.object(e.subprocess,'Popen'),mock.patch.object(e,'wait_worker',return_value=1), \
             mock.patch.object(e,'adapter') as adapter,self.assertRaises(TransactionError):
            e.supervise(self.records,self.request['operation_id'],self.request['engine_commit'])
        adapter.assert_not_called()
        self.assertEqual(self.host.live,'old')

    def test_map_projects_generations_without_private_receipt_contents(self):
        import json
        initial=e.map_status(self.records)
        self.assertEqual(initial['current']['generation_id'],self.old['generation_id'])
        self.assertIsNone(initial['previous'])
        self.assertIsNone(initial['state_copy'])
        self.records.persist(self.pending)
        directory=self.adapter.work/'copy'
        directory.mkdir(exist_ok=True)
        for name in ('forward.result.slot','forward.private.slot'):
            (directory/name).write_text('synthetic private content')
        self.copy.verify_receipt=mock.Mock()
        with mock.patch.object(e,'adapter',return_value=self.adapter):
            value=e.map_status(self.records)
        self.assertEqual(value['pending']['operation_id'],self.request['operation_id'])
        self.assertEqual(value['state_copy']['forward'],'complete')
        self.assertEqual(value['state_copy']['reverse'],'absent')
        self.assertNotIn('synthetic private',json.dumps(value))
        self.copy.verify_receipt.side_effect=TransactionError('synthetic private parse error')
        with mock.patch.object(e,'adapter',return_value=self.adapter):
            value=e.map_status(self.records)
        self.assertEqual(value['state_copy']['forward'],'unverified')
        self.assertNotIn('parse error',json.dumps(value))

    def test_map_refuses_changed_pointers_instead_of_joining_two_operations(self):
        with mock.patch.object(self.records,'pending',side_effect=[None,self.pending]):
            with self.assertRaisesRegex(TransactionError,'pointers changed'):
                e.map_status(self.records)

    def test_map_rejects_pending_operation_for_another_accepted_pointer(self):
        self.records.persist(self.pending)
        with mock.patch.object(self.records,'committed',side_effect=TransactionError('assignment differs')):
            with self.assertRaisesRegex(TransactionError,'assignment differs'):
                e.map_status(self.records)


if __name__ == '__main__':
    unittest.main()
