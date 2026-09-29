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
HOST = ROOT / 'roles/router-candidate-preparation/files/router-candidate-prepare-dom0'


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

    def test_initial_install_seeds_first_contact_after_candidate_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'candidate'
            events=[]
            def command(argv, deadline, **kwargs):
                events.append(tuple(map(str,argv)))
            prepared={'identity_absent':True,'mode':'initial-install'}
            seed=mock.Mock(return_value={'kind':'klokast.router-first-contact.v1'})
            candidate=types.SimpleNamespace(prepare=mock.Mock(return_value=prepared))
            value={'mode':'initial-install','personalization':{'approved':True},
                   'first_contact':{'key':'ssh-ed25519 key','backend_address':'192.0.2.2',
                                    'backend_prefix':24,'backend_source_address':'192.0.2.1'}}
            with mock.patch.dict(self.prepare.__globals__, {'ROOT':root,'command':command,
                    'router_candidate':candidate,'router_initial_contact':types.SimpleNamespace(seed=seed)}):
                result, first_contact = self.prepare(value)
            self.assertEqual(result, prepared)
            self.assertEqual(first_contact, {'kind':'klokast.router-first-contact.v1'})
            candidate.prepare.assert_called_once()
            seed.assert_called_once_with(root, job=value, key='ssh-ed25519 key',
                personalization=value['personalization'], backend_address='192.0.2.2', backend_prefix=24)
            self.assertIn(('/bin/sync',), events)


class CandidatePrepareHostTests(unittest.TestCase):
    def test_failed_allocation_records_exact_absence_and_preserves_reason(self):
        module = runpy.run_path(str(HOST), run_name='router_candidate_prepare_dom0')
        operation = 'a' * 24
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            def fail_create(*args):
                (work / 'candidate-disk.json').write_text('planned')
                raise RuntimeError('LVM refused the allocation')
            disk = types.SimpleNamespace(create=mock.Mock(side_effect=fail_create), retire=mock.Mock(return_value=0))
            with mock.patch.dict(module['allocate'].__globals__, {'router_candidate_disk':disk}):
                with self.assertRaisesRegex(RuntimeError, 'LVM refused the allocation'):
                    module['allocate'](work, {'operation_id':operation,'box':'boxa'}, work / 'template',
                                       {'artifacts':{'os':{'bytes':2147483648}}})
            disk.retire.assert_called_once_with(work, operation, box='boxa')

    def test_diagnostic_xen_definition_has_only_new_disk_result_and_no_vif(self):
        module = runpy.run_path(str(HOST), run_name='router_candidate_prepare_dom0')
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            name,path=module['configuration'](work,
                {'operation_id':'a'*24,'inputs_sha256':'b'*64,'job_sha256':'c'*64},
                {'path':'/dev/vg0/routergen_'+'a'*24},'/dev/loop7',
                '12345678-1234-4234-8234-123456789abc')
            content=path.read_text()
            self.assertEqual(name,'router-candidate-prepare-'+'a'*24)
            self.assertIn("vif = []",content)
            self.assertIn('/dev/vg0/routergen_'+'a'*24,content)
            self.assertIn('phy:/dev/loop7,xvdb,w',content)
            self.assertNotIn('/dev/vg0/lv_router',content)


if __name__ == '__main__':
    unittest.main()
