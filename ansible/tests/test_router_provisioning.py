"""Keep preparation inactive and preserve other Alpine asset consumers."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

try:
    from jinja2 import Environment
except ImportError:
    Environment = None
import yaml

REPO = Path(__file__).resolve().parents[2]
ROLES = REPO / 'ansible/roles'


class PreparationTests(unittest.TestCase):
    @unittest.skipIf(Environment is None, 'Jinja is supplied by the controller Ansible toolchain')
    def test_render_does_not_queue_handlers_or_change_live_sysctls(self):
        tasks = yaml.safe_load((ROLES / 'router/tasks/render.yml').read_text())
        env = Environment()
        for task in tasks:
            modules = {k for k in task if '.' in k}
            self.assertTrue(modules <= {'ansible.builtin.copy', 'ansible.builtin.template',
                                       'ansible.builtin.file', 'ansible.builtin.stat', 'ansible.posix.sysctl'})
            if 'notify' in task:
                self.assertEqual(env.from_string(task['notify']).render(router_configuration_phase='render'), '[]')
            if 'ansible.posix.sysctl' in task:
                self.assertEqual(env.from_string(task['ansible.posix.sysctl']['reload']).render(router_configuration_phase='render'), 'False')
                self.assertFalse(task['ansible.posix.sysctl'].get('sysctl_set', False))

    def test_every_legacy_mutator_checks_router_assignment_first(self):
        for role in ('router', 'router-alpine-rootfs', 'vm-base', 'tailscale-client', 'vm-tailscale-enrollment', 'xen-guest'):
            with self.subTest(role=role):
                tasks = yaml.safe_load((ROLES / role / 'tasks/main.yml').read_text())
                self.assertEqual(tasks[0]['ansible.builtin.include_role']['name'], 'router-boot-assignment')
        for name in ('30-vm-router-alpine-build.yml', '31-vm-router.yml'):
            play = yaml.safe_load((REPO / 'ansible/playbooks' / name).read_text())[0]
            self.assertTrue(play['any_errors_fatal'])
            self.assertEqual(play['pre_tasks'][0]['ansible.builtin.import_role']['name'], 'router-boot-assignment')

    @unittest.skipIf(Environment is None, 'Jinja is supplied by the controller Ansible toolchain')
    def test_asset_paths_preserve_shared_defaults_and_allow_router_namespace(self):
        defaults = yaml.safe_load((ROLES / 'alpine-virt-assets/defaults/main.yml').read_text())
        env = Environment()
        shared = {'dom0_xen_images_path': '/mnt/dom0_data/xen',
                  'xen_shared_installer_kernel_path': '/mnt/dom0_data/xen/kernel',
                  'xen_shared_installer_initramfs_path': '/mnt/dom0_data/xen/initramfs',
                  'xen_shared_installer_iso_path': '/mnt/dom0_data/xen/installer.iso'}
        for key, expected in [('alpine_virt_kernel_path', shared['xen_shared_installer_kernel_path']),
                              ('alpine_virt_initramfs_path', shared['xen_shared_installer_initramfs_path']),
                              ('alpine_virt_iso_link_path', shared['xen_shared_installer_iso_path'])]:
            self.assertEqual(env.from_string(defaults[key]).render(**shared), expected)
        for prefix in ('/mnt/dom0_data/xen', '/mnt/dom0_data/router/aaaaaaaa'):
            self.assertEqual(env.from_string(defaults['alpine_virt_modloop_path']).render(alpine_virt_asset_directory=prefix), prefix + '/modloop-virt')
            self.assertEqual(env.from_string(defaults['alpine_virt_package_repo_path']).render(alpine_virt_asset_directory=prefix), prefix + '/alpine-virt-apks')


class LockTests(unittest.TestCase):
    def test_concurrent_installation_refuses_and_nested_shell_reuses_lock(self):
        # Replace only filesystem metadata policy in this unprivileged fixture;
        # exercise the real kernel flock and inherited descriptors.
        source = (REPO / 'ansible/lib/platform-installation-lock.sh').read_text()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'operation.lock').touch(mode=0o660)
            (root / 'operation.lock').chmod(0o660)
            root.chmod(0o755)
            source = source.replace('/var/lib/klokast/updates', str(root))
            source = source.replace("'0:755'", "'" + str(os.getuid()) + ":755'")
            source = source.replace('"0:$(id -g):660:1"', '"' + str(os.getuid()) + ':$(id -g):660:1"')
            script = root / 'lock.sh'
            script.write_text(source)
            env = {k:v for k,v in os.environ.items() if k != 'KLOKAST_INSTALLATION_LOCK_FD'}
            with subprocess.Popen(['sh', '-c', '. "$1"; platform_installation_lock || exit; echo locked; read finish', 'sh', str(script)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) as held:
                self.assertEqual(held.stdout.readline().strip(), 'locked')
                second = subprocess.run(['sh', '-c', '. "$1"; platform_installation_lock', 'sh', str(script)],
                                        capture_output=True, text=True, env=env, timeout=5)
                self.assertNotEqual(second.returncode, 0)
                self.assertIn('Another VM update', second.stderr)
                held.communicate('done\n', timeout=5)
            nested = subprocess.run(['sh', '-c', '. "$1"; platform_installation_lock || exit; sh -c \' . "$1"; platform_installation_lock \' sh "$1"',
                                     'sh', str(script)], capture_output=True, text=True, env=env, timeout=5)
            self.assertEqual(nested.returncode, 0, nested.stderr)
            forged = subprocess.run(['sh', '-c', '. "$1"; platform_installation_lock', 'sh', str(script)],
                                    capture_output=True, text=True, env={**env, 'KLOKAST_INSTALLATION_LOCK_FD': '9'}, timeout=5)
            self.assertNotEqual(forged.returncode, 0)
            self.assertIn('does not identify', forged.stderr)


if __name__ == '__main__':
    unittest.main()
