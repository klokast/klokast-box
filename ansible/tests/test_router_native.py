"""Native guards reject aliased assignments, partial backends and expired work."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_native as n
import router_generations as g
from router_transaction import TransactionError
from test_router_generations import generation


def domain(value, domid=4):
    config = n.literal_configuration(g.configuration(value))
    return {'domid': domid, 'config': {
        'c_info': {k: config[k] for k in ('name', 'uuid', 'type')},
        'b_info': {'target_memkb': config['memory'] * 1024, 'max_vcpus': config['vcpus'],
                   'kernel': config['kernel'], 'ramdisk': config['ramdisk'], 'cmdline': config['extra']},
        'disks': [{'pdev_path': value['disk']['path'], 'vdev': 'xvda', 'readwrite': 1, 'format': 'raw'}],
        'nics': [dict(devid=i, **dict(v.split('=') for v in entry.split(',')))
                 for i, entry in enumerate(config['vif'])]}}


class NativeTests(unittest.TestCase):
    def test_native_runtime_uses_devices_and_exact_boot_topology(self):
        value = generation()
        live = domain(value)
        paths = {value['disk']['path']: 1, '/dev/mapper/alias': 1, '/dev/vg0/foreign': 2}
        live['config']['disks'][0]['pdev_path'] = '/dev/mapper/alias'
        n.validate_runtime(live, value, paths.__getitem__)
        for mutate in (lambda v: v['config']['disks'][0].update(pdev_path='/dev/vg0/foreign'),
                       lambda v: v['config']['disks'][0].update(readwrite=0),
                       lambda v: v['config']['b_info'].update(kernel='/other/kernel'),
                       lambda v: v['config']['nics'][0].update(mac='00:16:3e:00:00:02'),
                       lambda v: v['config']['c_info'].update(uuid='1' * 36)):
            wrong = copy.deepcopy(live); mutate(wrong)
            with self.assertRaises(TransactionError):
                n.validate_runtime(wrong, value, paths.__getitem__)

    def test_xenstore_keeps_partial_records_for_detachment_failure(self):
        root = '/local/domain/0/backend/vbd'
        text = '\n'.join((root+'/4 = ""', root+'/4/51712 = ""',
            root+'/4/51712/params = "/dev/vg0/lv_router"', root+'/4/51712/physical-device = "fd:1"',
            root+'/5/51712 = ""'))
        parsed = n.backend_records(text)
        self.assertEqual(parsed[(4,51712)]['physical-device'], 'fd:1')
        self.assertEqual(parsed[(5,51712)], {})
        for wrong in (text+'\ntruncated', text+'\n'+root+'/4/51712/params = "other"'):
            with self.assertRaises(TransactionError):
                n.backend_records(wrong)

    def test_inventory_needs_dom0_and_unique_domain_uuids(self):
        zero = {'domid': 0, 'config': {'c_info': {'name': 'Domain-0', 'uuid': '00000000-0000-0000-0000-000000000000'}}}
        live = domain(generation())
        host = n.Native()
        with mock.patch.object(n, 'command', return_value=json.dumps([zero, live])):
            self.assertEqual(len(host.inventory(deadline=100)), 2)
        without_uuid = copy.deepcopy(zero)
        without_uuid['config']['c_info'].pop('uuid')
        with mock.patch.object(n, 'command', return_value=json.dumps([without_uuid, live])):
            self.assertEqual(len(host.inventory(deadline=100)), 2)
        for values in ([live], [zero, live, live], [], [zero, {**live, 'domid': -1}]):
            with mock.patch.object(n, 'command', return_value=json.dumps(values)), self.assertRaises(TransactionError):
                host.inventory(deadline=100)

    def test_deadline_is_passed_to_native_command_and_never_extended(self):
        with mock.patch.object(n.time, 'monotonic', return_value=100), mock.patch.object(n.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess(['xl'], 0, 'output', '')
            self.assertEqual(n.command(['xl', 'list'], 102), 'output')
            self.assertEqual(run.call_args.kwargs['timeout'], 2)
            with self.assertRaises(TransactionError):
                n.command(['xl', 'list'], 99)
            self.assertEqual(run.call_count, 1)

    def test_foreign_guest_cannot_hold_a_router_disk_through_an_alias(self):
        pair = {'old': generation('legacy'), 'candidate': generation()}
        host = n.Native()
        foreign = domain(pair['old'])
        foreign['config']['c_info'].update(name='foreign', uuid='99999999-1111-4111-8111-111111111111')
        foreign['config']['disks'][0]['pdev_path'] = '/dev/mapper/alias'
        devices = {pair['old']['disk']['path']:1, pair['candidate']['disk']['path']:2, '/dev/mapper/alias':1}
        with mock.patch.object(host, 'disk', side_effect=lambda expected, **kw: devices[expected['path']]), \
             mock.patch.object(host, 'device', side_effect=devices.__getitem__), \
             mock.patch.object(host, 'inventory', return_value=[foreign]), self.assertRaises(TransactionError):
            host.guest(pair, deadline=100)


if __name__ == '__main__':
    unittest.main()
