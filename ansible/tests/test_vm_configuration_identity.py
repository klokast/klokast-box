"""The configuration identity covers exactly what the post-boot playbooks apply."""
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ansible/lib'))
import vm_configuration_identity as identity  # noqa: E402

ROLE_KEYS = ('import_role', 'include_role', 'ansible.builtin.import_role', 'ansible.builtin.include_role')
TASK_KEYS = ('import_tasks', 'include_tasks', 'ansible.builtin.import_tasks', 'ansible.builtin.include_tasks')
REFERENCE = re.compile(r"playbook_dir\s*(?:\}\}|~\s*')\s*(/(?:\{\{\s*item\s*\}\}|[^'\"\s{])*)(\{\{)?")


class Closure:
    """Follow role, task-file and playbook_dir references as Ansible would."""

    def __init__(self):
        self.roles, self.files = set(), set()

    def strings(self, value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from self.strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from self.strings(item)

    def reference(self, text, loop):
        for match in REFERENCE.finditer(text):
            path = re.sub(r'\{\{\s*item\s*\}\}', '{{ item }}', match.group(1))
            if match.group(2):
                raise AssertionError('a playbook_dir reference uses an expression: ' + text)
            if '{{ item }}' in path:
                if not isinstance(loop, list) or not all(isinstance(v, str) for v in loop):
                    raise AssertionError('a playbook_dir reference needs a static loop: ' + text)
                candidates = [path.replace('{{ item }}', value) for value in loop]
            else:
                candidates = [path]
            for candidate in candidates:
                resolved = (ROOT / 'ansible/playbooks' / candidate.lstrip('/')).resolve()
                if resolved == ROOT:
                    continue  # The controller checkout; controller-checkout-update changes it in place.
                if not resolved.is_file():
                    raise AssertionError('a playbook_dir reference names no file: ' + text)
                self.files.add(str(resolved.relative_to(ROOT)))

    def tasks(self, items, base):
        for task in items or []:
            if not isinstance(task, dict):
                continue
            for key in ('block', 'rescue', 'always'):
                self.tasks(task.get(key), base)
            for key in ROLE_KEYS:
                if key in task:
                    self.role(task[key]['name'])
            for key in TASK_KEYS:
                if key in task:
                    value = task[key] if isinstance(task[key], str) else task[key]['file']
                    self.task_file((base / value).resolve())
            for text in self.strings(task):
                self.reference(text, task.get('loop'))

    def task_file(self, path):
        name = str(path.relative_to(ROOT))
        if name not in self.files:
            self.files.add(name)
            self.tasks(yaml.safe_load(path.read_text()), path.parent)

    def role(self, name):
        if name in self.roles:
            return
        self.roles.add(name)
        directory = ROOT / 'ansible/roles' / name
        meta = directory / 'meta/main.yml'
        if meta.exists():
            for dependency in (yaml.safe_load(meta.read_text()) or {}).get('dependencies') or []:
                self.role(dependency if isinstance(dependency, str) else dependency.get('role') or dependency['name'])
        for path in sorted(directory.rglob('*.yml')):
            if path.parent.name in ('tasks', 'handlers'):
                self.task_file(path)
        for path in sorted(directory.rglob('*')):
            if path.is_file() and path.suffix in ('.j2', '.sh', '.py', '') and '__pycache__' not in path.parts:
                for text in path.read_text(errors='replace').splitlines():
                    self.reference(text, None)

    def playbook(self, name, group):
        path = ROOT / 'ansible/playbooks' / name
        self.files.add(str(path.relative_to(ROOT)))
        for play in yaml.safe_load(path.read_text()):
            if group and group not in str(play.get('hosts')).split(':'):
                continue
            for role in play.get('roles') or []:
                self.role(role if isinstance(role, str) else role.get('role') or role['name'])
            for key in ('pre_tasks', 'tasks', 'post_tasks', 'handlers'):
                self.tasks(play.get(key), path.parent)


class RecipeCompleteness(unittest.TestCase):
    maxDiff = None

    def test_each_recipe_lists_exactly_the_roles_and_files_that_its_playbooks_use(self):
        for name, spec in identity.RECIPES.items():
            with self.subTest(recipe=name):
                closure = Closure()
                for playbook, group in spec['playbooks'].items():
                    closure.playbook(playbook, group)
                self.assertEqual(sorted(closure.roles), sorted(spec['roles']))
                outside = {f for f in closure.files
                           if not any(f.startswith('ansible/roles/' + role + '/') for role in spec['roles'])}
                listed = set(spec['files']) | {'ansible/playbooks/' + p for p in spec['playbooks']}
                self.assertEqual(sorted(outside), sorted(listed))

    def test_each_update_role_has_a_recipe(self):
        from vm_lifecycle_roles import PROFILES
        self.assertEqual(set(PROFILES), set(identity.RECIPE_OF))

    def test_runner_workspace_helper_is_part_of_the_runner_image_recipe(self):
        # A change to this helper once reached no runner. The image owns it now, so
        # the image recipe must change, and no post-boot recipe may also claim it.
        import json
        import infrastructure_images
        profile = json.loads((ROOT / 'ansible/update-profiles/air-alpine-v1.json').read_text())
        qualified = infrastructure_images.qualify_profile(profile, repo=ROOT)
        original = Path.read_bytes
        def read(path):
            return original(path) + (b'\n# changed\n' if path.name == 'air_workspace.py' else b'')
        with patch.object(Path, 'read_bytes', read):
            altered = infrastructure_images.qualify_profile(profile, repo=ROOT)
        self.assertNotEqual(altered['recipe_sha256'], qualified['recipe_sha256'])
        for role in ('air', 'dmz', 'ops'):
            self.assertNotIn('ansible/lib/air_workspace.py', identity.recipe(role))
        self.assertNotIn('ansible/roles/air-workspace/tasks/main.yml', identity.recipe('ops'))


class RecipeDigest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        spec = identity.RECIPES['vpn-egress']
        for name in ['ansible/playbooks/' + p for p in spec['playbooks']] + ['ansible/roles/' + r for r in spec['roles']]:
            source, target = ROOT / name, self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            (shutil.copytree if source.is_dir() else shutil.copyfile)(source, target)

    def test_a_role_file_change_changes_the_recipe_and_a_missing_file_is_refused(self):
        before = identity.recipe('vpn-egress', repo=self.repo)
        task = self.repo / 'ansible/roles/vpn-egress-runtime/tasks/main.yml'
        task.write_text(task.read_text() + '\n# changed\n')
        self.assertNotEqual(identity.recipe('vpn-egress', repo=self.repo), before)
        shutil.rmtree(self.repo / 'ansible/roles/vm-dns')
        with self.assertRaisesRegex(RuntimeError, 'missing role: vm-dns'):
            identity.recipe('vpn-egress', repo=self.repo)

    def test_python_caches_are_not_configuration(self):
        before = identity.recipe('vpn-egress', repo=self.repo)
        cache = self.repo / 'ansible/roles/vm-dns/__pycache__'
        cache.mkdir()
        (cache / 'x.pyc').write_bytes(b'cache')
        self.assertEqual(identity.recipe('vpn-egress', repo=self.repo), before)


class HostInventory(unittest.TestCase):
    GRAPH = {'_meta': {'hostvars': {'boxa-dmz': {'node_name': 'boxa'}, 'boxa-air': {'node_name': 'other'}}},
             'all': {'children': ['dmz', 'air', 'boxa'], 'vars': {'platform_magicdns_suffix': 'example'}},
             'dmz': {'children': ['boxa_dmz']}, 'air': {'children': ['boxa_air']},
             'boxa': {'children': ['boxa_dmz', 'boxa_air']},
             'boxa_dmz': {'hosts': ['boxa-dmz']}, 'boxa_air': {'hosts': ['boxa-air']}}

    def test_only_the_groups_of_the_host_and_their_policy_files_are_selected(self):
        value = identity.host_inventory('boxa-dmz', self.GRAPH)
        self.assertEqual(value['hostvars'], {'node_name': 'boxa'})
        self.assertEqual(sorted(value['groups']), ['all', 'boxa', 'boxa_dmz', 'dmz'])
        self.assertEqual(sorted(value['group_vars']), ['all', 'dmz'])
        self.assertEqual(value['groups']['all'], {'platform_magicdns_suffix': 'example'})

    def test_an_undeclared_host_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'does not declare host boxa-iot'):
            identity.host_inventory('boxa-iot', self.GRAPH)


class Compute(unittest.TestCase):
    def test_declared_values_and_inventory_change_the_identity(self):
        with patch.object(identity, 'instance_inventory', return_value=HostInventory.GRAPH), \
                patch.object(identity, 'declared', return_value={'runtime': 'a'}) as declared:
            first = identity.compute('boxa', 'dmz')
            self.assertEqual(identity.compute('boxa', 'dmz'), first)
            declared.return_value = {'runtime': 'b'}
            self.assertNotEqual(identity.compute('boxa', 'dmz'), first)
        graph = dict(HostInventory.GRAPH, _meta={'hostvars': {'boxa-dmz': {'node_name': 'changed'}}})
        with patch.object(identity, 'instance_inventory', return_value=graph), \
                patch.object(identity, 'declared', return_value={'runtime': 'a'}):
            self.assertNotEqual(identity.compute('boxa', 'dmz'), first)

    def test_components_name_what_changed_without_a_value(self):
        with patch.object(identity, 'instance_inventory', return_value=HostInventory.GRAPH), \
                patch.object(identity, 'declared', return_value={'runtime': 'private'}) as declared:
            sha, first = identity.describe('boxa', 'dmz')
            self.assertEqual(sha, identity.compute('boxa', 'dmz'))
            declared.return_value = {'runtime': 'other'}
            second = identity.describe('boxa', 'dmz')[1]
        self.assertEqual([name for name in first if first[name] != second[name]], ['declared role values'])
        self.assertTrue({'role vm-dns', 'group policy dmz', 'Instance values',
                         'ansible/playbooks/69-dmz-configure.yml'} <= set(first))
        self.assertNotIn('private', str(first))

    def test_the_identity_of_an_installed_root_stays_the_same(self):
        # A changed formula would replace each VM root once, without a real change.
        with patch.object(identity, 'instance_inventory', return_value=HostInventory.GRAPH), \
                patch.object(identity, 'declared', return_value={'runtime': 'a'}):
            self.assertEqual(identity.compute('boxa', 'dmz'), identity.digest({
                'kind': 'klokast.vm-configuration-identity.v1', 'box': 'boxa', 'role': 'dmz',
                'recipe': identity.recipe('dmz'),
                'inventory': identity.host_inventory('boxa-dmz', HostInventory.GRAPH), 'declared': {'runtime': 'a'}}))

    def test_vpn_gateway_uses_its_existing_intent_signature(self):
        with patch.object(identity.platform_source, 'snapshot', return_value={'rendered': {'projection': {'registry': {}}}}), \
                patch.object(identity.model, 'load_topology', return_value={}), \
                patch.object(identity.model, 'registry_box_configs', return_value={'boxa': {}}), \
                patch('vpn_egress_provisioning.configuration_signature', return_value='s' * 64):
            self.assertEqual(identity.declared('boxa', 'vpn-egress'), {'vpn': 's' * 64})


if __name__ == '__main__':
    unittest.main()
