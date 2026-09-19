"""Legacy diagnostic cleanup must stay bounded to the reviewed paths."""
import json
import unittest
from pathlib import Path

import yaml


PLAY = Path(__file__).resolve().parents[1] / 'playbooks/74-platform-update-legacy-temp-cleanup.yml'


class LegacyTempCleanup(unittest.TestCase):
    def test_fixed_no_application_targets_and_file_lists(self):
        play = yaml.safe_load(PLAY.read_text())[0]
        self.assertEqual(play['hosts'], 'k001-dmz:k002-dmz:k002-iot')
        paths = play['vars']['legacy_temp_paths_by_host']
        self.assertEqual(set(paths), {'k001-dmz', 'k002-dmz', 'k002-iot'})
        self.assertEqual(len(paths['k001-dmz']), 9)
        self.assertEqual(len(paths['k002-dmz']), 3)
        self.assertEqual(len(paths['k002-iot']), 3)
        self.assertTrue(all(p.startswith('/tmp/') and p.count('/') == 2
                            for host_paths in paths.values() for p in host_paths))
        self.assertEqual(len({p for host_paths in paths.values() for p in host_paths}), 10)

    def test_apply_requires_checked_intent_preview_and_no_process_use(self):
        play = yaml.safe_load(PLAY.read_text())[0]
        source = json.dumps(play)
        for required in ('intent.eligible', 'intent.workloads', 'intent.datasets',
                         'legacy_temp_preview.content', 'item.stat.checksum',
                         'item.stat.inode', 'item.rc == 1', '/bin/busybox', 'fuser'):
            self.assertIn(required, source)
        removals = [task for task in play['tasks'] if 'Remove only' in task['name']]
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0]['ansible.builtin.command']['argv'],
                         ['/bin/rm', '--', '{{ item }}'])
        self.assertNotIn('recurse', source)


if __name__ == '__main__':
    unittest.main()
