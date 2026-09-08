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

import test_registry_source as registry_fixture


class InventorySourceTest(unittest.TestCase):
    def setUp(self):
        self.registry = registry_fixture.RegistrySourceTest()
        self.registry.setUp()
        self.identity = self.registry.identity
        self.m, self.pair = self.registry.m, self.registry.pair

    def scopes(self):
        return sorted([self.m.INVENTORY_SCOPE, self.m.RUNNER_SCOPE_PREFIX + "boxa-ops-airunner"])

    def state(self, adopted=False):
        m = self.m
        state = self.registry.state(True)
        if adopted:
            state.update(kind=m.KIND_AUTHORITY_V5, schema_version=5, prior_state_kind=m.KIND_AUTHORITY_V4,
                         inventory_adoption={"nonce": state["transition_id"], "intent_sha256": state["signed_intent_sha256"], "plan_sha256": "a" * 64})
            state["setting_groups"].append({"id": m.INVENTORY_GROUP, "scopes": self.scopes(), "source": "instance_specification_v1"})
            state["setting_groups"].sort(key=lambda g: g["id"])
        state["authority_state_sha256"] = m.authority_v2_hash(state)
        return state

    def plan(self, adopted=False):
        m = self.m
        plan = self.registry.plan(True)
        plan.update(kind=m.KIND_PLAN_V7, schema_version=7, migration_target="inventory")
        plan["authority_state"] = {k: v for k,v in self.state(adopted).items() if k in {"kind", "setting_groups", "authority_state_sha256"}}
        plan["actions"] = [a for a in plan["actions"] if not a["scope"].startswith(m.RUNNER_SCOPE_PREFIX)]
        for group in plan["action_groups"]: group["executor"] = "none"
        for action in plan["actions"]: action["executor"] = "none"
        graph = {"all": {"children": ["ops"]}, "ops": {"hosts": ["boxa-ops", "boxb-ops"]}, "_meta": {"hostvars": {b+"-ops": {"ops_airunner_enabled": b == "boxa"} for b in ("boxa", "boxb")}}}
        plan["inventory"] = {"inventory": graph, "inventory_sha256": m.inventory_digest(graph), "boxes": ["boxa", "boxb"], "airunners": ["boxa-ops-airunner"], "scopes": self.scopes()}
        operation = "verify_instance_authority" if adopted else "adopt_instance_specification"
        plan["action_groups"].append({"id": m.INVENTORY_GROUP, "operation": operation, "scopes": self.scopes(), "executor": m.INVENTORY_EXECUTOR, "rollback_type": "no_mutation"})
        plan["action_groups"].sort(key=lambda g: g["id"])
        for scope in self.scopes():
            before = "instance_specification_v1" if adopted else "legacy_engine_inventory" if scope == m.INVENTORY_SCOPE else "none"
            action = {"id": scope, "scope": scope, "operation": operation, "executor": m.INVENTORY_EXECUTOR, "authority_before": before, "authority_after": "instance_specification_v1", "preconditions": m.INVENTORY_PRECONDITIONS, "rollback": {"strategy": "no_mutation", "authority": before}}
            if scope != m.INVENTORY_SCOPE: action["finding_id"] = scope
            plan["actions"].append(action)
        return plan

    def prepare_comparison(self, work, plan):
        m = self.m
        result = m.prepare_complete_registry_comparison(work, plan)
        # Runtime files are real; external Ansible execution is covered separately.
        value = m.normalize_execution_inventory(plan["inventory"]["inventory"], ["boxa-ops", "boxb-ops"])
        for name in ("old", "effective"):
            path = work / (name + "-inventory.json")
            path.write_text(m.canonical(value) + "\n")
            path.chmod(0o640)
            result[name + "_inventory_path"] = str(path)
            result[name + "_inventory_sha256"] = m.inventory_digest(value)
        result.update(inventory_projection_sha256=m.inventory_digest(plan["inventory"]), inventory_policy_sha256="a"*64, legacy_inventory_sha256="b"*64, airunners=plan["inventory"]["airunners"])
        return result

    def test_closed_plan_versions_scope_and_authority(self):
        m = self.m
        for adopted in (False, True):
            m.validate_authority_current_document(self.state(adopted), ["boxa", "boxb"])
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = self.identity.base.fixture.store_plan(root, self.plan(adopted))
                with patch.object(m, "PLAN_ROOT", root): m.verify_plan_v3(path)
        for change in (
            lambda p: p.update(kind=m.KIND_PLAN_V6, schema_version=6),
            lambda p: p.update(migration_target="connectivity"),
            lambda p: p["inventory"]["scopes"].pop(),
            lambda p: p["inventory"]["airunners"].append("other-ops-airunner"),
            lambda p: p["projection"]["control_plane"]["active_controller"].update(hostname="wrong-ops"),
            lambda p: p["actions"][-1].update(command="id"),
            lambda p: p["actions"][-1].update(executor="shell"),
            lambda p: p["actions"][-1].update(operation="apply-box-access"),
            lambda p: p["actions"][-1].update(operation="rollback_to_legacy"),
        ):
            plan = copy.deepcopy(self.plan())
            change(plan)
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = self.identity.base.fixture.store_plan(root, plan)
                with patch.object(m, "PLAN_ROOT", root), self.assertRaises(m.ApplyError): m.verify_plan_v3(path)
        for state in (self.identity.state(), self.identity.state(True), self.registry.state(True)):
            state["inventory_adoption"] = None
            state["authority_state_sha256"] = m.authority_v2_hash(state)
            with self.assertRaises(m.ApplyError): m.validate_authority_current_document(state)

    def test_runner_evidence_is_live_closed_and_ordered(self):
        m = self.m
        peers = {"a": {"HostName": "boxa-ops-airunner", "ID": "a", "Online": True, "Tags": ["tag:airunner"]}, "b": {"HostName": "cloud-ops", "ID": "b", "Online": True, "Tags": ["tag:infra"]}}
        def reply(value): return Mock(returncode=0, stdout=json.dumps({"Peer": value}))
        with patch.object(m, "run_plan_as_controller", return_value=reply(peers)) as command:
            result = m.verify_declared_runners(["cloud-ops", "boxa-ops-airunner"])
            self.assertEqual([v["hostname"] for v in result], ["cloud-ops", "boxa-ops-airunner"])
            command.assert_called_once_with(["tailscale", "status", "--json"])
        for change in ("offline", "wrong-tag", "duplicate", "missing", "missing-id"):
            bad = copy.deepcopy(peers)
            if change == "offline": bad["a"]["Online"] = False
            elif change == "wrong-tag": bad["a"]["Tags"] = ["tag:ops"]
            elif change == "duplicate": bad["c"] = bad["a"]
            elif change == "missing": del bad["a"]
            else: del bad["a"]["ID"]
            with patch.object(m, "run_plan_as_controller", return_value=reply(bad)), self.assertRaises(m.ApplyError): m.verify_declared_runners(["boxa-ops-airunner"])

    def test_signed_files_verification_commands_nonce_publication_and_cleanup(self):
        m = self.m
        for outcome in ("adopt-direct", "adopt-derp", "verify", "router-failed", "wrong-peer", "unknown-reply", "input-changed", "runner-changed", "concurrent", "publication-failed", "publication-incomplete", "receipt-failed"):
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
                    _, checked_plan, group = m.verify_plan_v3(plan_path)
                    return {**binding, **self.prepare_comparison(work, checked_plan), "state": state, "plan": checked_plan, "group": group}
                stack.enter_context(patch.object(m, "validate_inputs_v3", side_effect=validate))
                stack.enter_context(patch.object(m, "verify_controller_pair", return_value=self.identity.roles()))
                stack.enter_context(patch.object(m, "verify_declared_runners", side_effect=lambda _: [{"hostname":"boxa-ops-airunner", "id":"changed" if executing and outcome == "runner-changed" else "test-runner", "online":True, "tags":["tag:airunner"]}]))
                with redirect_stdout(io.StringIO()) as stdout: m.inventory_preflight(Mock())
                intent = json.loads(stdout.getvalue())
                for change in ({"action": "rollback_to_legacy"}, {"command": "id"}, {"action_set": self.scopes()[:-1]}, {"expires_at": "2000-01-01T00:00:00Z"}):
                    with self.assertRaises(m.ApplyError): m.validate_inventory_intent({**intent, **change})
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
                if outcome == "publication-incomplete":
                    publish = m.publish_authority
                    def partial_publication(value):
                        publish(value)
                        raise OSError("failure after pointer publication")
                    stack.enter_context(patch.object(m, "publish_authority", side_effect=partial_publication))
                if outcome == "receipt-failed": stack.enter_context(patch.object(m, "store_identity_receipt", side_effect=OSError("disk full")))
                with redirect_stdout(io.StringIO()) as stdout:
                    if outcome in ("adopt-direct", "adopt-derp", "verify"):
                        m.inventory_execute(args, intent)
                        result = json.loads(stdout.getvalue())
                        self.assertEqual(result["result"], "verified" if outcome == "verify" else "success")
                        self.assertEqual(Path(result["receipt_path"]).stat().st_mode & 0o777, 0o440)
                    else:
                        with self.assertRaisesRegex(m.ApplyError, "incomplete" if outcome in {"receipt-failed", "publication-incomplete"} else "before publication"): m.inventory_execute(args, intent)
                active = m.AUTHORITY_POINTER.read_text().strip()
                if outcome in ("adopt-direct", "adopt-derp", "receipt-failed", "publication-incomplete"):
                    after = m.load_json(m.AUTHORITY_ROOT / (active + ".json"))
                    m.validate_authority_current_document(after)
                    self.assertEqual([g for g in after["setting_groups"] if g["id"] != m.INVENTORY_GROUP], state["setting_groups"])
                    self.assertEqual(after["controller_identity_adoption"], state["controller_identity_adoption"])
                    m.verify_inventory_anchor(after, self.scopes())
                    with self.assertRaises(m.ApplyError): m.verify_inventory_anchor(after, self.scopes()[:-1])
                    self.assertEqual(after["registry_adoption"], state["registry_adoption"])
                    if outcome == "adopt-direct": self.check_current_reader(stack, root, after, plan)
                else:
                    self.assertEqual(active, "9" * 64 if outcome == "concurrent" else state["authority_state_sha256"])
                self.assertEqual(list(m.BOX_RUNTIME_ROOT.iterdir()), [])
                self.assertEqual(len(list(m.NONCE_ROOT.iterdir())), 1)
                before = len(calls)
                with self.assertRaisesRegex(m.ApplyError, "nonce was already used"): m.inventory_execute(args, intent)
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
        rendered = {"schema_version":1,"kind":"klokast.inventory.v1","valid":True,"engine":{"repository":m.ENGINE_REPOSITORY,"ref":"main","commit":plan["engine"]["commit"]},"repository":{"branch":"main","clean":True,"head_commit":"c"*40,"reasons":[]},"inputs":plan["inputs"],"projection":plan["inventory"],"diagnostics":[]}
        with patch.object(m,"run_plan_as_controller",return_value=Mock(returncode=0,stdout=json.dumps(rendered))) as command:
            result = m.inventory_source_status()
            self.assertEqual(result["source"],"instance_specification_v1")
            self.assertEqual(result["rendered"],rendered)
            self.assertTrue(all(call.args[0][1:3] == ["inventory","--instance"] for call in command.call_args_list))
        dirty = copy.deepcopy(rendered)
        dirty["repository"]["clean"] = False
        with patch.object(m,"run_plan_as_controller",return_value=Mock(returncode=0,stdout=json.dumps(dirty))), self.assertRaises(m.ApplyError): m.inventory_source_status()
        changed = copy.deepcopy(rendered)
        changed["inputs"] = []
        with patch.object(m,"run_plan_as_controller",side_effect=[Mock(returncode=0,stdout=json.dumps(rendered)),Mock(returncode=0,stdout=json.dumps(changed))]), self.assertRaisesRegex(m.ApplyError,"inputs or source changed"): m.inventory_source_status()


if __name__ == "__main__": unittest.main()
