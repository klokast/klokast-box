"""Run provisioning entrypoints with local commands and an isolated lock."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
BOOTSTRAP_PHASES = [10, 11, 12, 13, 14, 20, 21, 22]

# These commands never contact a host. Capture inputs while temporary files
# exist, so tests can check permissions as well as the actual Ansible argv.
COMMAND = r'''
import json
import os
from pathlib import Path
import sys

kind = Path(sys.argv[0]).name
args = sys.argv[1:]
record = {"kind": kind, "argv": args}
phase = None
if kind == "ansible-playbook":
    playbook = next(arg for arg in args if arg.startswith(os.environ["KLOKAST_TEST_PLAYBOOKS"] + "/"))
    phase = int(Path(playbook).name.split("-", 1)[0])
    record["phase"] = phase
    connection_file = next(Path(arg[1:]) for arg in args if arg.endswith("/controller-connection-vars.json"))
    connection = json.loads(connection_file.read_text())
    run_dir = connection_file.parent
    record["run_dir"] = str(run_dir)
    record["connection"] = connection
    record["inventory"] = (run_dir / "inventory.yml").read_text()
    record["modes"] = {
        "run_dir": run_dir.stat().st_mode & 0o777,
        "connection": connection_file.stat().st_mode & 0o777,
        **{key: Path(value).stat().st_mode & 0o777 for key, value in connection.items()},
    }
with open(os.environ["KLOKAST_TEST_RECORDS"], "a") as out:
    out.write(json.dumps(record) + "\n")
if kind == "tailscale":
    if args != ["status", "--json"]:
        sys.exit("unexpected Tailscale operation in local test")
    if os.environ.get("KLOKAST_TEST_TAILSCALE_FAIL"):
        sys.exit(2)
    print(os.environ.get("KLOKAST_TEST_TAILSCALE_STATUS", "{}"))
elif kind == "ansible-playbook":
    print(f"mock phase {phase} stdout")
    print(f"mock phase {phase} stderr", file=sys.stderr)
    if str(phase) == os.environ.get("KLOKAST_TEST_FAIL_PHASE"):
        sys.exit(37)
'''


class Dom0ProvisioningCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dom0 cli ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ansible = self.root / "checkout" / "ansible"
        self.bin = self.ansible / "bin"
        self.bin.mkdir(parents=True)
        for name in ("bootstrap-dom0", "provision-box", "render-node-inventory", "magicdns-suffix"):
            shutil.copy2(REPO / "ansible" / "bin" / name, self.bin / name)
        (self.ansible / "ansible.cfg").touch()
        self.inventory = self.ansible / "execution-inventory" / "hosts"
        self.inventory.parent.mkdir()
        self.inventory.write_text("all: {}\n")
        self.home = self.root / "home"
        self.home.mkdir()
        self.private = self.root / "private state"
        self.console_file(self.private)
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        # A caller's unrelated files must survive cleanup on every exit path.
        self.sentinel = self.tmp / "unrelated"
        self.sentinel.write_text("keep")
        self.records = self.root / "commands.jsonl"
        commands = self.root / "commands"
        commands.mkdir()
        for name in ("ansible-playbook", "ansible-inventory", "tailscale"):
            command = commands / name
            command.write_text(f"#!{sys.executable}\n" + COMMAND)
            command.chmod(0o755)
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("KLOKAST_", "ANSIBLE_"))
        }
        self.env.update({
            "HOME": str(self.home), "TMPDIR": str(self.tmp),
            "PATH": str(commands) + os.pathsep + os.environ["PATH"],
            "KLOKAST_MAGICDNS_SUFFIX": "fixture.ts.net",
            "KLOKAST_PRIVATE_ROOT": str(self.private),
            "KLOKAST_TEST_RECORDS": str(self.records),
            "KLOKAST_TEST_PLAYBOOKS": str(self.ansible / "playbooks"),
        })
        # Use the real kernel lock. Change only its path and ownership policy
        # in the copied fixture; no privileged filesystem paths are touched.
        lock_temporary = tempfile.TemporaryDirectory(prefix="dom0-lock-")
        self.addCleanup(lock_temporary.cleanup)
        self.lock_root = Path(lock_temporary.name)
        self.lock_root.chmod(0o755)
        self.lock = self.lock_root / "operation.lock"
        self.lock.touch()
        self.lock.chmod(0o660)
        source = (REPO / "ansible/lib/platform-installation-lock.sh").read_text()
        source = source.replace("/var/lib/klokast/updates", str(self.lock_root))
        source = source.replace("'0:755'", f"'{os.getuid()}:755'")
        source = source.replace('"0:$(id -g):660:1"', f'"{os.getuid()}:$(id -g):660:1"')
        library = self.ansible / "lib"
        library.mkdir()
        (library / "platform-installation-lock.sh").write_text(source)

    def console_file(self, root):
        root.mkdir(parents=True, exist_ok=True)
        path = root / "dom0-console.yml"
        path.write_text("dom0_console_password_hashes: {}\n")
        return path

    def run_cli(self, name="bootstrap-dom0", args=(), input="", env=None):
        self.records.write_text("")
        selector = "--node" if name == "bootstrap-dom0" else "--box"
        result = subprocess.run(
            [str(self.bin / name), selector, "fixture", *args],
            input=input, capture_output=True, text=True, timeout=10,
            cwd=self.root, env=self.env if env is None else env,
        )
        self.calls = [json.loads(line) for line in self.records.read_text().splitlines()]
        self.assertEqual(list(self.tmp.iterdir()), [self.sentinel])
        self.assertEqual(self.sentinel.read_text(), "keep")
        return result

    def plays(self):
        return [call for call in self.calls if call["kind"] == "ansible-playbook"]

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def phases(self, result):
        return [int(phase) for phase in re.findall(r"^  (\d+)  ", result.stdout, re.M)]

    def test_default_bootstrap_plan_matches_bounded_runner(self):
        bootstrap = self.run_cli(args=["--dry-run-plan"])
        self.assert_ok(bootstrap)
        self.assertEqual(self.phases(bootstrap), BOOTSTRAP_PHASES)
        self.assertEqual(self.plays(), [])
        runner = self.run_cli("provision-box", ["--to", "22", "--dry-run-plan"])
        self.assert_ok(runner)
        self.assertEqual(
            re.findall(r"^  .*", bootstrap.stdout, re.M),
            re.findall(r"^  .*", runner.stdout, re.M),
        )
        full = self.run_cli("provision-box", ["--dry-run-plan"])
        self.assert_ok(full)
        self.assertEqual(self.phases(full), [
            *BOOTSTRAP_PHASES, 23, 24, 25, 26, 27, 30, 31,
            40, 41, 42, 43, 68, 69,
        ])

    def test_full_bootstrap_matches_runner_arguments_and_private_files(self):
        args = ["--manual-iso-detach", "--target-disk-device", "/dev/fixture",
                "--inventory", str(self.inventory), "--magicdns-suffix", "other.ts.net",
                "--oob-host", "fixture-oob", "--oob-user", "operator"]
        normalized = []
        for name in ("bootstrap-dom0", "provision-box"):
            selected = args if name == "bootstrap-dom0" else ["--to", "22", *args]
            result = self.run_cli(name, selected, input="wipe\n\n")
            self.assert_ok(result)
            plays = self.plays()
            self.assertEqual([call["phase"] for call in plays], BOOTSTRAP_PHASES)
            normalized.append([
                [arg.replace(call["run_dir"], "<RUN>") for arg in call["argv"]]
                for call in plays
            ])
            for call in plays:
                expected_host = "fixture-bootstrap" if call["phase"] < 20 else "fixture-dom0"
                self.assertEqual(call["argv"][call["argv"].index("--limit") + 1], expected_host)
                self.assertEqual(call["modes"], {
                    "run_dir": 0o700, "connection": 0o600,
                    "bootstrap_known_hosts_file": 0o600, "dom0_known_hosts_file": 0o600,
                })
                self.assertIn("other.ts.net", call["inventory"])
                self.assertIn("/dev/fixture", call["inventory"])
                self.assertFalse(Path(call["run_dir"]).exists())
            self.assertIn("/logs/provision-box/", result.stdout)
        self.assertEqual(normalized[0], normalized[1])

    def test_repeated_options_use_last_value(self):
        result = self.run_cli(args=["--node", "INVALID", "--node", "fixture",
                                   "--from", "69", "--from", "20",
                                   "--to", "69", "--to", "22", "--dry-run-plan"])
        self.assert_ok(result)
        self.assertEqual(self.phases(result), [20, 21, 22])

    def test_invalid_or_unbounded_ranges_do_not_execute(self):
        for args in (["--to", "23"], ["--from", "69"], ["--to", "99999999999999999999"],
                     ["--from", "15"], ["--to", "19"], ["--from", "22", "--to", "10"],
                     ["--to", "022"], ["--from", "-1"]):
            with self.subTest(args=args):
                result = self.run_cli(args=args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("error:", result.stderr)
                self.assertEqual(self.calls, [])

    def test_name_validation_is_applied_by_runner(self):
        for name in ("INVALID", "fixture-dom0", "-fixture", ""):
            with self.subTest(name=name):
                result = self.run_cli(args=["--node", name])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls, [])

    def test_missing_values_and_unsupported_options_do_not_execute(self):
        for args in (["--node"], ["--from"], ["--to"], ["--inventory"],
                     ["--private-state-root"], ["--target-disk-device"],
                     ["--magicdns-suffix"], ["--oob-host"], ["--oob-user"],
                     ["--yes"], ["--box", "other"], ["--unknown"]):
            with self.subTest(args=args):
                result = self.run_cli(args=args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls, [])

    def test_help_needs_no_discovery_or_node(self):
        result = subprocess.run([str(self.bin / "bootstrap-dom0"), "--help"],
                                capture_output=True, text=True, timeout=5,
                                env={**self.env, "KLOKAST_MAGICDNS_SUFFIX": "invalid"})
        self.assert_ok(result)
        self.assertIn("--node NODE", result.stdout)
        self.assertFalse(self.records.exists())

    def test_delimiter_preserves_arguments_without_changing_selection(self):
        extra = ["-e", '{"value":"spaces and literal $(command)"}', "--to", "69",
                 "--box", "other", "--yes"]
        result = self.run_cli(args=["--from", "10", "--to", "10", "--", *extra])
        self.assert_ok(result)
        self.assertEqual([call["phase"] for call in self.plays()], [10])
        self.assertEqual(self.plays()[0]["argv"][-len(extra):], extra)

    def test_wipe_gate_cannot_be_bypassed_by_forwarded_yes(self):
        for name in ("bootstrap-dom0", "provision-box"):
            result = self.run_cli(name, ["--from", "11", "--to", "11", "--", "--yes"], input="exit\n")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("operator stopped before disk wipe", result.stderr)
            self.assertEqual(self.plays(), [])

    def test_manual_detach_abort_prevents_reboot(self):
        result = self.run_cli(args=["--from", "14", "--to", "14", "--manual-iso-detach"], input="abort\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("operator aborted before reboot", result.stderr)
        self.assertEqual(self.plays(), [])

    def test_automatic_detach_options_reach_phase_14(self):
        result = self.run_cli(args=["--from", "14", "--to", "14", "--oob-host", "test-oob",
                                   "--oob-user", "test-user", "--magicdns-suffix", "other.ts.net"])
        self.assert_ok(result)
        options = next(json.loads(arg) for arg in self.plays()[0]["argv"] if arg.startswith("{"))
        self.assertEqual(options, {"nanokvm_virtual_media_enabled": True,
                                  "nanokvm_virtual_media_host": "test-oob",
                                  "nanokvm_virtual_media_user": "test-user",
                                  "nanokvm_virtual_media_magicdns_suffix": "other.ts.net"})

    def test_stale_identity_blocks_handoff_and_bounds_resume(self):
        for name in ("bootstrap-dom0", "provision-box"):
            result = self.run_cli(name, ["--from", "21", "--to", "22"], env={
                **self.env, "KLOKAST_TEST_TAILSCALE_STATUS": json.dumps({
                    "Self": {"HostName": "fixture-dom0", "Online": False},
                }),
            })
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--box fixture --from 21 --to 22", result.stderr)
            self.assertEqual(self.plays(), [])

    def test_failed_identity_query_blocks_handoff(self):
        result = self.run_cli(args=["--from", "21", "--to", "21"],
                              env={**self.env, "KLOKAST_TEST_TAILSCALE_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not query tailscale status", result.stderr)
        self.assertEqual(self.plays(), [])

    def test_console_path_uses_cli_then_environment_then_home(self):
        override = self.root / "override private state"
        home_root = self.home / "private" / "klokast"
        for root in (override, home_root):
            self.console_file(root)
        without_root = {key: value for key, value in self.env.items() if key != "KLOKAST_PRIVATE_ROOT"}
        for name in ("bootstrap-dom0", "provision-box"):
            for extra, env, expected in (
                    (["--private-state-root", str(override)], self.env, override),
                    ([], self.env, self.private), ([], without_root, home_root)):
                with self.subTest(name=name, root=expected):
                    result = self.run_cli(name, ["--from", "20", "--to", "20", *extra], env=env)
                    self.assert_ok(result)
                    self.assertIn("@" + str(expected / "dom0-console.yml"), self.plays()[0]["argv"])

    def test_missing_console_vars_fail_before_first_phase(self):
        (self.private / "dom0-console.yml").unlink()
        for name in ("bootstrap-dom0", "provision-box"):
            result = self.run_cli(name, ["--to", "22"], input="wipe\n")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing private dom0 console vars", result.stderr)
            self.assertEqual(self.plays(), [])

    def test_ansible_failure_stops_phases_preserves_status_and_logs(self):
        for name in ("bootstrap-dom0", "provision-box"):
            result = self.run_cli(name, ["--to", "14"], input="wipe\n",
                                  env={**self.env, "KLOKAST_TEST_FAIL_PHASE": "12"})
            self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
            self.assertEqual([call["phase"] for call in self.plays()], [10, 11, 12])
            self.assertIn("--box fixture --from 12 --to 14", result.stdout)
            run_id = Path(self.plays()[-1]["run_dir"]).name
            log_dir = self.home / "private/klokast/logs/provision-box" / run_id
            logs = list(log_dir.glob("12-*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("mock phase 12 stdout", logs[0].read_text())
            self.assertIn("mock phase 12 stderr", logs[0].read_text())

    def test_busy_or_missing_lock_prevents_execution_but_not_dry_run(self):
        with self.lock.open("r+") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for name in ("bootstrap-dom0", "provision-box"):
                result = self.run_cli(name, ["--from", "10", "--to", "10"])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("holds the installation lock", result.stderr)
                self.assertEqual(self.plays(), [])
        self.lock.unlink()
        (self.private / "dom0-console.yml").unlink()
        for name in ("bootstrap-dom0", "provision-box"):
            result = self.run_cli(name, ["--from", "10", "--to", "10"])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("lock is absent or unsafe", result.stderr)
            self.assertEqual(self.plays(), [])
            result = self.run_cli(name, ["--to", "22", "--dry-run-plan"])
            self.assert_ok(result)
            self.assertEqual(self.plays(), [])


if __name__ == "__main__":
    unittest.main()
