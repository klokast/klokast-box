#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "ansible" / "roles" / "podman-host"


class PodmanHostRoleTest(unittest.TestCase):
    def test_convergence_does_not_download_or_run_a_test_image_by_default(self):
        defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text(encoding="utf-8"))
        tasks = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text(encoding="utf-8"))
        egress = yaml.safe_load((REPO_ROOT / "ansible/roles/vm-egress-verification/tasks/main.yml").read_text(encoding="utf-8"))
        self.assertIs(defaults["podman_host_container_probe"], False)
        optional = [task for task in tasks if task.get("name") == "Run the optional Podman container test with exact cleanup"]
        self.assertEqual(len(optional), 1)
        self.assertEqual(optional[0]["when"], "podman_host_container_probe | bool")
        self.assertTrue(any("podman pull" in str(task.get("ansible.builtin.command", "")) for task in optional[0]["block"]))
        self.assertTrue(any("test image" in task["name"] for task in optional[0]["always"]))
        self.assertFalse(any("podman" in str(task.get("ansible.builtin.command", "")) and
                             "run" in str(task.get("ansible.builtin.command", "")) for task in egress))

    def test_runroot_cleanup_service_is_enabled_in_boot_runlevel(self):
        tasks = (ROLE / "tasks" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("klokast-podman-runroot-cleanup", tasks)
        self.assertIn("/sbin/rc-update", tasks)
        self.assertIn("- boot", tasks)

    def test_convergence_refuses_partial_stale_runroot_removal(self):
        tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
        stale = [task for task in tasks if task.get('name') ==
                 'Refuse in-place cleanup of stale rootless Podman boot state']
        self.assertEqual(len(stale), 1)
        self.assertIn('ansible.builtin.fail', stale[0])
        self.assertIn('current system boot ID differs from cached boot ID', stale[0]['when'])
        self.assertFalse(any('/tmp/storage-run-' in str(task.get('ansible.builtin.file', {}))
                             and task.get('ansible.builtin.file', {}).get('state') == 'absent'
                             for task in tasks))

    def test_runroot_cleanup_service_only_removes_transient_runroot(self):
        template = (
            ROLE / "templates" / "klokast-podman-runroot-cleanup.init.j2"
        ).read_text(encoding="utf-8")
        self.assertIn('runroot="/tmp/storage-run-${uid}"', template)
        self.assertIn('rm -rf -- "${runroot}"', template)
        self.assertNotIn("/srv", template)
        self.assertNotIn(".local/share/containers/storage", template)

    def test_boot_cleanup_keeps_an_unsafe_runroot_and_clears_a_safe_one(self):
        template = (ROLE / 'templates/klokast-podman-runroot-cleanup.init.j2').read_text()
        rendered = template.replace('{{ podman_host_runner_user | quote }}', 'neo')
        rendered = rendered.replace('{{ podman_host_runner_user }}', 'neo')
        self.assertEqual(subprocess.run(['sh', '-n'], input=rendered, text=True).returncode, 0)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / 'openrc-service'
            script.write_text(rendered)
            stub = base / 'bin'
            stub.mkdir()
            root = base / 'storage-run'

            def run(*, pgrep=1, mode=0o700, mounted=False, missing_pgrep=False,
                    linked=False):
                target = base / 'linked-target'
                if linked:
                    target.mkdir()
                    root.symlink_to(target, target_is_directory=True)
                else:
                    root.mkdir(mode=mode)
                    root.chmod(mode)
                (root / 'stale').write_text('transient')
                probe = stub / 'pgrep'
                probe.write_text('#!/bin/sh\nexit ' + str(pgrep) + '\n')
                probe.chmod(0o755)
                mount_probe = stub / 'awk'
                if mounted:
                    mount_probe.write_text('#!/bin/sh\nprintf "%s\\n" mounted\n')
                    mount_probe.chmod(0o755)
                else:
                    mount_probe.unlink(missing_ok=True)
                path = str(stub) if missing_pgrep else str(stub) + ':' + os.environ['PATH']
                if missing_pgrep:
                    probe.unlink()
                result = subprocess.run(
                    ['/bin/sh', '-c', 'eend() { return "$1"; }; . "$1"; uid="$2"; gid="$3"; runroot="$4"; clear_runroot',
                     'sh', str(script), str(os.getuid()), str(os.getgid()), str(root)],
                    env={**os.environ, 'PATH': path}, capture_output=True, text=True)
                exists = root.exists() or root.is_symlink()
                stale_exists = (root / 'stale').exists()
                if linked:
                    root.unlink()
                    (target / 'stale').unlink()
                    target.rmdir()
                elif exists:
                    (root / 'stale').unlink()
                    root.rmdir()
                return result.returncode, exists, stale_exists

            for state in ({'pgrep': 0}, {'pgrep': 2}, {'mode': 0o755},
                          {'mounted': True}, {'missing_pgrep': True}, {'linked': True}):
                with self.subTest(state=state):
                    self.assertEqual(run(**state), (1, True, True))
            self.assertEqual(run(), (0, False, False))

    def test_runroot_cleanup_service_prepares_tun_device(self):
        template = (
            ROLE / "templates" / "klokast-podman-runroot-cleanup.init.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("/sbin/modprobe tun", template)
        self.assertIn("mknod /dev/net/tun c 10 200", template)
        self.assertIn("chgrp netdev /dev/net/tun", template)
        self.assertIn("chmod 0666 /dev/net/tun", template)


if __name__ == "__main__":
    unittest.main()
