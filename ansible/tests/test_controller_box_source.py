"""Controller source approval, exact publication, and real-file execution tests."""
import copy
import datetime as dt
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import test_platform_apply as fixtures


class ControllerBoxSourceTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PlatformApplyTest()
        self.fixture.setUp()
        self.mod = self.fixture.mod

    def state(self, adopted=False):
        m = self.mod
        state = {
            "schema_version": 2, "kind": m.KIND_AUTHORITY_V2,
            "prior_state_kind": m.KIND_AUTHORITY_V2,
            "prior_state_sha256": "a" * 64, "signed_intent_sha256": "b" * 64,
            "transition_id": "previous-transition",
            "setting_groups": [
                {"id": m.BOX_GROUP_PREFIX + box, "scopes": m.box_scopes(box),
                 "source": "instance_specification_v1" if box == "boxb" or adopted else m.LEGACY_REGISTRY_SOURCE}
                for box in ("boxa", "boxb")
            ] + [{"id": m.TAILNET_GROUP_ID, "scopes": m.SCOPES, "source": "instance_specification_v1"}],
        }
        state["authority_state_sha256"] = m.authority_v2_hash(state)
        return state

    def plan(self, adopted=False, target="active-controller"):
        m = self.mod
        p = self.fixture.valid_plan()
        p.pop("atomic_action_group")
        p.update(schema_version=4, kind=m.KIND_PLAN_V4, connectivity_target=target,
                 selected_box="boxa" if target == "active-controller" else "boxb")
        p["authority_state"] = {k: v for k, v in self.state(adopted).items() if k in {"kind", "setting_groups", "authority_state_sha256"}}
        p["projection"] = {
            "boxes": [{"id": box, "access": {
                "declared_capabilities": ["overlay"], "legacy_available_capabilities": ["overlay"],
                "enabled_capabilities": ["overlay"], "prohibited_capabilities": [],
            }} for box in ("boxa", "boxb")],
            "control_plane": {"active_controller": {"box_id": "boxa", "hostname": "boxa-ops"}},
            "tailnet": {"magicdns_suffix": "example.ts.net"},
        }
        p["compatibility_inputs"] = [{"name": name, "sha256": str(i + 1) * 64}
                                     for i, name in enumerate(("legacy_deployment", m.LEGACY_REGISTRY_SOURCE, "legacy_controller_ha"))]
        p["engine"] = {"commit": "c" * 40}
        p["instance"] = {"commit": "d" * 40}
        p["instance_source"] = {"receipt_sha256": "e" * 64}
        p["controller_toolchain"] = {"receipt_sha256": "f" * 64}
        p["observation"] = {"generation_sha256": "1" * 64}
        p["inputs"] = [{"path": "klokast-instance.json", "sha256": "2" * 64}]
        p["actions"] = []
        p["action_groups"] = []
        for state_group in p["authority_state"]["setting_groups"]:
            gid = state_group["id"]
            if gid == m.TAILNET_GROUP_ID:
                p["action_groups"].append(dict(id=gid, operation="verify_instance_authority", scopes=m.SCOPES,
                                               executor=m.EXECUTOR, rollback_type=m.ROLLBACK_TYPE))
                continue
            box = gid.removeprefix(m.BOX_GROUP_PREFIX)
            selected = box == p["selected_box"]
            executor = (m.CONTROLLER_BOX_EXECUTOR if target == "active-controller" else m.BOX_EXECUTOR) if selected else "none"
            operation = "verify_instance_authority" if state_group["source"] == "instance_specification_v1" else ("adopt_instance_specification" if selected else "retain_legacy")
            rollback = "no_mutation" if executor == m.CONTROLLER_BOX_EXECUTOR else m.BOX_ROLLBACK_TYPE
            p["action_groups"].append(dict(id=gid, operation=operation, scopes=m.box_scopes(box), executor=executor, rollback_type=rollback, box=box))
            for scope in m.box_scopes(box):
                p["actions"].append(dict(
                    id=scope, finding_id=scope, scope=scope, executor=executor, operation=operation,
                    authority_before=state_group["source"], authority_after="instance_specification_v1" if selected else state_group["source"],
                    preconditions=["active_controller_fenced", "exact_plan_v4_revalidated", "effective_registry_compiles_equal",
                                   "router_verified_before_source_publication" if executor == m.CONTROLLER_BOX_EXECUTOR else "one_router_rollback_prepared"],
                    rollback=dict(strategy=rollback, authority=m.LEGACY_REGISTRY_SOURCE, source_sha256="2" * 64),
                ))
        return p

    def test_plan_v4_targets_scopes_prerequisites_and_historical_refusal(self):
        for target in ("active-controller", "non-controller"):
            for adopted in (False, True):
                with self.subTest(target=target, adopted=adopted), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    p = self.plan(adopted, target)
                    path = self.fixture.store_plan(root, p)
                    with patch.object(self.mod, "PLAN_ROOT", root):
                        _, result, group = self.mod.verify_plan_v3(path)
                        self.assertEqual(group["box"], result["selected_box"])
        mutations = {
            "old-plan": lambda p: p.update(kind=self.mod.KIND_PLAN_V3, schema_version=3),
            "wrong-controller": lambda p: p["projection"]["control_plane"]["active_controller"].update(box_id="boxb"),
            "partial-scope": lambda p: p["action_groups"][0]["scopes"].pop(),
            "extra-action": lambda p: p["actions"].append(copy.deepcopy(p["actions"][0])),
            "wrong-executor": lambda p: p["actions"][0].update(executor=self.mod.BOX_EXECUTOR),
            "peer-legacy": lambda p: p["authority_state"]["setting_groups"][1].update(source=self.mod.LEGACY_REGISTRY_SOURCE),
            "router-apply": lambda p: p["action_groups"][0].update(operation="apply-box-access"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                p = self.plan()
                mutate(p)
                path = self.fixture.store_plan(root, p)
                with patch.object(self.mod, "PLAN_ROOT", root), self.assertRaises(self.mod.ApplyError):
                    self.mod.verify_plan_v3(path)

    def test_closed_intent_rejects_rollback_commands_wrong_controller_and_expiry(self):
        value = self.fixture.valid_box_intent(box="boxa")
        value.update(kind=self.mod.KIND_CONTROLLER_BOX_INTENT, executor=self.mod.CONTROLLER_BOX_EXECUTOR,
                     rollback_type="no_mutation", active_controller="boxa-ops")
        self.mod.validate_controller_box_intent(value)
        for changes in ({"action": "rollback_to_legacy"}, {"command": "id"}, {"action_set": value["action_set"][:-1]},
                        {"active_controller": "boxb-ops"}, {"executor": self.mod.BOX_EXECUTOR},
                        {"issued_at": "2000-01-01T00:00:00Z", "expires_at": "2000-01-01T00:10:00Z"}):
            with self.subTest(changes=changes), self.assertRaises(self.mod.ApplyError):
                self.mod.validate_controller_box_intent({**value, **changes})
        with self.assertRaisesRegex(self.mod.ApplyError, "signer ID"):
            self.mod.verify_signature(Mock(signer_id="wrong"), value)
        with patch.object(self.mod, "run", return_value=Mock(returncode=1)), self.assertRaisesRegex(self.mod.ApplyError, "signature verification"):
            self.mod.verify_signature(Mock(signer_id=self.mod.SIGNER_ID, approval_signature="/signature"), value)

    def test_every_signed_input_and_old_engine_is_revalidated(self):
        m = self.mod
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            plan = self.plan()
            plan["plan_sha256"] = "3" * 64
            binding = {key: str(root / key) for key in (
                "plan_path", "authority_path", "toolchain_path", "source_path",
                "observation", "build_dir", "old_registry_path", "effective_registry_path",
            )}
            for key in ("old_registry_path", "effective_registry_path"):
                Path(binding[key]).write_bytes(b"unchanged")
            binding.update(recovery_path=str(root / (("4" * 64) + ".json")),
                           binary_sha256="5" * 64, builder_receipt_sha256="6" * 64,
                           old_registry_sha256="7" * 64, effective_registry_sha256="8" * 64,
                           compiled_sha256="9" * 64, router_vars_sha256="a" * 64)
            current = {**binding, "plan": plan, "state": self.state(), "group": plan["action_groups"][0]}
            intent = m.controller_box_intent_common(current, current["group"]["operation"], "input-test-nonce", m.now_utc())
            stack.enter_context(patch.object(m, "BOX_RUNTIME_ROOT", root / "runtime"))
            stack.enter_context(patch.object(m.os, "chown"))
            stack.enter_context(patch.object(m, "smith_gid", return_value=os.getgid()))
            stack.enter_context(patch.object(m, "validate_inputs_v3", return_value=current))
            for field in [k for k in intent if k.endswith("sha256") or k.endswith("commit")]:
                altered = {**intent, field: "0" * len(intent[field])}
                with self.subTest(field=field), self.assertRaisesRegex(m.ApplyError, "inputs changed"):
                    m.box_revalidate(binding, altered)
            plan["kind"] = m.KIND_PLAN_V3
            with self.assertRaisesRegex(m.ApplyError, "Plan v4 executor"):
                m.box_revalidate(binding, intent)
            self.assertEqual(list((root / "runtime").iterdir()), [])

    def test_active_guard_identity_is_checked_before_sealed_engine_work(self):
        m = self.mod
        plan = self.plan()
        with patch.object(m, "require_root_active", return_value={"hostname": "boxb-ops"}), patch.object(
            m, "require_self_match"
        ), patch.object(m, "verify_plan_v3", return_value=("/plan", plan, plan["action_groups"][0])), patch.object(
            m, "resolve_build_directory"
        ) as builder, self.assertRaisesRegex(m.ApplyError, "active controller identity"):
            m.validate_inputs_v3(Mock())
        builder.assert_not_called()

    def root_metadata(self, descriptor):
        fields = list(self.real_fstat(descriptor))
        fields[4] = 0
        return os.stat_result(fields)

    def test_shared_writer_refuses_concurrent_change_and_lock_holder(self):
        m = self.mod
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            state = self.state()
            states = root / "states"
            states.mkdir()
            pointer = root / "active"
            pointer.write_text(state["authority_state_sha256"] + "\n")
            (states / (state["authority_state_sha256"] + ".json")).write_text(m.canonical(state) + "\n")
            for name, value in {"AUTHORITY_ROOT": states, "AUTHORITY_POINTER": pointer}.items():
                stack.enter_context(patch.object(m, name, value))
            stack.enter_context(patch.object(m.os, "chown"))
            stack.enter_context(patch.object(m, "smith_gid", return_value=os.getgid()))
            self.real_fstat = os.fstat
            stack.enter_context(patch.object(m.os, "fstat", side_effect=self.root_metadata))
            next_state = m.make_authority_v2(state, {"nonce": "test-transition"}, group_id=m.BOX_GROUP_PREFIX + "boxa", source="instance_specification_v1")
            with m.authority_publication_lock(), self.assertRaisesRegex(m.ApplyError, "concurrent"):
                m.write_authority_v2(next_state)
            m.write_authority_v2(next_state)
            self.assertEqual(pointer.read_text(), next_state["authority_state_sha256"] + "\n")
            rival = m.make_authority_v2(state, {"nonce": "rival-transition"}, group_id=m.BOX_GROUP_PREFIX + "boxa", source="instance_specification_v1")
            with self.assertRaisesRegex(m.ApplyError, "prior state"):
                m.write_authority_v2(rival)
            self.assertFalse((states / (rival["authority_state_sha256"] + ".json")).exists())

    def test_signed_real_file_path_verifies_before_publication_and_cleans_up(self):
        # External sealed-engine and router calls are replaced. Preparation,
        # comparison, archive copying, exact signed binding, nonce, writer,
        # receipts, and cleanup use real code and files under umask 077.
        for outcome in ("direct", "derp", "verify", "verification-failed", "publication-failed", "receipt-failed", "changed-after-check"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                m = self.mod
                root = Path(temporary)
                previous_mask = os.umask(0o077)
                stack.callback(os.umask, previous_mask)
                for name in ("PREFLIGHT_ROOT", "BOX_RUNTIME_ROOT", "NONCE_ROOT", "EXECUTION_ROOT", "AUTHORITY_ROOT"):
                    stack.enter_context(patch.object(m, name, root / name))
                stack.enter_context(patch.object(m, "AUTHORITY_POINTER", root / "active"))
                stack.enter_context(patch.object(m, "smith_gid", return_value=os.getgid()))
                stack.enter_context(patch.object(m.os, "chown"))
                self.real_fstat = os.fstat
                stack.enter_context(patch.object(m.os, "fstat", side_effect=self.root_metadata))
                stack.enter_context(patch.object(m, "verify_public_checkout"))
                stack.enter_context(patch.object(m, "append_audit"))
                state = self.state(outcome == "verify")
                m.AUTHORITY_ROOT.mkdir()
                state_path = m.AUTHORITY_ROOT / (state["authority_state_sha256"] + ".json")
                state_path.write_text(m.canonical(state) + "\n")
                m.AUTHORITY_POINTER.write_text(state["authority_state_sha256"] + "\n")
                plan = self.plan(outcome == "verify")
                old = root / "registry.yml"
                registry = {"schema_version": 1, "apps": {"kept": {"enabled": False}}, "boxes": {
                    box: {"access": {"available_capabilities": ["overlay"], "enabled_capabilities": ["overlay"], "prohibited_capabilities": []}, "extra": ["kept"]}
                    for box in ("boxa", "boxb")
                }}
                old.write_text(m.yaml.safe_dump(registry))
                stack.enter_context(patch.object(m, "REGISTRY", old))
                plan["compatibility_inputs"][1]["sha256"] = m.sha256_file(old)
                plan["plan_sha256"] = "3" * 64
                binding = {k: str(root / k) for k in ("plan_path", "toolchain_path", "source_path", "observation", "build_dir")}
                binding.update(authority_path=str(state_path), recovery_path=str(root / (("4" * 64) + ".json")),
                               binary_sha256="5" * 64, builder_receipt_sha256="6" * 64)
                validations = []
                live = []

                def validate(_args, work):
                    validations.append(work)
                    if outcome == "changed-after-check" and len(validations) == 3:
                        raise m.ApplyError("private input changed after live verification")
                    comparison = m.prepare_registry_comparison(work, plan, "boxa")
                    return {**binding, **comparison, "plan": plan, "group": plan["action_groups"][0], "state": state}

                def helper(path, args):
                    content = m.yaml.safe_load(Path(path).read_text())
                    self.assertEqual(content, registry)
                    self.assertEqual(Path(path).stat().st_mode & 0o777, 0o440)
                    self.assertEqual(Path(path).parent.stat().st_mode & 0o777, 0o750)
                    if args == ["show-box-configs"]:
                        return Mock(returncode=0, stdout=json.dumps({"registry_path": str(path), "registry_sha256": m.sha256_file(Path(path)), "config": content}))
                    if args[-1] == "show-box-access-vars":
                        return Mock(returncode=0, stdout='{"router":"same"}')
                    self.assertEqual(args, ["--box", "boxa", "--magicdns-suffix", "example.ts.net", "verify-box-access"])
                    self.assertEqual(m.AUTHORITY_POINTER.read_text(), state["authority_state_sha256"] + "\n")
                    live.append(path)
                    if len(live) == 2:
                        self.assertEqual(len(list(m.NONCE_ROOT.iterdir())), 1)
                    return Mock(returncode=1 if outcome == "verification-failed" and len(live) == 2 else 0,
                                stdout="Controller-to-router probe used DERP. Access checks passed." if outcome == "derp" else "",
                                stderr="[ERROR]: verification failed")

                stack.enter_context(patch.object(m, "validate_inputs_v3", side_effect=validate))
                stack.enter_context(patch.object(m, "run_platform_resources_as_controller", side_effect=helper))
                with redirect_stdout(io.StringIO()) as preparation:
                    m.box_preflight(Mock())
                intent = json.loads(preparation.getvalue())
                m.validate_controller_box_intent(intent)
                key = root / "synthetic-approval-key"
                subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
                allowed = root / "allowed-signers"
                allowed.write_text(m.SIGNER_ID + " " + key.with_suffix(".pub").read_text())
                stack.enter_context(patch.object(m, "ALLOWED_SIGNERS", allowed))
                signed_path = root / "signed-intent.json"
                signed_path.write_text(m.canonical(intent) + "\n")
                subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", m.SIGNATURE_NAMESPACE, str(signed_path)], check=True, capture_output=True)
                signed_args = Mock(signer_id=m.SIGNER_ID, approval_signature=str(signed_path) + ".sig")
                archive = m.PREFLIGHT_ROOT / intent["nonce"]
                self.assertEqual(archive.stat().st_mode & 0o777, 0o700)
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in archive.iterdir()))
                if outcome == "publication-failed":
                    stack.enter_context(patch.object(m, "write_authority_v2", side_effect=m.ApplyError("publication failed")))
                if outcome == "receipt-failed":
                    stack.enter_context(patch.object(m, "store_box_execution", side_effect=OSError("disk full")))
                with redirect_stdout(io.StringIO()) as execution:
                    if outcome in {"verification-failed", "publication-failed", "receipt-failed", "changed-after-check"}:
                        with self.assertRaisesRegex(m.ApplyError, "incomplete" if outcome == "receipt-failed" else "before publication"):
                            m.controller_box_execute(signed_args, intent)
                    else:
                        m.controller_box_execute(signed_args, intent)
                        result = json.loads(execution.getvalue())
                        receipt = m.load_json(result["receipt_path"], canonical_stored=True)
                        self.assertEqual(receipt["executor"], m.CONTROLLER_BOX_EXECUTOR)
                        self.assertEqual(receipt["result"], "verified" if outcome == "verify" else "success")
                        self.assertEqual(Path(result["receipt_path"]).stat().st_mode & 0o777, 0o440)
                active = m.AUTHORITY_POINTER.read_text().strip()
                if outcome in {"direct", "derp", "receipt-failed"}:
                    after = m.load_json(m.AUTHORITY_ROOT / f"{active}.json")
                    self.assertEqual(after["prior_state_sha256"], state["authority_state_sha256"])
                    self.assertEqual(after["setting_groups"][1:], state["setting_groups"][1:])
                    self.assertEqual(after["setting_groups"][0]["source"], "instance_specification_v1")
                else:
                    self.assertEqual(active, state["authority_state_sha256"])
                self.assertEqual(list(m.BOX_RUNTIME_ROOT.iterdir()), [])
                self.assertEqual(len(live), 2)
                with self.assertRaisesRegex(m.ApplyError, "nonce was already used"):
                    m.controller_box_execute(signed_args, intent)
                self.assertEqual(len(live), 2)
                self.assertEqual(m.sha256_file(old), plan["compatibility_inputs"][1]["sha256"])


if __name__ == "__main__":
    unittest.main()
