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


def health_function(script, name):
    function = next(
        node for node in ast.walk(ast.parse(script))
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    scope = {"re": re}
    exec(compile(ast.Module([function], []), "health", "exec"), scope)
    return scope[name]


def health_script():
    for task in yaml.safe_load(HEALTH_TASKS.read_text(encoding="utf-8")):
        shell = task.get("ansible.builtin.shell", "")
        if "python3 - <<'PY'" in shell:
            return shell.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    raise AssertionError("dom0 health script is missing")


class Dom0HealthWorldTest(unittest.TestCase):
    def test_pre_upstream_tailscale_bundle_keeps_only_the_previous_world(self):
        script = health_script()
        ast.parse(script)
        self.assertIn("upstream_tailscale_bundle = (generation / 'tailscale').is_dir()", script)
        self.assertIn('expected_world - {"nftables"} | {"tailscale", "tailscale-openrc"}', script)
        self.assertIn('{"openresolv", "busybox-mdev-openrc"} if openresolv_bundle else set()', script)
        self.assertIn('expect(apk_world == sorted(expected_world)', script)


class Dom0HealthTaskTest(unittest.TestCase):
    def test_ansible_can_split_the_health_shell_arguments(self):
        # Ansible counts quotes in a free-form shell task, also in comments.
        try:
            from ansible.parsing.splitter import split_args
        except ImportError:
            self.skipTest("ansible is not installed")
        for task in yaml.safe_load(HEALTH_TASKS.read_text(encoding="utf-8")):
            if "ansible.builtin.shell" in task:
                split_args(task["ansible.builtin.shell"])


class Dom0HealthConsoleTest(unittest.TestCase):
    def setUp(self):
        self.findings = health_function(health_script(), "console_findings")
        self.declared = yaml.safe_load(
            (REPO_ROOT / "ansible/inventory-policy/group_vars/dom0.yml").read_text())["dom0_linux_args"]
        self.inittab = ["tty1::respawn:/sbin/getty 38400 tty1", "# enable login on alternative console",
                        "hvc0::respawn:/sbin/getty -L 0 hvc0 vt100"]

    def test_a_boot_before_the_declared_arguments_only_warns(self):
        # The k002-dom0 values before the dom0 update that applies console=tty1.
        old_boot = "modules=loop,squashfs,sd-mod,usb-storage,nvme quiet alpine_dev=LABEL=ALPINE_EFI".split()
        failures, warnings = self.findings(self.declared, old_boot, ["hvc0", "tty0"],
                                           self.inittab + ["tty0::respawn:/sbin/getty -L 0 tty0 vt100"])
        self.assertEqual(failures, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Alt+F2", warnings[0])

    def test_a_boot_with_the_declared_arguments_requires_one_getty_per_console(self):
        boot = (self.declared + " alpine_dev=LABEL=ALPINE_EFI").split()
        self.assertEqual(self.findings(self.declared, boot, ["hvc0", "tty1"], self.inittab), ([], []))
        failures, _ = self.findings(self.declared, boot, ["hvc0", "tty0"], self.inittab)
        self.assertIn("kernel consoles still include the alias tty0: ['hvc0', 'tty0']", failures)
        failures, _ = self.findings(self.declared, boot, ["hvc0", "tty1"],
                                    self.inittab + ["tty0::respawn:/sbin/getty -L 0 tty0 vt100"])
        self.assertEqual(failures, ["/etc/inittab starts a getty on tty0, which shares tty1 with the tty1 getty"])


class Dom0HealthGuestTest(unittest.TestCase):
    def test_managed_guest_size_comes_from_its_xen_definition(self):
        script = health_script()
        number = health_function(script, "xen_config_number")

        # The update engine writes `key = repr(value)` lines.
        engine = "name = 'bak'\nmemory = 4096\nmaxmem = 4096\nvcpus = 2\ncpus = '4-7'"
        self.assertEqual(number(engine, "memory"), 4096)
        self.assertEqual(number(engine, "vcpus"), 2)
        self.assertIsNone(number("name = 'bak'\nvcpus = 'two'", "vcpus"))
        self.assertIsNone(number("maxmem = 4096", "memory"))

        self.assertIn('xen_config_number(active_config, "vcpus")', script)
        self.assertIn('xen_config_number(active_config, "memory")', script)
        self.assertIn('for path in assignment.get("artifacts", {}):', script)

    def test_unmanaged_guest_boot_files_come_from_its_xen_definition(self):
        script = health_script()
        path = health_function(script, "xen_config_path")

        engine = "name = 'ops'\nkernel = '/mnt/dom0_data/ops/legacy/kernel'\nvcpus = 2"
        self.assertEqual(path(engine, "kernel"), "/mnt/dom0_data/ops/legacy/kernel")
        self.assertEqual(path('ramdisk = "/mnt/dom0_data/xen_images/x-initramfs"', "ramdisk"),
                         "/mnt/dom0_data/xen_images/x-initramfs")
        self.assertIsNone(path(engine, "ramdisk"))
        self.assertIsNone(path("kernel = 4096", "kernel"))

        self.assertIsNotNone(re.search(
            r'if not managed:\n(\s+#.*\n)*\s+for name in \("kernel", "ramdisk"\):\n'
            r'\s+path = xen_config_path\(active_config, name\)', script))
        # The inventory does not name boot files.
        self.assertNotIn("required_paths", script)

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
        # A controller without a boot assignment keeps its size and volume.
        self.assertEqual(
            sorted(specs["ops"]),
            ["autostart", "config_path", "guest_name", "lv_size", "memory_mb",
             "required_lvs", "vcpus", "vifs"],
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
