"""Reject incomplete or ambiguous live router boot identities."""
import copy
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'roles/router-update-inspection/files/inspect-router-update'
LOADER = importlib.machinery.SourceFileLoader('router_inspection', str(PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
INSPECT = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(INSPECT)


class XenInspectionTests(unittest.TestCase):
    def configuration(self):
        return {'name': 'router', 'type': 'pvh', 'memory': 512, 'vcpus': 1,
                'kernel': '/mnt/dom0_data/router-kernel', 'ramdisk': '/mnt/dom0_data/router-initramfs',
                'extra': 'console=hvc0 root=/dev/xvda3 rw modules=ext4',
                'disk': ['phy:/dev/vg0/lv_router,xvda,w'],
                'vif': ['bridge=br-wan,mac=00:16:3e:11:00:01'],
                'on_crash': 'destroy', 'on_reboot': 'restart'}

    @staticmethod
    def render(values):
        return '\n'.join(key + ' = ' + repr(value) for key, value in values.items())

    def test_read_literal_configuration_without_executing_code(self):
        values = self.configuration()
        self.assertEqual(INSPECT.xen_configuration(self.render(values)), values)
        for suffix in ("\nname = 'other'", '\nimport os', '\nextra += " changed"',
                       '\npci = ["01:00.0"]', '\ncpus = "0-3"'):
            with self.subTest(suffix=suffix), self.assertRaises(RuntimeError):
                INSPECT.xen_configuration(self.render(values) + suffix)
        with self.assertRaises((ValueError, RuntimeError)):
            INSPECT.xen_configuration(self.render(values).replace("name = 'router'", 'name = str(1)'))

    def test_incomplete_or_unsupported_config_cannot_be_a_baseline(self):
        for key, value in (('memory', True), ('disk', []), ('disk', ['file:/tmp/other,xvda,w']),
                           ('vif', ['bridge=br-wan,mac=00:16:3e:11:00:01,script=other']),
                           ('uuid', 'unknown'), ('on_crash', 'restart')):
            values = self.configuration()
            values[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                INSPECT.xen_configuration(self.render(values))
        values = self.configuration()
        del values['extra']
        with self.assertRaises(RuntimeError):
            INSPECT.xen_configuration(self.render(values))

    def runtime(self):
        return [{'domid': 4, 'config': {
            'c_info': {'name': 'router', 'type': 'pvh', 'uuid': '12345678-1234-1234-1234-123456789abc'},
            'b_info': {'max_vcpus': 1, 'target_memkb': 524288,
                       'kernel': '/mnt/dom0_data/router-kernel', 'ramdisk': '/mnt/dom0_data/router-initramfs',
                       'cmdline': 'console=hvc0 root=/dev/xvda3 rw modules=ext4'},
            'disks': [{'pdev_path': '/dev/vg0/lv_router', 'vdev': 'xvda', 'format': 'raw', 'readwrite': 1}],
            'nics': [{'devid': 0, 'mac': '00:16:3e:11:00:01', 'bridge': 'br-wan'}]}}]

    def test_live_uuid_and_ordered_attachments_are_independent_evidence(self):
        runtime = self.runtime()
        result = INSPECT.xen_runtime(runtime)
        for key in ('name', 'type', 'kernel', 'ramdisk', 'extra', 'disk', 'vif', 'vcpus'):
            self.assertEqual(result[key], self.configuration()[key])
        self.assertEqual(result['memory_kb'], 512 * 1024)
        for mutate in (lambda d: d.append(copy.deepcopy(d[0])),
                       lambda d: d[0]['config']['c_info'].update(uuid=''),
                       lambda d: d[0]['config']['disks'][0].update(readwrite=0),
                       lambda d: d[0]['config']['nics'][0].update(devid=1),
                       lambda d: d[0]['config']['b_info'].pop('kernel')):
            runtime = self.runtime()
            mutate(runtime)
            with self.assertRaises(RuntimeError):
                INSPECT.xen_runtime(runtime)


if __name__ == '__main__':
    unittest.main()
