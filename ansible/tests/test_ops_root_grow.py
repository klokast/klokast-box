import copy
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'roles/ops-controller/files/ops-root-grow'
loader = importlib.machinery.SourceFileLoader('ops_root_grow', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
grow = importlib.util.module_from_spec(spec)
loader.exec_module(grow)


class RootGrowthTests(unittest.TestCase):
    def setUp(self):
        self.table = {'label': 'dos', 'device': '/dev/xvda', 'unit': 'sectors', 'partitions': [
            {'node': '/dev/xvda1', 'type': '83', 'start': 2048, 'size': 524288},
            {'node': '/dev/xvda2', 'type': '83', 'start': 526336, 'size': 2048},
            {'node': '/dev/xvda3', 'type': '83', 'start': 528384, 'size': 104329216},
        ]}

    def test_old_root_on_larger_disk_and_repeated_growth(self):
        before = copy.deepcopy(self.table)
        self.assertEqual(grow.validate(self.table, grow.MIN_SECTORS)['start'], 528384)
        self.assertEqual(before, self.table)
        self.table['partitions'][2]['size'] = grow.MIN_SECTORS - 528384
        self.assertEqual(grow.validate(self.table, grow.MIN_SECTORS)['size'], 209186816)
        self.assertIsNotNone(grow.validate(self.table, grow.MIN_SECTORS * 2))

    def test_stale_guest_disk_size_refuses_before_partition_change(self):
        with self.assertRaisesRegex(ValueError, 'has not reported'):
            grow.validate(self.table, 104857600)

    def test_refuse_unknown_or_overlapping_layout(self):
        for change in ('label', 'device', 'extra', 'overlap', 'type', 'oversize'):
            value = copy.deepcopy(self.table)
            if change == 'label': value['label'] = 'gpt'
            if change == 'device': value['device'] = '/dev/xvdb'
            if change == 'extra': value['partitions'].append({})
            if change == 'overlap': value['partitions'][2]['start'] = 2048
            if change == 'type': value['partitions'][2]['type'] = '82'
            if change == 'oversize': value['partitions'][2]['size'] = grow.MIN_SECTORS
            with self.subTest(change=change), self.assertRaises(ValueError):
                grow.validate(value, grow.MIN_SECTORS)
