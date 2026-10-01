"""Audit real first-install dispatch with local ops and service guests absent.

Native receipts are synthetic. This source proof checks target and delegation
dependencies; it does not replace native installation and recovery proof.
"""
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml
import test_router_initial_cli as fixtures

try:
    from jinja2 import Environment, StrictUndefined
except ImportError:
    Environment = None

try:
    from ansible.inventory.manager import InventoryManager
    from ansible.parsing.dataloader import DataLoader
except ImportError:
    InventoryManager = None

REPO = Path(__file__).resolve().parents[2]


@unittest.skipIf(Environment is None, 'Jinja is supplied by the controller Ansible toolchain')
class PreOpsAbsenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InitialCliTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.cli = self.fixture.cli
        self.available = {'boxa-dom0', 'boxa-router', 'localhost'}
        self.groups = {'dom0': ['boxa-dom0'], 'vm_dom0': ['boxa-dom0'],
                       'router': ['boxa-router']}
        self.context = {'node_name': 'boxa', 'router_initial_box': 'boxa'}
        self.env = Environment(undefined=StrictUndefined)
        self.plays, self.roles, self.targets = set(), set(), set()
        self.inventory = self.fixture.root / 'preops-inventory.yml'
        self.inventory.write_text(yaml.safe_dump({'all': {'children': {
            name: {'hosts': dict.fromkeys(hosts, {})} for name, hosts in self.groups.items()}}}))

    def tasks(self, values, directory):
        for task in values:
            if 'delegate_to' in task:
                target = self.env.from_string(task['delegate_to']).render(**self.context)
                self.assertIn(target, self.available, task['name'])
                self.targets.add(target)
            for key in ('block', 'rescue', 'always'):
                self.tasks(task.get(key, []), directory)
            for key in ('ansible.builtin.import_role', 'ansible.builtin.include_role'):
                if key in task:
                    role = task[key]['name']
                    self.assertNotIn('{{', role)
                    if role not in self.roles:
                        self.roles.add(role)
                        path = REPO / 'ansible/roles' / role / 'tasks/main.yml'
                        self.tasks(yaml.safe_load(path.read_text()), path.parent)
            for key in ('ansible.builtin.import_tasks', 'ansible.builtin.include_tasks'):
                if key in task:
                    name = task[key]
                    self.assertIsInstance(name, str)
                    self.assertNotIn('{{', name)
                    path = directory / name
                    self.tasks(yaml.safe_load(path.read_text()), path.parent)

    def audit_playbook(self, path, limit=None):
        self.plays.add(path)
        for play in yaml.safe_load(path.read_text()):
            selected = self.groups.get(play['hosts'], [])
            self.assertTrue(selected, 'play requires an absent guest: ' + play['hosts'])
            if limit is not None:
                self.assertIn(limit, selected, 'limit selects no first-install host')
            self.targets.update(selected if limit is None else [limit])
            roles = [{'ansible.builtin.import_role': {'name': role if isinstance(role, str)
                      else role['role']}} for role in play.get('roles', [])]
            for key in ('pre_tasks', 'tasks', 'post_tasks', 'handlers'):
                self.tasks(play.get(key, []), path.parent)
            self.tasks(roles, path.parent)

    def command(self, argv, **kwargs):
        args = [str(value) for value in argv]
        if args[0] == 'ansible-playbook':
            path = next(Path(arg) for arg in args if arg.endswith('.yml'))
            self.assertIn('--limit', args)
            self.audit_playbook(path, args[args.index('--limit') + 1])
        else:
            self.assertIn(Path(args[0]).name, ('git', 'platform-resources'))
        return self.fixture.command(argv, **kwargs)

    def install(self):
        with patch.object(self.cli.transport, 'command', side_effect=self.command):
            self.fixture.prepare()
            self.cli.start_initial('boxa', self.fixture.operation)
            key_dir = self.fixture.root / '.ssh'
            key_dir.mkdir(mode=0o700)
            for name in ('github-klokast-codex', 'known_hosts'):
                path = key_dir / name
                path.write_text('synthetic controller key\n')
                path.chmod(0o600)
            with patch.object(Path, 'home', return_value=self.fixture.root):
                self.cli.enroll_initial('boxa', self.fixture.operation)
            with patch.object(self.cli.vm_template_inputs, 'bootstrap',
                    side_effect=lambda source, output, guest, **kwargs:
                        self.fixture.stage_boot(source, output.parent)):
                self.cli.finalize_initial('boxa', self.fixture.operation)
            self.cli.boot_final_initial('boxa', self.fixture.operation)
            self.cli.verify_initial('boxa', self.fixture.operation)
            accepted = self.cli.accept_initial('boxa', self.fixture.operation)
        self.assertEqual(accepted['status'], 'accepted')
        for name in ('30-vm-router-alpine-build.yml', '31-vm-router.yml',
                     '74-router-recovery-setup.yml'):
            self.audit_playbook(REPO / 'ansible/playbooks' / name)
        self.assertEqual(self.targets, self.available)
        self.assertGreaterEqual(len(self.plays), 18)

    def test_first_install_accepts_without_local_ops_or_service_guests(self):
        self.install()

    @unittest.skipIf(InventoryManager is None, 'Ansible inventory parser is on the controller')
    def test_real_inventory_selects_every_dispatched_play_without_dependent_guests(self):
        self.install()
        inventory = InventoryManager(loader=DataLoader(), sources=[str(self.inventory)])
        self.assertEqual(set(inventory.hosts), {'boxa-dom0', 'boxa-router'})
        for path in self.plays:
            for play in yaml.safe_load(path.read_text()):
                hosts = inventory.get_hosts(pattern=play['hosts'])
                self.assertTrue(hosts, str(path))
                self.assertLessEqual({host.name for host in hosts}, self.available)

    def test_fixture_rejects_a_new_dependency_on_an_absent_ops_guest(self):
        with self.assertRaises(AssertionError):
            self.tasks([{'name': 'new dependency', 'delegate_to': 'boxa-ops'}], REPO)


if __name__ == '__main__':
    unittest.main()
