"""Candidate preparation must release every guest mount on failure."""
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
GUEST = ROOT / 'roles/router-candidate-preparation/files/router-candidate-prepare-guest'


class CandidatePrepareGuestTests(unittest.TestCase):
    def setUp(self):
        module = runpy.run_path(str(GUEST), run_name='router_candidate_prepare_guest')
        self.prepare = module['prepare']
        self.unique = module['unique']

    def test_duplicate_job_fields_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            self.unique([('box','boxa'),('box','boxb')])

    def test_failed_preparation_unmounts_all_bound_paths_and_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'candidate'
            events=[]
            def command(argv, deadline, **kwargs):
                events.append(tuple(map(str,argv)))
            candidate=types.SimpleNamespace(prepare=mock.Mock(side_effect=ValueError('fixture failure')))
            with mock.patch.dict(self.prepare.__globals__, {'ROOT':root,'command':command,
                                                               'router_candidate':candidate}):
                with self.assertRaisesRegex(ValueError,'fixture failure'):
                    self.prepare({'mode':'replacement'})
            self.assertEqual([event[0:2] for event in events[-4:]],
                [('/bin/umount',str(root/'dev')),('/bin/umount',str(root/'sys')),
                 ('/bin/umount',str(root/'proc')),('/bin/umount',str(root))])


if __name__ == '__main__':
    unittest.main()
