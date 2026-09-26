#!/usr/bin/env python3
"""Input-integrity and isolation checks for disposable VM template builds."""
import copy
import gzip
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ansible/lib"))
import vm_template_inputs as v
from platform_updates import UpdateError, digest


def module(name, path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    result = importlib.util.module_from_spec(spec)
    loader.exec_module(result)
    return result


def tar_member(name, content):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        member = tarfile.TarInfo(name)
        member.size = len(content)
        tar.addfile(member, io.BytesIO(content))
    return gzip.compress(out.getvalue())


class InputsTests(unittest.TestCase):
    def test_smoke_coldplug_uses_alpine_rules_and_normal_boot_only_verifies_devices(self):
        import stat
        smoke = module('smoke_devices', REPO / 'ansible/roles/vm-template-builder/files/vm-template-smoke-guest')
        devices = [SimpleNamespace(st_mode=stat.S_IFCHR | 0o666, st_rdev=os.makedev(1, minor), st_uid=0)
                   for minor in (3, 5)]
        with patch.object(smoke, 'run') as run, patch.object(smoke.Path, 'lstat', side_effect=devices):
            smoke.device_test(initialize=True)
            run.assert_called_once_with(['/sbin/mdev', '-s'])
        with patch.object(smoke, 'run') as run, patch.object(smoke.Path, 'lstat', side_effect=devices):
            smoke.device_test()
            run.assert_not_called()
        for info in (SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_rdev=os.makedev(1, 3), st_uid=0),
                     SimpleNamespace(st_mode=stat.S_IFREG | 0o666, st_rdev=0, st_uid=0)):
            with patch.object(smoke.Path, 'lstat', return_value=info), self.assertRaisesRegex(RuntimeError, 'device setup'):
                smoke.device_test()

    def fixture(self, root):
        (root / "packages").mkdir()
        (root / "keys").mkdir()
        (root / "keys/example.pub").write_bytes(b"test key")
        package = root / "packages/example-1-r0.apk"
        # APK v2 consists of concatenated gzip/tar streams, not one plain tar.
        package.write_bytes(tar_member(".SIGN.RSA.example.pub", b"signature") +
                            tar_member(".PKGINFO", b"pkgname = example\npkgver = 1-r0\narch = x86_64\n") +
                            tar_member("usr/bin/example", b"payload"))
        manifest = {"kind": v.KIND, "engine_commit": "a" * 40, "profile": "shared-alpine-v1",
                    "profile_sha256": "b" * 64, "branch": "v3.23", "architecture": "x86_64",
                    "world": ["example"], "repositories": [v.ORIGIN + "/v3.23/" + n for n in ("main", "community")],
                    "keys": {"example.pub": v.sha256(root / "keys/example.pub")},
                    "indexes": {"APKINDEX." + n + ".tar.gz": "c" * 64 for n in ("1111", "2222")},
                    "packages": [v.read_package(package)]}
        self.seal(root, manifest)
        return manifest

    def test_router_inputs_require_explicit_profile_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.fixture(root)
            manifest['profile'] = 'router-alpine-v1'
            self.seal(root, manifest)
            with self.assertRaises(UpdateError):
                v.verify_inputs(root, manifest)
            v.verify_inputs(root, manifest, expected_profile='router-alpine-v1')
            manifest['profile'] = 'shared-alpine-v1'
            self.seal(root, manifest)
            with self.assertRaises(UpdateError):
                v.verify_inputs(root, manifest, expected_profile='router-alpine-v1')

    def seal(self, root, manifest):
        manifest.pop("inputs_sha256", None)
        manifest["inputs_sha256"] = digest(manifest)
        (root / "inputs.json").write_text(json.dumps(manifest))

    def test_concatenated_package_and_capsule_are_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.fixture(root)
            v.verify_inputs(root, manifest)
            capsule = root / "capsule.tar"
            result = v.capsule(root, capsule, Path(__file__), Path(__file__), Path(__file__), Path(__file__), Path(__file__), Path(__file__), Path(__file__), Path(__file__), Path(__file__))
            self.assertEqual(result["sha256"], v.sha256(capsule))
            with tarfile.open(capsule) as archive:
                self.assertEqual(archive.getnames(), ["inputs.json", "keys/example.pub", "packages/example-1-r0.apk", "build.py", "smoke.py", "retained_data.py", "retained_data_test.py", "vm_app_compatibility.py", "static_site_test.py", "vm_personalize.py", "vm_personalize_test.py", "personalization-config.json"])

    def test_forged_or_changed_inputs_fail_before_native_commands(self):
        changes = [lambda m: m.update(engine_commit="main"), lambda m: m.update(world=[["example"]]),
                   lambda m: m.update(keys={}), lambda m: m.update(indexes={}),
                   lambda m: m["packages"][0].update(file="../example.apk"),
                   lambda m: m["packages"][0].update(architecture="aarch64"),
                   lambda m: m["packages"][0].update(name={}),
                   lambda m: m["packages"].append(copy.deepcopy(m["packages"][0]))]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest = self.fixture(root)
                change(manifest)
                self.seal(root, manifest)
                with self.assertRaises(UpdateError):
                    v.verify_inputs(root, manifest)

    def test_byte_change_extra_key_and_symlink_directory_are_rejected(self):
        for mutation in ("bytes", "extra-key", "symlink"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest = self.fixture(root)
                if mutation == "bytes":
                    (root / manifest["packages"][0]["file"]).write_bytes(b"changed")
                elif mutation == "extra-key":
                    (root / "keys/extra.pub").write_text("extra")
                else:
                    (root / "packages").rename(root / "elsewhere")
                    (root / "packages").symlink_to(root / "elsewhere", target_is_directory=True)
                with self.assertRaises(UpdateError): v.verify_inputs(root, manifest)

    def test_duplicate_identity_and_wrong_filename_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.fixture(root)
            path = root / manifest["packages"][0]["file"]
            with path.open("ab") as stream:
                stream.write(tar_member(".PKGINFO", b"pkgname = example\npkgver = 1-r0\narch = x86_64\n"))
            with self.assertRaises(UpdateError): v.read_package(path)
            path.rename(root / "wrong.apk")
            with self.assertRaises(UpdateError): v.read_package(root / "wrong.apk")

    def test_official_key_email_filename_is_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.fixture(root)
            name = "alpine-devel@lists.alpinelinux.org-6165ee59.rsa.pub"
            (root / "keys/example.pub").rename(root / "keys" / name)
            manifest["keys"] = {name: v.sha256(root / "keys" / name)}
            self.seal(root, manifest)
            v.verify_inputs(root, manifest)

    def test_bootstrap_cannot_run_as_root(self):
        with patch.object(v.os, "geteuid", return_value=0), patch.object(v, "invoke") as invoke:
            with self.assertRaises(UpdateError): v.bootstrap("missing", "missing", "missing")
            invoke.assert_not_called()

    def test_guest_rejects_wrong_environment_before_output(self):
        guest = module("vm_build_guest", REPO / "ansible/roles/vm-template-builder/files/vm-template-build-guest")
        with patch.object(guest, "parameters", side_effect=RuntimeError("dom0 forbidden")), patch.object(guest, "build") as build:
            with self.assertRaisesRegex(RuntimeError, "dom0 forbidden"): guest.main()
            build.assert_not_called()

    def test_smoke_test_refuses_an_ordinary_process_before_mounting(self):
        guest = module("vm_smoke_guest", REPO / "ansible/roles/vm-template-builder/files/vm-template-smoke-guest")
        with patch.object(guest.os, "getpid", return_value=123), patch.object(guest, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "PID 1"):
                guest.main()
            run.assert_not_called()

    def test_generic_image_drops_package_added_boot_services_and_locks_root(self):
        guest = module("vm_baseline_guest", REPO / "ansible/roles/vm-template-builder/files/vm-template-build-guest")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            inputs = Path(temporary) / "inputs"
            (root / "etc/runlevels/default").mkdir(parents=True)
            (root / "etc/runlevels/default/unexpected-daemon").symlink_to("/etc/init.d/unexpected-daemon")
            (root / "etc/shadow").write_text("root::0:0:99999:7:::\n")
            inputs.mkdir()
            (inputs / "smoke.py").write_text("# fixed test job\n")
            (inputs / "retained_data.py").write_text("# copy primitive\n")
            (inputs / "retained_data_test.py").write_text("# synthetic test\n")
            (inputs / "vm_personalize.py").write_text("# personalization primitive\n")
            (inputs / "vm_personalize_test.py").write_text("# synthetic personalization test\n")
            (inputs / "personalization-config.json").write_text("{}\n")
            (inputs / "vm_app_compatibility.py").write_text("# component validator\n")
            (inputs / "static_site_test.py").write_text("# component adapter\n")
            manifest = {"repositories": [], "packages": [{"name": "example", "version": "1"}], "world": ["example"],
                        "engine_commit": "a" * 40, "profile": "shared-alpine-v1", "inputs_sha256": "b" * 64}
            with patch.object(guest, "ROOT", root), patch.object(guest, "INPUT", inputs):
                guest.baseline(manifest)
            self.assertEqual(list((root / "etc/runlevels/default").iterdir()), [])
            self.assertTrue((root / "etc/shadow").read_text().startswith("root:!:"))


class HostBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.host = module("vm_build_dom0", REPO / "ansible/roles/vm-template-builder/files/vm-template-build-dom0")

    def test_build_configuration_has_only_operation_disks_and_no_network(self):
        work = Path("/mnt/dom0_data/klokast-vm-templates/staging/" + "a" * 24)
        request = {"operation_id": "a" * 24, "inputs_sha256": "b" * 64, "capsule": {"sha256": "c" * 64, "bytes": 10240}}
        loops = {name: "/dev/loop" + str(i) for i, name in enumerate(("input", *self.host.SLOTS))}
        config = self.host.configuration(work, "vm-build-" + "a" * 24, "test-uuid", request, loops)
        self.assertIn("vif = []", config)
        self.assertIn("phy:/dev/loop0,xvda,r", config)
        self.assertNotIn("qdisk", config)
        self.assertNotIn("/dev/vg", config)
        self.assertNotIn("/etc/xen/auto", config)

    def test_component_disk_is_readonly_and_missing_or_changed_evidence_blocks_success(self):
        import vm_app_compatibility as app
        selection = {'kind': 'klokast.vm-app-test-input.v1', 'app': 'static-site-web',
                     'image_ref': 'ghcr.io/static-web-server/static-web-server@sha256:' + 'a' * 64,
                     'manifest_sha256': 'a' * 64, 'image_id': 'b' * 64,
                     'archive': {'sha256': 'c' * 64, 'bytes': 100},
                     'config_sha256': 'd' * 64, 'adapter_sha256': 'e' * 64}
        for mode in ('valid', 'missing', 'changed', 'normal-failed', 'normal-input-changed',
                     'profile-failed', 'profile-receipt-changed', 'stage-source-changed',
                     'restore-failed', 'restore-changed-receipt'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                for name in ('root.slot', 'kernel.slot', 'initramfs.slot', 'app-test.tar'):
                    (work / name).write_bytes(b'opaque bytes')
                request = {'operation_id': 'a' * 24, 'inputs_sha256': 'b' * 64,
                           'app_test': {'selection': selection, 'capsule': {'sha256': 'f' * 64, 'bytes': 100}}}
                build = {'kernel_release': 'test-kernel',
                         'artifacts': {name: {'bytes': 12} for name in ('kernel', 'initramfs')}}
                restore_request = {'kind': 'klokast.vm-backup-restore.v1', 'operation_id': request['operation_id'],
                    'engine_commit': 'a' * 40, 'backup_receipt_sha256': 'c' * 64,
                    'disk_bytes': 256 * 1024**2, 'disk_sha256': hashlib.sha256(b'').hexdigest(),
                    'root_partition': 3, 'root_uuid': '11111111-1111-4111-8111-111111111111',
                    'runtime': {'uid': 2000, 'gid': 2000, 'subuid': [[200000, 65536]], 'subgid': [[200000, 65536]]}}
                expected_identity = {'sha256': '9' * 64, 'entries': 1, 'required_bytes': 4096}
                def boot(work, name, identity, config, request, **kwargs):
                    text = config.read_text()
                    self.assertIn('vif = []', text)
                    if name.startswith('vm-restore-'):
                        self.assertIn('phy:/dev/loop2,xvdc,r', text)
                        self.assertIn('phy:/dev/loop6,xvde,r', text)
                        self.assertIn('init=/usr/local/libexec/retained_data.py', text)
                        self.assertNotIn('klokast_app_capsule=', text)
                        self.assertEqual(kwargs['timeout'], 300)
                        restored = {'kind': 'klokast.vm-backup-restore-result.v1', 'request_sha256': digest(restore_request),
                            **{k: restore_request[k] for k in ('backup_receipt_sha256', 'disk_sha256', 'disk_bytes', 'root_uuid', 'runtime')},
                            'identity': expected_identity, 'complete_disk_restored': True, 'root_filesystem_checked': True,
                            'backup_unchanged': True, 'source_freshness_verified': False,
                            'application_consistency_verified': False, 'adoption_accepted': False}
                        restored['receipt_sha256'] = digest(restored)
                        if mode == 'restore-changed-receipt': restored['receipt_sha256'] = '0' * 64
                        output = {'kind': 'klokast.vm-backup-restore-guest.v1', 'success': mode != 'restore-failed',
                                  'operation_id': request['operation_id'], 'inputs_sha256': request['inputs_sha256'],
                                  'request_sha256': digest(restore_request), 'restore': restored}
                        (work / 'test-result.slot').write_text(json.dumps(output))
                        return
                    self.assertIn('phy:/dev/loop4,xvde,r', text)
                    self.assertIn('klokast_app_capsule=' + 'f' * 64, text)
                    if name.startswith(('vm-personalize-', 'vm-profile-')):
                        self.assertIn('phy:/dev/loop8,xvdf,r', text)
                        self.assertEqual(kwargs['timeout'], 300)
                        prepared = {'kind': 'klokast.vm-template-personalize-stage.v1', 'success': True,
                                    'operation_id': request['operation_id'], 'inputs_sha256': request['inputs_sha256'],
                                    'production_data_used': False, 'request_sha256': '1' * 64, 'receipt_sha256': '2' * 64,
                                    'source_root_sha256': hashlib.sha256(b'opaque bytes').hexdigest()}
                        if name.startswith('vm-personalize-'):
                            self.assertIn('phy:/dev/loop7,xvdd,w', text)
                            self.assertIn('klokast_personalize_stage=1', text)
                            if mode == 'stage-source-changed': prepared['source_root_sha256'] = '0' * 64
                            (work / 'test-result.slot').write_text(json.dumps(prepared))
                        else:
                            self.assertIn('phy:/dev/loop7,xvda,w', text)
                            self.assertIn('klokast_personalized=1', text)
                            profile = {**prepared, 'kind': 'klokast.vm-template-personalized-test.v1',
                                       'kernel_release': 'test-kernel', 'success': mode != 'profile-failed',
                                       'tests': dict.fromkeys(('personalized_boot', 'configuration', 'retained_mount',
                                           'runtime_identity', 'tailscale_retained_state', 'firewall', 'default_rootless_podman', 'packages_unchanged'), True)}
                            if mode == 'profile-receipt-changed': profile['receipt_sha256'] = '0' * 64
                            (work / 'test-result.slot').write_text(json.dumps(profile))
                        return
                    if name.startswith('vm-openrc-'):
                        self.assertIn('init=/sbin/init klokast_openrc=1', text)
                        self.assertNotIn('init=/usr/local/libexec/klokast-template-test', text)
                        self.assertEqual(kwargs['timeout'], 300)
                        self.assertEqual((work / 'test-result.slot').read_bytes().strip(b'\0'), b'')
                        normal = {'kind': 'klokast.vm-template-openrc-test.v1', 'success': mode != 'normal-failed',
                                  'operation_id': request['operation_id'], 'inputs_sha256': request['inputs_sha256'],
                                  'kernel_release': 'test-kernel', 'tests': dict.fromkeys(('openrc_boot', 'cgroup_v2',
                                      'kernel_modules', 'tailscale_offline', 'default_rootless_podman'), True)}
                        if mode == 'normal-input-changed': normal['inputs_sha256'] = '0' * 64
                        (work / 'test-result.slot').write_text(json.dumps(normal))
                        return
                    self.assertEqual(kwargs['timeout'], 600)
                    result = {'kind': 'klokast.vm-template-test-result.v1', 'success': True,
                              'operation_id': request['operation_id'], 'inputs_sha256': request['inputs_sha256'],
                              'kernel_release': 'test-kernel',
                              'tests': {k: True for k in ('boot', 'kernel_modules', 'tailscale_offline',
                                        'rootless_podman', 'nftables_kernel', 'retained_data_copy', 'retained_data_stage', 'retained_identity', 'retained_partition', 'personalization', 'backup_restore')}}
                    if mode != 'missing':
                        result['application_test'] = {'kind': 'klokast.vm-app-test-result.v1',
                            'selection': copy.deepcopy(selection), 'tests': dict.fromkeys(app.TESTS, True),
                            'production_qualified': mode == 'changed'}
                    result['backup_restore_test'] = {'request': restore_request, 'expected_identity': expected_identity,
                                                     'production_data_used': False}
                    (work / 'test-result.slot').write_text(json.dumps(result))
                with patch.object(self.host, 'SLOTS', {'root': 12}), \
                        patch.object(self.host.os, 'posix_fallocate'), \
                        patch.object(self.host, 'domain', return_value=None), \
                        patch.object(self.host, 'attach_loop', side_effect=['/dev/loop' + str(i) for i in range(9)]) as attach, \
                        patch.object(self.host, 'detach_loop') as detach, \
                        patch.object(self.host, 'boot_guest', side_effect=boot):
                    if mode == 'valid':
                        result = self.host.smoke_test(work, request, build)
                        self.assertFalse(result['application_test']['production_qualified'])
                    else:
                        message = ('personalized' if mode.startswith(('stage-', 'profile-')) else
                                   ('OpenRC boot tests' if mode.startswith('normal-') else
                                    ('dedicated backup restore' if mode.startswith('restore-') else 'component test evidence')))
                        with self.assertRaisesRegex(RuntimeError, message):
                            self.host.smoke_test(work, request, build)
                    self.assertEqual(attach.call_args.kwargs, {'readonly': True})
                    self.assertEqual(detach.call_count, 9 if mode == 'valid' or mode.startswith(('stage-', 'profile-')) else
                                     (7 if mode.startswith(('normal-', 'restore-')) else 5))
                self.assertEqual((work / 'root.slot').read_bytes(), b'opaque bytes')
                self.assertFalse((work / 'test-root.slot').exists())
                self.assertEqual(json.loads((work / 'test-lifecycle.json').read_text())['stage'], 'cleaned')

    def test_wrong_uuid_prevents_cleanup_of_another_guest(self):
        with self.assertRaisesRegex(RuntimeError, "UUID changed"):
            self.host.require_identity({"config": {"c_info": {"uuid": "other"}}}, "expected")

    def test_failed_xen_inventory_cannot_be_mistaken_for_shutdown(self):
        with patch("xen_build_runtime.run", side_effect=RuntimeError("xl failed")):
            with self.assertRaisesRegex(RuntimeError, "xl failed"):
                self.host.domain("vm-build-example")

    def test_unknown_xen_format_cannot_be_mistaken_for_shutdown(self):
        for records in ([], [{"domid": 7, "name": "vm-build-example"}], {"domains": []}):
            with self.subTest(records=records), patch("xen_build_runtime.run", return_value=SimpleNamespace(stdout=json.dumps(records))):
                with self.assertRaises(RuntimeError): self.host.domain("vm-build-example")

    def test_dom0_is_never_a_cleanup_target_even_with_a_matching_uuid(self):
        with self.assertRaisesRegex(RuntimeError, "UUID changed"):
            self.host.require_identity({"domid": 0, "config": {"c_info": {"uuid": "expected"}}}, "expected")

    def test_native_dom0_record_has_no_uuid(self):
        records = [{"domid": 0, "config": {"c_info": {"type": "pv", "name": "Domain-0"}}}]
        with patch("xen_build_runtime.run", return_value=SimpleNamespace(stdout=json.dumps(records))):
            self.assertIsNone(self.host.domain("vm-build-example"))

    def test_reassigned_loop_is_never_detached(self):
        with patch("xen_build_runtime.loop_devices", return_value=["/dev/loop2"]), patch("xen_build_runtime.run") as run:
            with self.assertRaisesRegex(RuntimeError, "loop identity changed"):
                self.host.detach_loop(Path("/operation/root.slot"), "/dev/loop1")
            run.assert_not_called()

    def test_reused_domain_refused_before_disk_creation(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(self.host, "domain", return_value={"domid": 5}):
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "reuse"):
                self.host.execute(root, {"operation_id": "a" * 24})
            self.assertEqual(list(root.iterdir()), [])

    def test_xen_memory_guard_handles_padded_native_info(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(self.host, "domain", return_value=None), \
                patch.object(self.host, "run", return_value=SimpleNamespace(stdout="free_memory            : 100\n")):
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "free Xen memory"):
                self.host.execute(root, {"operation_id": "a" * 24})
            self.assertEqual(list(root.iterdir()), [])

    def test_candidate_outputs_cannot_claim_success_without_matching_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "result.slot").write_text(json.dumps({"success": True, "kind": "klokast.vm-template-build-result.v1"}))
            with self.assertRaisesRegex(RuntimeError, "matching result"):
                self.host.read_result(root, {"operation_id": "a" * 24, "inputs_sha256": "b" * 64})

    def test_completion_signal_requires_whole_result_and_exact_operation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = {"operation_id": "a" * 24, "inputs_sha256": "b" * 64}
            result = {"kind": "klokast.vm-template-build-result.v1", "success": True, **request}
            for data, ready in ((b"\0" * 1024, False), (b'{"kind":', False),
                                (json.dumps({**result, "operation_id": "c" * 24}).encode(), False),
                                (json.dumps(result).encode() + b"\0", True)):
                (root / "result.slot").write_bytes(data)
                self.assertEqual(self.host.completion_ready(root, request), ready)

    def test_failed_boot_test_never_publishes_a_candidate_and_cleans_build_slots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = {"operation_id": "a" * 24, "inputs_sha256": "b" * 64,
                       "capsule": {"sha256": "c" * 64, "bytes": 10240}, "box": "boxa"}
            slots = {name: 4096 for name in self.host.SLOTS}
            with patch.object(self.host, "BASE", root / "artifacts"), patch.object(self.host, "SLOTS", slots), \
                    patch.object(self.host, "domain", return_value=None), \
                    patch.object(self.host, "run", return_value=SimpleNamespace(stdout="free_memory : 8192\n")), \
                    patch.object(self.host.os, "statvfs", return_value=SimpleNamespace(f_bavail=2**40, f_frsize=4096)), \
                    patch.object(self.host, "attach_loop", return_value="/dev/loop1"), \
                    patch.object(self.host, "detach_loop"), patch.object(self.host, "boot_guest"), \
                    patch.object(self.host, "read_result", return_value={}), \
                    patch.object(self.host, "smoke_test", side_effect=RuntimeError("boot test failed")):
                with self.assertRaisesRegex(RuntimeError, "boot test failed"):
                    self.host.execute(root, request)
            self.assertFalse((root / "artifacts").exists())
            self.assertFalse(list(root.glob("*.slot")))
            self.assertEqual(json.loads((root / "lifecycle.json").read_text())["stage"], "cleaned")

    def test_lingering_test_guest_preserves_all_outer_build_artifacts(self):
        for prefix in ('vm-openrc-', 'vm-personalize-', 'vm-profile-'):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                request = {'operation_id': 'a' * 24, 'inputs_sha256': 'b' * 64,
                           'capsule': {'sha256': 'c' * 64, 'bytes': 10240}, 'box': 'boxa'}
                running = []
                def failed(*args):
                    running.append(True)
                    raise RuntimeError('test guest could not be stopped')
                with patch.object(self.host, 'BASE', root / 'artifacts'), \
                        patch.object(self.host, 'SLOTS', {name: 4096 for name in self.host.SLOTS}), \
                        patch.object(self.host, 'domain', side_effect=lambda name: {} if running and name.startswith(prefix) else None), \
                        patch.object(self.host, 'run', return_value=SimpleNamespace(stdout='free_memory : 8192\n')), \
                        patch.object(self.host.os, 'statvfs', return_value=SimpleNamespace(f_bavail=2**40, f_frsize=4096)), \
                        patch.object(self.host, 'attach_loop', return_value='/dev/loop1'), \
                        patch.object(self.host, 'detach_loop'), patch.object(self.host, 'boot_guest'), \
                        patch.object(self.host, 'read_result', return_value={}), \
                        patch.object(self.host, 'smoke_test', side_effect=failed):
                    with self.assertRaisesRegex(RuntimeError, 'retain all build resources'):
                        self.host.execute(root, request)
                self.assertTrue((root / 'kernel.slot').exists())
                self.assertTrue((root / 'root.slot').exists())
                self.assertEqual(json.loads((root / 'lifecycle.json').read_text())['stage'], 'allocated')


if __name__ == "__main__":
    unittest.main()
