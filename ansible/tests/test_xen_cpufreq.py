"""The Xen frequency driver stays limited to k002 and is saved only after Xen uses it."""
from pathlib import Path
import unittest

import yaml

PLAYBOOK = Path(__file__).resolve().parents[1] / 'playbooks/74-k002-xen-cpufreq.yml'


class XenCpufreq(unittest.TestCase):
    def test_only_k002_loads_the_driver(self):
        plays = yaml.safe_load(PLAYBOOK.read_text())
        self.assertEqual([play['hosts'] for play in plays], ['k002-dom0'])
        self.assertIn("ansible_play_hosts_all == ['k002-dom0']", plays[0]['tasks'][0]['ansible.builtin.assert']['that'])

    def test_checks_come_before_each_change_and_the_save(self):
        names = [task['name'] for task in yaml.safe_load(PLAYBOOK.read_text())[0]['tasks']]
        order = [names.index(name) for name in (
            'Require active controller authority', 'Require a dom0 without an unfinished change',
            'Load the driver at each boot', 'Load the driver now',
            'Require the Xen frequency driver before saving the configuration', 'Persist the driver selection',
            'Verify that the saved overlay contains the driver selection')]
        self.assertEqual(order, sorted(order))


if __name__ == '__main__':
    unittest.main()
