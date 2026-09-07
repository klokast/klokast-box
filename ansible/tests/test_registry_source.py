"""Closed registry adoption, signed execution, and effective-source boundaries."""
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import test_controller_identity_source as identity_fixture


class RegistrySourceTest(unittest.TestCase):
    def setUp(self):
        self.identity = identity_fixture.ControllerIdentitySourceTest()
        self.identity.setUp()
        self.m = self.identity.m
        self.pair = self.identity.pair

    def scopes(self):
        return sorted([f"boxes.{box}.{field}" for box in ("boxa", "boxb") for field in ("dhcp_reservations", "dom0_bridge_ports", "shared_guests")] + ["apps.saved.enabled", "apps.saved.placement"])

    def state(self, adopted=False):
        m = self.m
        state = self.identity.state(True)
        if adopted:
            state.update(kind=m.KIND_AUTHORITY_V4, schema_version=4, prior_state_kind=m.KIND_AUTHORITY_V3,
                         registry_adoption={"nonce": state["transition_id"], "intent_sha256": state["signed_intent_sha256"], "plan_sha256": "a" * 64})
            state["setting_groups"].append({"id": m.REGISTRY_GROUP, "scopes": self.scopes(), "source": "instance_specification_v1"})
            state["setting_groups"].sort(key=lambda g: g["id"])
        state["authority_state_sha256"] = m.authority_v2_hash(state)
        return state

    def plan(self, adopted=False):
        m = self.m
        plan = self.identity.plan(True)
        plan.update(kind=m.KIND_PLAN_V6, schema_version=6, migration_target="registry")
        registry = {"schema_version": 1, "boxes": {box: {"access": m.instance_box_access(plan, box)} for box in ("boxa", "boxb")}, "apps": {"saved": {"enabled": False, "placement": {"builder_box": ""}}}}
        plan["projection"]["registry"] = {"registry": registry, "registry_sha256": m.sha256_bytes(m.canonical(registry).encode()), "scopes": self.scopes()}
        plan["authority_state"] = {k: v for k, v in self.state(adopted).items() if k in {"kind", "setting_groups", "authority_state_sha256"}}
        for group in plan["action_groups"]:
            group["executor"] = "none"
            if group["id"] == m.TAILNET_GROUP_ID: group["rollback_type"] = "no_mutation"
        for action in plan["actions"]: action["executor"] = "none"
        operation = "verify_instance_authority" if adopted else "adopt_instance_specification"
        group = {"id": m.REGISTRY_GROUP, "operation": operation, "scopes": self.scopes(), "executor": m.REGISTRY_EXECUTOR, "rollback_type": "no_mutation"}
        plan["action_groups"].append(group)
        plan["action_groups"].sort(key=lambda g: g["id"])
        before = "instance_specification_v1" if adopted else m.LEGACY_REGISTRY_SOURCE
        for scope in self.scopes():
            plan["actions"].append({"id": scope, "finding_id": scope, "scope": scope, "operation": operation, "executor": m.REGISTRY_EXECUTOR, "authority_before": before, "authority_after": "instance_specification_v1", "preconditions": m.REGISTRY_PRECONDITIONS, "rollback": {"strategy": "no_mutation", "authority": before}})
        return plan

    def test_closed_plans_and_authority_versions(self):
        m = self.m
        for adopted in (False, True):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = self.identity.base.fixture.store_plan(root, self.plan(adopted))
                with patch.object(m, "PLAN_ROOT", root):
                    _, _, group = m.verify_plan_v3(path)
                self.assertEqual(group["executor"], m.REGISTRY_EXECUTOR)
            m.validate_authority_current_document(self.state(adopted), ["boxa", "boxb"])
        for change in (
            lambda p: p.update(kind=m.KIND_PLAN_V5, schema_version=5),
            lambda p: p.update(migration_target="connectivity"),
            lambda p: p["projection"]["control_plane"]["active_controller"].update(hostname="wrong-ops"),
            lambda p: p["actions"][-1].update(operation="apply-box-access"),
            lambda p: p["actions"][-1].update(scope="apps.saved.command"),
            lambda p: p["actions"][-1].update(executor="shell"),
            lambda p: p["actions"][-1].update(command="id"),
            lambda p: p["action_groups"][-2]["scopes"].pop(),
            lambda p: p["authority_state"]["setting_groups"][0].update(source=m.LEGACY_REGISTRY_SOURCE),
        ):
            plan = copy.deepcopy(self.plan())
            change(plan)
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = self.identity.base.fixture.store_plan(root, plan)
                with patch.object(m, "PLAN_ROOT", root), self.assertRaises(m.ApplyError): m.verify_plan_v3(path)
        for state in (self.identity.state(), self.identity.state(True)):
            state["registry_adoption"] = None
            state["authority_state_sha256"] = m.authority_v2_hash(state)
            with self.assertRaises(m.ApplyError): m.validate_authority_current_document(state)
        with self.assertRaisesRegex(m.ApplyError, "recovery design"):
            m.make_authority_v2(self.state(True), {"nonce": "later-source-change"}, group_id=m.BOX_GROUP_PREFIX + "boxb", source=m.LEGACY_REGISTRY_SOURCE)

    def test_signed_files_verification_commands_nonce_publication_and_cleanup(self):
        m = self.m
        for outcome in ("adopt-direct", "adopt-derp", "verify", "router-failed", "wrong-peer", "unknown-reply", "input-changed", "concurrent", "publication-failed", "receipt-failed"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary)
                stack.callback(os.umask, os.umask(0o077))
                for name in ("PREFLIGHT_ROOT", "BOX_RUNTIME_ROOT", "NONCE_ROOT", "EXECUTION_ROOT", "AUTHORITY_ROOT", "PLAN_ROOT"):
                    stack.enter_context(patch.object(m, name, root / name))
                stack.enter_context(patch.object(m, "AUTHORITY_POINTER", root / "active"))
                stack.enter_context(patch.object(m, "smith_gid", return_value=os.getgid()))
                stack.enter_context(patch.object(m.os, "chown"))
                self.identity.base.real_fstat = os.fstat
                stack.enter_context(patch.object(m.os, "fstat", side_effect=self.identity.base.root_metadata))
                stack.enter_context(patch.object(m, "append_audit"))
                state = self.state(outcome == "verify")
                m.AUTHORITY_ROOT.mkdir()
                state_path = m.AUTHORITY_ROOT / (state["authority_state_sha256"] + ".json")
                state_path.write_text(m.canonical(state) + "\n")
                m.AUTHORITY_POINTER.write_text(state["authority_state_sha256"] + "\n")
                plan = self.plan(outcome == "verify")
                config = root / "controller-ha.yml"
                config.write_text(m.yaml.safe_dump(dict(schema_version=1, remote_user="smith", repo_dir="~/src/klokast/klokast-box", controllers=list(self.pair.values()))))
                registry = root / "registry.yml"
                registry.write_text(m.yaml.safe_dump(plan["projection"]["registry"]["registry"]))
                stack.enter_context(patch.object(m, "CONTROLLER_HA", config))
                stack.enter_context(patch.object(m, "REGISTRY", registry))
                for value in plan["compatibility_inputs"]:
                    if value["name"] == "legacy_controller_ha": value["sha256"] = m.sha256_file(config)
                    if value["name"] == m.LEGACY_REGISTRY_SOURCE: value["sha256"] = m.sha256_file(registry)
                m.PLAN_ROOT.mkdir()
                plan_path = self.identity.base.fixture.store_plan(m.PLAN_ROOT, plan)
                plan = m.load_json(plan_path)
                binding = {k: str(root / k) for k in ("plan_path", "toolchain_path", "source_path", "observation", "build_dir")}
                binding.update(authority_path=str(state_path), recovery_path=str(root / ("4" * 64 + ".json")), binary_sha256="5" * 64, builder_receipt_sha256="6" * 64)
                binding["plan_path"] = str(plan_path)
                executing = False
                calls = []
                def compiler(path, argv):
                    calls.append(argv)
                    self.assertEqual(Path(path).stat().st_mode & 0o777, 0o440)
                    self.assertEqual(Path(path).parent.stat().st_mode & 0o777, 0o750)
                    command = argv[-1]
                    self.assertIn(command, {"show", "show-box-configs", "show-box-access-vars", "verify-box-access"})
                    if command == "verify-box-access":
                        if executing and outcome in {"router-failed", "wrong-peer", "unknown-reply"}:
                            return Mock(returncode=1, stdout="", stderr=outcome)
                        notice = "Controller-to-router probe used DERP. Access checks passed." if outcome == "adopt-derp" else "verified direct"
                        return Mock(returncode=0, stdout=notice, stderr="")
                    data = m.yaml.safe_load(Path(path).read_text())
                    if command == "show-box-access-vars": value = {"router": argv[1], "settings": data["boxes"][argv[1]]}
                    else: value = {"registry_path": str(path), "registry_sha256": m.sha256_file(Path(path)), "compiled": data, "mode": command}
                    return Mock(returncode=0, stdout=json.dumps(value), stderr="")
                stack.enter_context(patch.object(m, "run_platform_resources_as_controller", side_effect=compiler))
                validations = []
                def validate(args, work):
                    validations.append(work)
                    if executing and outcome == "input-changed": raise m.ApplyError("private input changed")
                    return {**binding, **m.prepare_complete_registry_comparison(work, plan), "state": state, "plan": plan, "group": next(g for g in plan["action_groups"] if g["id"] == m.REGISTRY_GROUP)}
                stack.enter_context(patch.object(m, "validate_inputs_v3", side_effect=validate))
                stack.enter_context(patch.object(m, "verify_controller_pair", return_value=self.identity.roles()))
                with redirect_stdout(io.StringIO()) as stdout: m.registry_preflight(Mock())
                intent = json.loads(stdout.getvalue())
                for change in ({"action": "rollback_to_legacy"}, {"command": "id"}, {"action_set": self.scopes()[:-1]}, {"expires_at": "2000-01-01T00:00:00Z"}):
                    with self.assertRaises(m.ApplyError): m.validate_registry_intent({**intent, **change})
                archive = m.PREFLIGHT_ROOT / intent["nonce"]
                self.assertEqual(archive.stat().st_mode & 0o777, 0o700)
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in archive.iterdir()))
                key = root / "test-signing-key"
                subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
                allowed = root / "allowed-signers"
                allowed.write_text(m.SIGNER_ID + " " + key.with_suffix(".pub").read_text())
                stack.enter_context(patch.object(m, "ALLOWED_SIGNERS", allowed))
                signed = root / "intent.json"
                signed.write_text(m.canonical(intent) + "\n")
                subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", m.SIGNATURE_NAMESPACE, str(signed)], check=True, capture_output=True)
                args = Mock(signer_id=m.SIGNER_ID, approval_signature=str(signed) + ".sig")
                executing = True
                if outcome == "concurrent": m.AUTHORITY_POINTER.write_text("9" * 64 + "\n")
                if outcome == "publication-failed": stack.enter_context(patch.object(m, "publish_authority", side_effect=OSError("disk full")))
                if outcome == "receipt-failed": stack.enter_context(patch.object(m, "store_identity_receipt", side_effect=OSError("disk full")))
                with redirect_stdout(io.StringIO()) as stdout:
                    if outcome in ("adopt-direct", "adopt-derp", "verify"):
                        m.registry_execute(args, intent)
                        result = json.loads(stdout.getvalue())
                        self.assertEqual(result["result"], "verified" if outcome == "verify" else "success")
                        self.assertEqual(Path(result["receipt_path"]).stat().st_mode & 0o777, 0o440)
                    else:
                        with self.assertRaisesRegex(m.ApplyError, "incomplete" if outcome == "receipt-failed" else "before publication"): m.registry_execute(args, intent)
                active = m.AUTHORITY_POINTER.read_text().strip()
                if outcome in ("adopt-direct", "adopt-derp", "receipt-failed"):
                    after = m.load_json(m.AUTHORITY_ROOT / (active + ".json"))
                    m.validate_authority_current_document(after)
                    self.assertEqual([g for g in after["setting_groups"] if g["id"] != m.REGISTRY_GROUP], state["setting_groups"])
                    self.assertEqual(after["controller_identity_adoption"], state["controller_identity_adoption"])
                    m.verify_registry_anchor(after, self.scopes())
                    with self.assertRaises(m.ApplyError): m.verify_registry_anchor(after, self.scopes()[:-1])
                    if outcome == "adopt-direct":
                        self.check_current_reader(stack, root, after, plan)
                else:
                    self.assertEqual(active, "9" * 64 if outcome == "concurrent" else state["authority_state_sha256"])
                self.assertEqual(list(m.BOX_RUNTIME_ROOT.iterdir()), [])
                self.assertEqual(len(list(m.NONCE_ROOT.iterdir())), 1)
                before = len(calls)
                with self.assertRaisesRegex(m.ApplyError, "nonce was already used"): m.registry_execute(args, intent)
                self.assertEqual(len(calls), before)

    def check_current_reader(self, stack, root, state, plan):
        m = self.m
        controller = {"schema_version":1, "kind":"klokast.controller-identity-status.v1", "source":"instance_specification_v1", "authority_state_sha256":state["authority_state_sha256"], "engine_commit":plan["engine"]["commit"], "controllers":self.pair}
        stack.enter_context(patch.object(m,"controller_identity_status",return_value=controller))
        builds = root / "builds"
        directory = builds / plan["engine"]["commit"] / "123456789abc"
        directory.mkdir(parents=True)
        stack.enter_context(patch.object(m,"BUILD_ROOT",builds))
        stack.enter_context(patch.object(m,"resolve_build_directory",return_value=(directory,plan["engine"]["commit"])))
        stack.enter_context(patch.object(m,"verify_build_directory",return_value=({"binary_sha256":"b"*64},directory/"klokast")))
        stack.enter_context(patch.object(m,"verify_binary_version"))
        rendered = {"schema_version":1,"kind":"klokast.registry.v1","valid":True,"engine":{"repository":m.ENGINE_REPOSITORY,"ref":"main","commit":plan["engine"]["commit"]},"repository":{"branch":"main","clean":True,"head_commit":"c"*40,"reasons":[]},"inputs":plan["inputs"],"projection":plan["projection"]["registry"],"diagnostics":[]}
        with patch.object(m,"run_plan_as_controller",return_value=Mock(returncode=0,stdout=json.dumps(rendered))) as command:
            result = m.registry_source_status()
            self.assertEqual(result["source"],"instance_specification_v1")
            self.assertEqual(result["rendered"],rendered)
            self.assertTrue(all(call.args[0][1:3] == ["registry","--instance"] for call in command.call_args_list))
        dirty = copy.deepcopy(rendered)
        dirty["repository"]["clean"] = False
        with patch.object(m,"run_plan_as_controller",return_value=Mock(returncode=0,stdout=json.dumps(dirty))), self.assertRaises(m.ApplyError): m.registry_source_status()
        changed = copy.deepcopy(rendered)
        changed["inputs"] = []
        with patch.object(m,"run_plan_as_controller",side_effect=[Mock(returncode=0,stdout=json.dumps(rendered)),Mock(returncode=0,stdout=json.dumps(changed))]), self.assertRaisesRegex(m.ApplyError,"inputs or source changed"): m.registry_source_status()


if __name__ == "__main__": unittest.main()
