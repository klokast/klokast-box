"""Controller retention, interruption recovery and ownership refusal."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_ops_replacement as fixtures
import ops_retirement as retirement


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReplacementTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.m = self.f.m
        old = self.m.read(self.m.BASE / 'assignment.json')
        old.update(origin='legacy-adoption', legacy_config_sha256='a' * 64)
        self.m.write(self.m.BASE / 'assignment.json', old)
        for image in ('a' * 24, 'b' * 24, 'a' * 24): self.f.replace(image); self.f.accept()
        probe = patch.object(retirement, 'no_references'); probe.start(); self.addCleanup(probe.stop)
        self.probe = probe
        original = self.f.run_command
        def run(argv, **kwargs):
            if argv[0] == 'lvs': return SimpleNamespace(stdout=json.dumps({'report': [{'lv': [dict(lv_path=path, **row) for path, row in self.f.lvs.items()]}]}))
            if argv[0] == 'lvremove': self.f.lvs.pop(argv[-1]); return SimpleNamespace(stdout='')
            return original(argv, **kwargs)
        patcher = patch.object(self.m, 'run', side_effect=run); patcher.start(); self.addCleanup(patcher.stop)

    def test_three_generations_keep_two_and_replay_does_not_restore_references(self):
        result = retirement.retire(self.m, 'boxa', True)
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(len(self.f.lvs), 4)
        result = retirement.retire(self.m, 'boxa')
        self.assertEqual(len(result['removed_disks']), 2)
        self.assertEqual(len(self.f.lvs), 2)
        for name in ('assignment.json', 'replacement.json'):
            self.assertEqual(len(retirement.generations(self.m.read(self.m.BASE / name))), 2)
        self.f.resume()
        self.assertEqual(len(retirement.generations(self.m.read(self.m.BASE / 'assignment.json'))), 2)
        self.assertFalse(retirement.retire(self.m, 'boxa')['changed'])

    def test_every_deletion_and_record_publication_can_resume(self):
        proposal = retirement.retire(self.m, 'boxa', True)
        original_write = self.m.write
        def interrupt(path, value):
            original_write(path, value)
            if path.name == 'assignment.json': raise RuntimeError('lost publication reply')
        with patch.object(self.m, 'write', side_effect=interrupt), self.assertRaisesRegex(RuntimeError, 'publication'):
            retirement.retire(self.m, 'boxa')
        self.assertEqual(len(self.f.lvs), 2)
        self.assertEqual(retirement.retire(self.m, 'boxa')['plan_sha256'], proposal['plan_sha256'])
        self.f.resume()
        self.assertEqual(len(retirement.generations(self.f.state())), 2)

    def test_disk_removed_before_file_unlink_and_metadata_failure_resume(self):
        original = self.m.run
        def interrupt(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[0] == 'lvs': return SimpleNamespace(stdout=json.dumps({'report': [{'lv': [dict(lv_path=path, **row) for path, row in self.f.lvs.items()]}]}))
            if argv[0] == 'lvremove': raise RuntimeError('lost disk reply')
            return result
        with patch.object(self.m, 'run', side_effect=interrupt), self.assertRaises(RuntimeError):
            retirement.retire(self.m, 'boxa')
        with patch.object(retirement, 'persist', side_effect=RuntimeError('metadata')), self.assertRaisesRegex(RuntimeError, 'metadata'):
            retirement.retire(self.m, 'boxa')
        self.assertTrue(retirement.retire(self.m, 'boxa')['complete'])

    def test_reused_lv_name_and_live_reference_preserve_all_old_generations(self):
        older = retirement.generations(self.f.state())[2]
        self.f.lvs[older['root_lv']]['lv_uuid'] = 'foreign'
        result = retirement.retire(self.m, 'boxa')
        self.assertFalse(result['changed']); self.assertTrue(result['unknown_unchanged'])
        self.assertEqual(len(self.f.lvs), 4)
        self.f.lvs[older['root_lv']]['lv_uuid'] = older['lv_uuid']
        with patch.object(retirement, 'no_references', side_effect=RuntimeError('live reference')):
            self.assertFalse(retirement.retire(self.m, 'boxa')['changed'])

    def test_incomplete_replacement_retains_all_generations(self):
        state = self.f.state(); state['stage'] = 'booted'; self.m.write(self.m.BASE / 'replacement.json', state)
        result = retirement.retire(self.m, 'boxa')
        self.assertFalse(result['changed']); self.assertEqual(len(self.f.lvs), 4)

    def test_unrecorded_fixture_is_not_inferred_from_its_lv_name(self):
        work = self.m.BASE.parent / 'qualification' / ('d' * 24); work.mkdir(parents=True)
        self.m.write(work / 'request.json', dict(box='boxa', operation_id=work.name, image='a' * 24,
                    legacy_disk='/dev/vg0/opsqual_' + work.name, tag='klokast-ops-qualification-' + work.name))
        plan = retirement.retire(self.m, 'boxa', True)
        self.assertEqual(plan['fixtures'], [])
        self.assertIn('protected legacy LV UUID', plan['unknown_unchanged'][0]['reason'])

    def test_unknown_controller_lv_is_reported_and_never_removed(self):
        path = '/dev/vg0/ops_' + 'e' * 24
        self.f.lvs[path] = {'lv_uuid': 'unknown', 'lv_tags': ''}
        result = retirement.retire(self.m, 'boxa')
        self.assertIn(path, self.f.lvs)
        self.assertTrue(any(row['resource'] == path for row in result['unknown_unchanged']))

    def test_reused_name_during_interrupted_cleanup_stops_replay(self):
        original = self.m.run
        removed = []
        def interrupt(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[0] == 'lvremove':
                removed.append(argv[-1]); raise RuntimeError('lost deletion reply')
            return result
        with patch.object(self.m, 'run', side_effect=interrupt), self.assertRaises(RuntimeError):
            retirement.retire(self.m, 'boxa')
        self.f.lvs[removed[0]] = {'lv_uuid': 'reused-name', 'lv_tags': ''}
        with self.assertRaisesRegex(RuntimeError, 'different UUID'):
            retirement.retire(self.m, 'boxa')
        self.assertEqual(self.f.lvs[removed[0]]['lv_uuid'], 'reused-name')


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReplacementTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.m, self.state = self.f.m, self.f.old

    def test_live_guest_disk_and_boot_references_refuse(self):
        for guest in ({'config': {'disks': [{'pdev_path': self.state['root_lv']}]}},
                      {'config': {'kernel': self.state['work'] + '/kernel'}}):
            with self.subTest(guest=guest), patch.object(self.m, 'run', return_value=SimpleNamespace(stdout=json.dumps([{'domid': 0}, guest]))):
                with self.assertRaisesRegex(RuntimeError, 'live guest'):
                    retirement.no_references(self.m, self.state)

    def test_loop_reference_refuses(self):
        backing = self.f.root / 'backing_file'; backing.write_text(self.state['work'] + '/kernel')
        original = Path.glob
        def glob(path, pattern):
            return iter([backing]) if str(path) == '/sys/block' else original(path, pattern)
        with patch.object(self.m, 'run', return_value=SimpleNamespace(stdout='[{"domid":0}]')), patch.object(Path, 'glob', glob):
            with self.assertRaisesRegex(RuntimeError, 'loop device'):
                retirement.no_references(self.m, self.state)

    def test_mounted_filesystem_refuses(self):
        import os
        original_exists, original_stat, original_read = Path.exists, Path.stat, Path.read_text
        disk = self.state['root_lv']
        def exists(path): return True if str(path) == disk else original_exists(path)
        def stat(path, *args, **kwargs):
            return SimpleNamespace(st_rdev=os.makedev(253, 42)) if str(path) == disk else original_stat(path, *args, **kwargs)
        def read(path, *args, **kwargs):
            return '1 0 253:42 / /mounted rw - ext4 /dev/alias rw' if str(path) == '/proc/self/mountinfo' else original_read(path, *args, **kwargs)
        with patch.object(self.m, 'run', return_value=SimpleNamespace(stdout='[{"domid":0}]')), \
                patch.object(Path, 'exists', exists), patch.object(Path, 'stat', stat), patch.object(Path, 'read_text', read):
            with self.assertRaisesRegex(RuntimeError, 'mounted filesystem'):
                retirement.no_references(self.m, self.state)


if __name__ == '__main__': unittest.main()
