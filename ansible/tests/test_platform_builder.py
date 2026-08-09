#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "ansible" / "bin" / "platform-builder"
DOM0 = (
    REPO_ROOT
    / "ansible"
    / "roles"
    / "klokast-cli-builder"
    / "files"
    / "klokast-cli-builder-dom0"
)
PLAYBOOK = REPO_ROOT / "ansible" / "playbooks" / "73-platform-builder.yml"
CONTROLLER_PLAYBOOK = REPO_ROOT / "ansible" / "playbooks" / "67-ops-controller-converge.yml"
ROLE_TASKS = REPO_ROOT / "ansible" / "roles" / "klokast-cli-builder" / "tasks" / "main.yml"


def load(path, name):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PlatformBuilderWrapperTest(unittest.TestCase):
    def setUp(self):
        self.mod = load(WRAPPER, "platform_builder")

    def test_rejects_unsafe_box_commit_and_path_inputs(self):
        for value in ("", "K001", "../k001", "k001-dom0", "a" * 33):
            with self.subTest(box=value), self.assertRaises(self.mod.BuilderError):
                self.mod.validate_box(value)
        for value in ("abc123", "A" * 40, "0" * 39, "0" * 41):
            with self.subTest(commit=value), self.assertRaises(self.mod.BuilderError):
                self.mod.validate_commit(value)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(self.mod.BuilderError):
                self.mod.safe_child(Path(temporary), "..", "escaped")

    def test_rejects_non_smith_and_inactive_controllers(self):
        with patch.object(self.mod.getpass, "getuser", return_value="agent"):
            with self.assertRaisesRegex(self.mod.BuilderError, "smith"):
                self.mod.require_active_controller("k001")
        completed = Mock(returncode=78, stdout="", stderr="inactive")
        with patch.object(self.mod.getpass, "getuser", return_value="smith"), patch.object(
            self.mod.Path, "is_file", return_value=True
        ), patch.object(self.mod.os, "access", return_value=True), patch.object(
            self.mod, "run", return_value=completed
        ):
            with self.assertRaisesRegex(self.mod.BuilderError, "inactive"):
                self.mod.require_active_controller("k001")

    def test_rejects_dirty_or_unsynchronized_source(self):
        commit = "a" * 40

        def dirty_output(argv, **_kwargs):
            command = " ".join(str(item) for item in argv)
            if "--is-inside-work-tree" in command:
                return "true"
            if "--show-toplevel" in command:
                return str(REPO_ROOT)
            if "status --porcelain" in command:
                return "?? unreviewed.go"
            return commit

        with patch.object(self.mod, "output", side_effect=dirty_output), patch.object(self.mod, "run"):
            with self.assertRaisesRegex(self.mod.BuilderError, "dirty"):
                self.mod.verify_repository(commit)

        def stale_output(argv, **_kwargs):
            command = " ".join(str(item) for item in argv)
            if "--is-inside-work-tree" in command:
                return "true"
            if "--show-toplevel" in command:
                return str(REPO_ROOT)
            if "status --porcelain" in command:
                return ""
            if "@{upstream}" in command:
                return "b" * 40
            return commit

        with patch.object(self.mod, "output", side_effect=stale_output), patch.object(self.mod, "run"):
            with self.assertRaisesRegex(self.mod.BuilderError, "synchronized"):
                self.mod.verify_repository(commit)

    def test_rejects_image_digest_mismatch(self):
        completed = Mock(returncode=0, stdout=json.dumps({"Digest": "sha256:" + "0" * 64}), stderr="")
        with patch.object(self.mod, "run", return_value=completed):
            with self.assertRaisesRegex(self.mod.BuilderError, "digest mismatch"):
                self.mod.inspect_image_archive(Path("image.oci.tar"))

    def test_image_download_uses_an_empty_private_authfile(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            self.mod, "run"
        ) as run, patch.object(
            self.mod, "inspect_image_archive", return_value=self.mod.GO_IMAGE_DIGEST
        ):
            staging = Path(temporary)
            archive = staging / "golang-1.24.13-bookworm.oci.tar"
            archive.write_bytes(b"oci archive")
            self.mod.prepare_image_archive(staging)

            authfile = staging / "registry-auth.json"
            self.assertEqual(json.loads(authfile.read_text(encoding="utf-8")), {"auths": {}})
            self.assertEqual(authfile.stat().st_mode & 0o777, 0o600)
            command = [str(item) for item in run.call_args.args[0]]
            self.assertEqual(command[command.index("--authfile") + 1], str(authfile))

    def test_staged_inputs_have_fixed_size_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input"
            path.write_bytes(b"x")
            self.assertEqual(self.mod.bounded_size(path, 1, "input"), 1)
            with self.assertRaisesRegex(self.mod.BuilderError, "size limit"):
                self.mod.bounded_size(path, 0, "input")

    def write_result(self, directory, *, test_result="success", binary_hash=None):
        binary = directory / "klokast"
        binary.write_bytes(b"verified binary")
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        receipt = {
            "schema_version": 1,
            "operation_id": "0123456789ab",
            "source_commit": "a" * 40,
            "source_archive_sha256": "b" * 64,
            "image_manifest_digest": self.mod.GO_IMAGE_DIGEST,
            "image_archive_sha256": "c" * 64,
            "toolchain": "go version go1.24.13 linux/amd64",
            "toolchain_sha256": "d" * 64,
            "test_result": test_result,
            "build_result": "success",
            "binary_sha256": binary_hash or digest,
            "started_at": "2026-08-09T00:00:00Z",
            "finished_at": "2026-08-09T00:00:01Z",
        }
        (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        (directory / "build.log").write_text("redacted\n", encoding="utf-8")
        return {
            "operation_id": receipt["operation_id"],
            "source_commit": receipt["source_commit"],
            "source_archive_sha256": receipt["source_archive_sha256"],
            "image_archive_sha256": receipt["image_archive_sha256"],
        }

    def test_rejects_failed_tests_and_binary_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            expected = self.write_result(directory, test_result="failure")
            with self.assertRaisesRegex(self.mod.BuilderError, "failed tests"):
                self.mod.verify_result(directory, expected)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            expected = self.write_result(directory, binary_hash="0" * 64)
            with self.assertRaisesRegex(self.mod.BuilderError, "binary hash"):
                self.mod.verify_result(directory, expected)


class PlatformBuilderDom0Test(unittest.TestCase):
    def setUp(self):
        self.mod = load(DOM0, "platform_builder_dom0")

    def test_xen_definition_has_zero_vifs_and_fixed_limits(self):
        rendered = self.mod.render_xen_config(
            "k001-builder-klokast-cli-0123456789ab",
            Path("/dev/vg0/lv_builder_klokast_cli_0123456789ab"),
        )
        self.assertIn("type = \"pvh\"", rendered)
        self.assertIn("memory = 2048", rendered)
        self.assertIn("vcpus = 2", rendered)
        self.assertIn("vif = []", rendered)
        self.assertNotIn("bridge=", rendered)
        self.assertNotIn("device_model", rendered)
        self.assertNotIn("/etc/xen/auto", rendered)
        self.assertEqual(self.mod.SNAPSHOT_COW_SIZE, "8G")
        self.assertEqual(self.mod.BUILDER_TIMEOUT_SECONDS, 900)

    def test_guest_job_uses_rootless_networkless_bounded_build(self):
        job = self.mod.GUEST_JOB
        for required in (
            '"/sbin/su-exec", "builder:builder"',
            '"--network=none"',
            '"--read-only"',
            '"--cap-drop=all"',
            '"--security-opt=no-new-privileges"',
            '"--pids-limit=512"',
            '"--memory=1536m"',
            '"--cpus=2"',
            '"--env=CGO_ENABLED=0"',
            '"go", "test", "-mod=vendor", "-buildvcs=false", "./..."',
            '"go", "build", "-mod=vendor", "-trimpath", "-buildvcs=false"',
        ):
            self.assertIn(required, job)

    def test_timeout_fails_closed(self):
        with patch.object(self.mod, "domain_exists", return_value=True), patch.object(
            self.mod.time, "monotonic", side_effect=[0, 2]
        ):
            self.assertFalse(self.mod.wait_for_shutdown("builder", timeout_seconds=1))

    def test_cleanup_removes_every_ephemeral_resource(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "builder.cfg"
            config.write_text("vif = []\n", encoding="utf-8")
            snapshot = Path("/dev/vg0/lv_builder_klokast_cli_0123456789ab")
            mount = Path(temporary) / "mount"
            with patch.object(self.mod, "domain_exists", return_value=True), patch.object(
                self.mod, "lv_exists", return_value=True
            ), patch.object(self.mod, "run") as run, patch.object(
                self.mod, "unmap"
            ) as unmap, patch.object(
                self.mod, "remaining_resources", return_value=[]
            ):
                self.assertEqual(self.mod.cleanup("builder", config, snapshot, mount), [])
            self.assertFalse(config.exists())
            unmap.assert_called_once_with(snapshot)
            commands = [[str(item) for item in call.args[0]] for call in run.call_args_list]
            self.assertIn(["xl", "destroy", "builder"], commands)
            self.assertIn(["lvremove", "-f", str(snapshot)], commands)

    def test_playbook_fetches_before_removing_staging(self):
        tasks = ROLE_TASKS.read_text(encoding="utf-8")
        self.assertLess(tasks.index("Fetch available stopped-guest results"), tasks.index("Remove per-operation staging"))
        self.assertIn("alpine-virt-assets", PLAYBOOK.read_text(encoding="utf-8"))

    def test_large_transfers_use_bounded_dom0_data_scratch(self):
        playbook = PLAYBOOK.read_text(encoding="utf-8")
        tasks = ROLE_TASKS.read_text(encoding="utf-8")
        self.assertIn("path: /mnt/dom0_data/klokast-builder\n", playbook)
        self.assertIn('mode: "0711"', playbook)
        self.assertIn("/mnt/dom0_data/klokast-builder/transfer/{{ builder_operation_id }}", playbook)
        self.assertIn("ansible_remote_tmp:", playbook)
        self.assertIn("Remove operation-specific Ansible transfer scratch space", playbook)
        self.assertIn("builder_source_archive_size <= 67108864", tasks)
        self.assertIn("builder_image_archive_size <= 1073741824", tasks)
        self.assertIn("536870912", tasks)

    def test_snapshot_is_created_writable_without_mutating_the_sealed_origin(self):
        lifecycle = DOM0.read_text(encoding="utf-8")
        self.assertIn('"lvcreate", "--snapshot", "--ignoremonitoring"', lifecycle)
        self.assertNotIn('"lvchange", "--permission", "rw", str(snapshot_lv)', lifecycle)

    def test_controller_convergence_installs_oci_transport(self):
        controller = CONTROLLER_PLAYBOOK.read_text(encoding="utf-8")
        self.assertRegex(controller, r"(?m)^\s+- skopeo$")


if __name__ == "__main__":
    unittest.main()
