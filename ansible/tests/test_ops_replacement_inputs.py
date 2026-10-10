"""Frozen inputs survive source changes; accepted images do not allocate disks."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import test_ops_replacement as fixtures
import ops_replacement_inputs as inputs


class InputsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repos = {}
        for name in ('public', 'instance'):
            repo = self.base / name; repo.mkdir()
            subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
            for key, value in (('user.email', 'test@example.invalid'), ('user.name', 'Test')):
                inputs.run(['git', '-C', repo, 'config', key, value])
            (repo / 'input').write_text('first')
            inputs.run(['git', '-C', repo, 'add', 'input'])
            inputs.run(['git', '-C', repo, 'commit', '-qm', 'first'])
            inputs.run(['git', '-C', repo, 'remote', 'add', 'origin', repo])
            self.repos[name] = repo
        for name, value in (('ROOT', self.base / 'snapshots'), ('INSTANCE', self.repos['instance'])):
            p = patch.object(inputs, name, value); p.start(); self.addCleanup(p.stop)
        original = inputs.run
        def run(argv):
            if argv[0] == '/usr/local/bin/klokast':
                return json.dumps({'valid': True, 'projection': {'inventory': {
                    'all': {'children': ['ops']}, 'ops': {'hosts': ['boxa-ops']},
                    '_meta': {'hostvars': {'boxa-ops': {'node_name': 'boxa'}}}}}})
            return original(argv)
        p = patch.object(inputs, 'run', side_effect=run); p.start(); self.addCleanup(p.stop)

    def test_resume_uses_original_commits_and_inventory_after_both_sources_advance(self):
        config = {'box': 'boxa', 'engine_commit': inputs.revision(self.repos['public']),
                  'instance_commit': inputs.revision(self.repos['instance'])}
        variables = {'ops_replace_configuration': config, 'ops_replace_image': 'a' * 24}
        work, checksum = inputs.freeze(self.repos['public'], variables)
        record = {'requested_configuration': config, 'image': 'a' * 24, 'inputs_sha256': checksum}
        for repo in self.repos.values():
            (repo / 'input').write_text('second')
            inputs.run(['git', '-C', repo, 'commit', '-qam', 'second'])
        self.assertEqual(inputs.recover(record)[0], work)
        self.assertEqual((work / 'public/input').read_text(), 'first')
        self.assertEqual((work / 'instance/input').read_text(), 'first')
        (work / 'inventory/hosts.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'inventory snapshot changed'): inputs.recover(record)

    def test_missing_and_changed_snapshot_refuse_unfinished_resume(self):
        with self.assertRaisesRegex(RuntimeError, 'no verified source snapshot'):
            inputs.recover({'requested_configuration': {}})
        config = {'engine_commit': inputs.revision(self.repos['public']),
                  'instance_commit': inputs.revision(self.repos['instance'])}
        work, checksum = inputs.freeze(self.repos['public'], {'ops_replace_configuration': config, 'ops_replace_image': 'a' * 24})
        record = {'requested_configuration': config, 'image': 'a' * 24, 'inputs_sha256': checksum}
        (work / 'public/input').write_text('tampered')
        with self.assertRaisesRegex(RuntimeError, 'uncommitted changes'): inputs.recover(record)

    def test_unapproved_or_dirty_source_refuses_before_snapshot(self):
        (self.repos['public'] / 'input').write_text('dirty')
        with self.assertRaisesRegex(RuntimeError, 'uncommitted'):
            inputs.revision(self.repos['public'], True)


class AcceptedTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReplacementTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.replace(); self.f.accept()

    def test_configuration_changes_report_convergence_without_disk_or_restart(self):
        before = list(self.f.events)
        config = json.loads(self.f.config.read_text()); config.update(engine_commit='e' * 40, instance_commit='f' * 40)
        self.f.config.write_text(json.dumps(config))
        result = self.f.replace()
        self.assertFalse(result['changed']); self.assertTrue(result['configuration_update_needed'])
        self.assertEqual(self.f.events, before); self.assertEqual(len(self.f.lvs), 2)

    def test_convergence_record_survives_accepted_operation_replay(self):
        value = {'engine_commit': 'e' * 40, 'instance_commit': 'f' * 40}
        self.f.config.write_text(json.dumps(value))
        self.assertTrue(self.f.m.record_convergence('boxa', self.f.config)['changed'])
        self.f.resume()
        self.assertEqual(self.f.m.read(self.f.m.BASE / 'assignment.json')['convergence'], value)
        self.assertFalse(self.f.m.record_convergence('boxa', self.f.config)['changed'])

    def test_step_mode_stops_before_each_destructive_transition(self):
        f = fixtures.ReplacementTests(); f.setUp()
        try:
            stages = []
            for _ in range(5):
                stages.append(f.m.replace('boxa', 'a' * 24, f.config, f.receipt, one_step=True)['stage'])
            self.assertEqual(stages, ['copied', 'stopped', 'finalized', 'boot-requested', 'booted'])
        finally: f.doCleanups()


if __name__ == '__main__': unittest.main()
