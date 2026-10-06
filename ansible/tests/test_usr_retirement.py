import importlib.util
import io
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('retire_shared_usr', Path(__file__).resolve().parents[1] / 'lib/retire_shared_usr.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class UsrRetirementTest(unittest.TestCase):
    def volume(self, name='lv_podman_usr', origin=''):
        return {'lv_path': '/dev/vg0/' + name, 'lv_name': name, 'origin': origin}

    def test_absence_and_per_user_guests_require_no_action(self):
        config = {'/etc/xen/usr-alice.cfg': {'name': 'usr-alice', 'disk': ['phy:/dev/vg0/lv_user_shell_usr_alice,xvda,w']}}
        plan = MOD.retirement_plan('boxa', config, [self.volume('lv_user_shell_usr_alice')], ['usr-alice'], [])
        self.assertEqual(plan, {'domains': [], 'disks': [], 'files': []})

    def test_exclusively_owned_disks_and_exact_files_are_selected(self):
        config = {'/etc/xen/usr.cfg': {'name': 'usr', 'disk': ['phy:/dev/vg0/lv_podman_usr,xvda,w', 'phy:/dev/vg0/lv_usr_data,xvdb,w']}}
        files = ['/etc/xen/usr.cfg', '/etc/xen/auto/usr.cfg', '/etc/xen/usr-alice.cfg']
        plan = MOD.retirement_plan('boxa', config, [self.volume(), self.volume('lv_usr_data')], ['usr', 'usr-alice'], files)
        self.assertEqual(plan['domains'], ['usr'])
        self.assertEqual(plan['disks'], ['/dev/vg0/lv_podman_usr', '/dev/vg0/lv_usr_data'])
        self.assertNotIn('/etc/xen/usr-alice.cfg', plan['files'])

    def test_shared_disk_and_snapshot_are_refused(self):
        for other in ('guest', 'snapshot'):
            configs = {'/etc/xen/usr.cfg': {'name': 'usr'}}
            volumes = [self.volume()]
            if other == 'guest':
                configs['/etc/xen/alice.cfg'] = {'disk': ['phy:/dev/mapper/vg0-lv_podman_usr,xvda,w']}
            else:
                volumes.append(self.volume('saved', 'lv_podman_usr'))
            with self.subTest(other=other), self.assertRaises(ValueError):
                MOD.retirement_plan('boxa', configs, volumes, [], [])

    def test_unknown_disk_or_boot_file_is_refused(self):
        for field, value in (('disk', ['phy:/dev/vg0/lv_podman_template,xvda,w']), ('kernel', '/mnt/dom0_data/shared-kernel')):
            config = {'/etc/xen/usr.cfg': {'name': 'usr', field: value}}
            with self.subTest(field=field), self.assertRaises(ValueError):
                MOD.retirement_plan('boxa', config, [self.volume('lv_podman_template')], [], [])

    def test_literal_parser_never_executes_configuration(self):
        with self.assertRaises(ValueError):
            MOD.configuration("name = 'usr'\ndisk = dangerous()\n")
        with self.assertRaises(ValueError):
            MOD.configuration("name = 'usr'\nname = 'usr-alice'\n")
        with self.assertRaises(ValueError):
            MOD.configuration("name = 'usr-alice'\ndisk = []\ndisk.append('phy:/dev/vg0/lv_usr,xvda,w')\n")

    def test_running_fixed_guest_requires_ownership_evidence(self):
        with self.assertRaises(ValueError):
            MOD.retirement_plan('boxa', {}, [], ['usr'], [])

    def test_apply_removes_only_selected_resources_and_second_run_is_empty(self):
        initial = {'domains': [], 'disks': ['/dev/vg0/lv_usr'], 'files': ['/etc/xen/usr.cfg']}
        empty = {'domains': [], 'disks': [], 'files': []}
        with patch('sys.argv', ['retire', '--box', 'boxa', '--apply']), \
                patch.object(MOD, 'inspect', side_effect=[initial, initial, initial, empty]), \
                patch.object(MOD, 'command') as command, patch.object(Path, 'unlink') as unlink, \
                redirect_stdout(io.StringIO()):
            MOD.main()
            MOD.main()
        command.assert_called_once_with(['lvremove', '--yes', '/dev/vg0/lv_usr'])
        unlink.assert_called_once_with()

    def test_shutdown_timeout_preserves_disks(self):
        plan = {'domains': ['usr'], 'disks': ['/dev/vg0/lv_usr'], 'files': []}
        with patch('sys.argv', ['retire', '--box', 'boxa', '--apply']), \
                patch.object(MOD, 'inspect', return_value=plan), \
                patch.object(MOD, 'command') as command, \
                patch.object(MOD.time, 'monotonic', side_effect=[0, 61]):
            with self.assertRaises(ValueError):
                MOD.main()
        command.assert_called_once_with(['xl', 'shutdown', 'usr'])


if __name__ == '__main__':
    unittest.main()
