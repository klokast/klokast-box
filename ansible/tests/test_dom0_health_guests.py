#!/usr/bin/env python3
import ast
import re
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
HEALTH_TASKS = (
    REPO_ROOT / "ansible" / "roles" / "dom0-health-verification" / "tasks" / "main.yml"
)
VM_DOM0_VARS = REPO_ROOT / "ansible" / "inventory-policy" / "group_vars" / "vm_dom0.yml"
SHARED_GUEST_TASKS = REPO_ROOT / "ansible" / "roles" / "shared-xen-guests" / "tasks"


def health_script():
    for task in yaml.safe_load(HEALTH_TASKS.read_text(encoding="utf-8")):
        shell = task.get("ansible.builtin.shell", "")
        if "python3 - <<'PY'" in shell:
            return shell.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    raise AssertionError("dom0 health script is missing")


class Dom0HealthGuestTest(unittest.TestCase):
    def test_managed_guest_size_comes_from_its_xen_definition(self):
        script = health_script()
        tree = ast.parse(script)
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "xen_config_number"
        )
        scope = {"re": re}
        exec(compile(ast.Module([function], []), "health", "exec"), scope)
        number = scope["xen_config_number"]

        # The update engine writes `key = repr(value)` lines.
        engine = "name = 'bak'\nmemory = 4096\nmaxmem = 4096\nvcpus = 2\ncpus = '4-7'"
        self.assertEqual(number(engine, "memory"), 4096)
        self.assertEqual(number(engine, "vcpus"), 2)
        self.assertIsNone(number("name = 'bak'\nvcpus = 'two'", "vcpus"))
        self.assertIsNone(number("maxmem = 4096", "memory"))

        self.assertIn('xen_config_number(active_config, "vcpus")', script)
        self.assertIn('xen_config_number(active_config, "memory")', script)
        self.assertIn('for path in assignment.get("artifacts", {}):', script)
        self.assertIsNotNone(re.search(r"if not managed:\n\s+for path in required_paths:", script))

    def test_inventory_does_not_declare_what_the_update_system_owns(self):
        specs = yaml.safe_load(VM_DOM0_VARS.read_text(encoding="utf-8"))["node_xen_guest_specs"]

        for key in ("backend", "dmz", "iot"):
            self.assertEqual(
                sorted(specs[key]), ["autostart", "config_path", "guest_name", "vifs"], key
            )
            self.assertTrue(all(sorted(vif) == ["bridge"] for vif in specs[key]["vifs"]), key)
        # The first creation of the router reads its size and interfaces.
        self.assertEqual(
            sorted(specs["router"]),
            ["autostart", "config_path", "guest_name", "memory_mb", "vcpus", "vifs"],
        )

    def test_shared_guest_runtime_requires_a_boot_assignment(self):
        inspect = (SHARED_GUEST_TASKS / "inspect-one.yml").read_text(encoding="utf-8")
        apply = (SHARED_GUEST_TASKS / "apply-one.yml").read_text(encoding="utf-8")

        self.assertIn("- vm_boot_assignment.managed", inspect)
        self.assertIn("platform-update provision", inspect)
        for text in (inspect, apply):
            self.assertNotIn("name: xen-guest", text)
        self.assertIn("reconcile-assignment", apply)


if __name__ == "__main__":
    unittest.main()
