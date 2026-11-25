"""The one-time cpufreq trial keeps the accepted boot entry first and can be removed exactly."""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / 'ansible/roles/dom0-update/files'
PLAYBOOK = ROOT / 'ansible/playbooks/74-dom0-cpufreq-trial.yml'


def load(name):
    spec = importlib.util.spec_from_file_location(name, FILES / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class CpufreqTrial(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.mount = Path(directory.name) / 'efi'
        self.base = Path(directory.name) / 'base'; self.base.mkdir()
        (self.mount / 'boot/grub/x86_64-efi').mkdir(parents=True)
        for name in ('loadenv.mod', 'test.mod', 'multiboot2.mod'):
            (self.mount / 'boot/grub/x86_64-efi' / name).touch()
        self.trial, self.native = load('dom0_cpufreq_trial'), load('dom0_update')
        self.generation = self.mount / 'klokast-dom0' / ('a' * 24)
        accepted = self.native.grub(self.generation, self.mount, '')
        self.config = accepted + self.native.recovery_entry(accepted, 'c' * 24, self.mount)
        self.path = self.mount / 'boot/grub/grub.cfg'
        self.path.write_text(self.config)
        self.native.grubenv(self.mount)
        self.empty = (self.mount / 'boot/grub/grubenv').read_bytes()
        (self.base / 'accepted.json').write_text(json.dumps(
            {'box': 'boxa', 'generation': str(self.generation)}))
        commands = self.commands = []

        def run(args, timeout=120):
            commands.append([str(value) for value in args])
            return 'xen_commandline        : loglvl=all' if args[0] == 'xl' else ''
        for name, value in {'BASE': self.base, 'run': run}.items():
            patcher = patch.object(self.native, name, value); patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(self.native, 'media', lambda writable=False: self.mount)
        patcher.start(); self.addCleanup(patcher.stop)

    def environment(self):
        return (self.mount / 'boot/grub/grubenv').read_bytes()

    def test_arm_appends_one_trial_and_keeps_the_existing_text(self):
        self.trial.arm(self.native, 'boxa')
        config = self.path.read_text()
        self.assertTrue(config.startswith(self.config))
        # rearm() of the next dom0 update still accepts this file.
        self.assertTrue(config.startswith(self.native.grub(self.generation, self.mount, '')))
        entries = re.findall(r'(?m)^menuentry "([^"]+)"', config)
        self.assertEqual([entry.split(':')[0] for entry in entries],
                         ['Alpine Linux (Xen, from RAM)', 'Recovery', 'Trial'])
        self.assertEqual(config.count('cpufreq=hwp'), 1)
        trial = config[config.index('menuentry "Trial: '):]
        self.assertEqual(trial.replace('"Trial: ', '"').replace(' cpufreq=hwp', '').replace(self.trial.END, ''),
                         self.native.first_entry(self.config))
        self.assertRegex(trial, r'(?m)^\s*multiboot2 .* cpufreq=hwp$')
        self.assertEqual(config.index('set default=0'), self.config.index('set default=0'))
        self.assertTrue(self.native.trial_pending(self.mount))
        self.assertEqual(len(self.environment()), 1024)
        self.assertEqual((self.base / 'cpufreq-trial/grub.cfg').read_text(), self.config)
        self.assertEqual(self.commands[-2][:3], ['mount', '-o', 'remount,ro'])
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()),
                         ['grub.cfg', 'grubenv', 'x86_64-efi'])

    def test_grub_selects_the_trial_only_after_it_read_back_a_cleared_flag(self):
        lines = [line.strip() for line in self.trial.block(self.native, self.config).splitlines()]
        order = ['insmod loadenv', 'load_env --file ${prefix}/grubenv next_entry',
                 'if [ "x${next_entry}" = "xtrial" ]; then', 'save_env --file ${prefix}/grubenv next_entry',
                 'set next_entry=unknown', 'if [ "x${next_entry}" = "x" ]; then', 'set default=2']
        positions = [lines.index(line) for line in order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(lines.count('set default=2'), 1)
        unknown, default = lines.index('set next_entry=unknown'), lines.index('set default=2')
        self.assertIn('load_env --file ${prefix}/grubenv next_entry', lines[unknown:default])
        self.assertEqual(lines[lines.index('set next_entry=unknown') - 2], 'set next_entry=')

    def test_clear_restores_both_files_exactly_before_and_after_the_boot(self):
        for after_boot in (False, True):
            with self.subTest(after_boot=after_boot):
                self.trial.arm(self.native, 'boxa')
                if after_boot:
                    (self.mount / 'boot/grub/grubenv').write_bytes(
                        self.environment().replace(b'next_entry=trial\n', b'next_entry=\n#####'))
                self.trial.clear(self.native, 'boxa')
                self.assertEqual(self.path.read_text(), self.config)
                self.assertEqual(self.environment(), self.empty)

    def test_clear_after_an_interrupted_arm(self):
        self.trial.arm(self.native, 'boxa')
        self.native.grubenv(self.mount)
        self.trial.clear(self.native, 'boxa')
        self.trial.clear(self.native, 'boxa')
        self.assertEqual(self.path.read_text(), self.config)

    def test_clear_refuses_a_changed_grub_file(self):
        self.trial.arm(self.native, 'boxa')
        self.path.write_text(self.path.read_text() + '# changed\n')
        armed = self.environment()
        with self.assertRaisesRegex(RuntimeError, 'neither'):
            self.trial.clear(self.native, 'boxa')
        self.assertEqual(self.environment(), armed)

    def refused(self, message):
        before = self.path.read_bytes(), self.environment()
        with self.assertRaisesRegex(RuntimeError, message):
            self.trial.arm(self.native, 'boxa')
        self.assertEqual((self.path.read_bytes(), self.environment()), before)
        self.assertFalse((self.base / 'cpufreq-trial').exists())

    def test_arm_refuses_a_pending_transaction(self):
        (self.base / 'pending.json').write_text('{}')
        self.refused('pending')

    def test_arm_refuses_another_default_or_a_third_entry(self):
        self.path.write_text('# other\n' + self.config)
        self.refused('accepted dom0 generation')
        self.path.write_text(self.config + self.native.first_entry(self.config))
        self.refused('exactly')

    def test_arm_refuses_a_used_environment_and_a_missing_module(self):
        self.native.grubenv(self.mount, [('next_entry', 'trial')])
        self.refused('grubenv is not empty')
        self.native.grubenv(self.mount)
        (self.mount / 'boot/grub/x86_64-efi/test.mod').unlink()
        self.refused('test.mod')

    def test_a_failed_write_returns_the_boot_partition_to_read_only(self):
        with patch.object(self.native, 'grubenv', side_effect=OSError('full')), self.assertRaises(OSError):
            self.trial.arm(self.native, 'boxa')
        self.assertEqual(self.commands[-1][:3], ['mount', '-o', 'remount,ro'])
        self.assertFalse(self.native.trial_pending(self.mount))

    def test_playbook_has_one_option_one_box_and_active_authority(self):
        play = yaml.safe_load(PLAYBOOK.read_text())[0]
        self.assertIn('--require-active', play['pre_tasks'][0]['ansible.builtin.command'])
        checks = play['pre_tasks'][1]['ansible.builtin.assert']['that']
        self.assertIn('ansible_play_hosts_all | length == 1', checks)
        self.assertIn("cpufreq_trial_action in ['arm', 'clear', 'status']", checks)
        self.assertEqual(self.trial.OPTION, 'cpufreq=hwp')
        self.assertNotIn('/sbin/reboot', PLAYBOOK.read_text() + (FILES / 'dom0_cpufreq_trial.py').read_text())


if __name__ == '__main__':
    unittest.main()
