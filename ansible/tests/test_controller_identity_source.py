"""Controller identity source boundaries and real-file signed execution."""
import copy
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import Mock, patch

import test_controller_box_source as box_fixture


class ControllerIdentitySourceTest(unittest.TestCase):
    def setUp(self):
        self.base = box_fixture.ControllerBoxSourceTest()
        self.base.setUp()
        self.m = self.base.mod
        self.pair = {role: {"box": box, "hostname": box + "-ops"} for role, box in (("active", "boxa"), ("standby", "boxb"))}

    def state(self, adopted=False):
        m = self.m
        state = self.base.state(True)
        if adopted:
            state.update(kind=m.KIND_AUTHORITY_V3, schema_version=3, prior_state_kind=m.KIND_AUTHORITY_V2)
            state["controller_identity_adoption"] = {"nonce": state["transition_id"], "intent_sha256": state["signed_intent_sha256"], "plan_sha256": "a" * 64}
            state["setting_groups"].append({"id": m.IDENTITY_GROUP, "scopes": m.IDENTITY_SCOPES, "source": "instance_specification_v1"})
            state["setting_groups"].sort(key=lambda x: x["id"])
        state["authority_state_sha256"] = m.authority_v2_hash(state)
        return state

    def plan(self, adopted=False):
        m = self.m
        p = self.base.plan(True)
        p.update(kind=m.KIND_PLAN_V5, schema_version=5, migration_target="controller-identity")
        p["projection"]["control_plane"]["standby_controller"] = {"box_id": "boxb", "hostname": "boxb-ops"}
        p["authority_state"] = {k: v for k, v in self.state(adopted).items() if k in {"kind", "setting_groups", "authority_state_sha256"}}
        for g in p["action_groups"]:
            if "box" in g:
                g.update(executor="none", rollback_type=m.BOX_ROLLBACK_TYPE)
        for a in p["actions"]:
            a["executor"] = "none"
        operation = "verify_instance_authority" if adopted else "adopt_instance_specification"
        group = {"id": m.IDENTITY_GROUP, "scopes": m.IDENTITY_SCOPES, "operation": operation, "executor": m.IDENTITY_EXECUTOR, "rollback_type": "no_mutation"}
        p["action_groups"].append(group)
        p["action_groups"].sort(key=lambda g: g["id"])
        for scope in m.IDENTITY_SCOPES:
            before = "instance_specification_v1" if adopted else ("controller_ha_markers" if scope.startswith("deployment.") else "legacy_controller_ha")
            p["actions"].append(dict(id=scope, finding_id=scope, scope=scope, executor=m.IDENTITY_EXECUTOR, operation=operation,
                authority_before=before, authority_after="instance_specification_v1", rollback={"strategy": "no_mutation", "authority": before},
                preconditions=["configured_controller_pair", "exact_plan_v5_revalidated", "equal_controller_configuration", "roles_verified_before_source_publication"]))
        return p

    def roles(self):
        return {role: {"schema_version": 1, "configured": True, "hostname": item["hostname"], "box": item["box"],
                "role": role, "active": role == "active", "active_box": "boxa", "updated_at": "2026-08-01T00:00:00Z"}
                for role, item in self.pair.items()}

    def test_plan_and_state_are_closed_and_versioned(self):
        m = self.m
        for adopted in (False, True):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                p = self.plan(adopted)
                path = self.base.fixture.store_plan(root, p)
                with patch.object(m, "PLAN_ROOT", root):
                    _, _, group = m.verify_plan_v3(path)
                self.assertEqual(group["executor"], m.IDENTITY_EXECUTOR)
                m.validate_authority_current_document(self.state(adopted), ["boxa", "boxb"])
        for mutation in (
            lambda p: p.update(schema_version=4, kind=m.KIND_PLAN_V4),
            lambda p: p["projection"]["control_plane"].pop("standby_controller"),
            lambda p: p["action_groups"][2]["scopes"].pop(),
            lambda p: p["actions"][-1].update(executor=m.BOX_EXECUTOR),
            lambda p: p["actions"][-1].update(authority_before="legacy_deployment"),
            lambda p: p["action_groups"][2].update(operation="promote"),
            lambda p: p["authority_state"]["setting_groups"][0].update(source=m.LEGACY_REGISTRY_SOURCE),
        ):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                p = copy.deepcopy(self.plan())
                mutation(p)
                path = self.base.fixture.store_plan(root, p)
                with patch.object(m, "PLAN_ROOT", root), self.assertRaises(m.ApplyError):
                    m.verify_plan_v3(path)
        state = self.state(True)
        with self.assertRaises(m.ApplyError): m.validate_authority_v2_document(state)
        later = m.make_authority_v2(state, {"nonce": "later-connectivity-request"}, group_id=m.BOX_GROUP_PREFIX + "boxb", source=m.LEGACY_REGISTRY_SOURCE)
        self.assertEqual(later["controller_identity_adoption"], state["controller_identity_adoption"])
        self.assertEqual(later["kind"], m.KIND_AUTHORITY_V3)
        later["controller_identity_adoption"]["extra"] = True
        later["authority_state_sha256"] = m.authority_v2_hash(later)
        with self.assertRaises(m.ApplyError): m.validate_authority_current_document(later)

    def test_role_checks_are_read_only_and_reject_missing_wrong_or_unknown_peers(self):
        m = self.m
        for failure in (None, "unreachable", "wrong-peer", "missing-marker", "two-active", "unknown-reply", "wrong-active-box"):
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                role = "active" if len(calls) == 1 else "standby"
                status = dict(self.roles()[role], checked_at=m.format_utc(m.now_utc()))
                host = self.pair[role]["hostname"]
                if role == "standby":
                    if failure == "unreachable": return Mock(returncode=1, stdout="")
                    if failure == "wrong-peer": host = "wrong-ops"
                    if failure == "missing-marker": status.update(configured=False, active=True, role="legacy-unconfigured")
                    if failure == "two-active": status.update(role="active", active=True)
                    if failure == "unknown-reply": return Mock(returncode=0, stdout="unknown")
                    if failure == "wrong-active-box": status["active_box"] = "boxb"
                self.assertEqual(kwargs["input_text"], "set -eu\nhostname\n/usr/local/sbin/klokast-controller-guard --status --json\n")
                return Mock(returncode=0, stdout=host + "\n" + json.dumps(status) + "\n")
            with patch.object(m, "run", side_effect=run):
                if failure:
                    with self.assertRaises(m.ApplyError): m.verify_controller_pair(self.pair)
                else:
                    self.assertEqual(m.verify_controller_pair(self.pair), self.roles())
            self.assertEqual(calls[0], [m.DOAS, "-u", "smith", "sh", "-s"])
            self.assertEqual(calls[1], [m.DOAS, "-u", "smith", "timeout", "20", "tailscale", "ssh", "smith@boxb-ops", "sh", "-s"])

    def test_signed_files_nonce_publication_receipt_and_cleanup(self):
        m = self.m
        for outcome in ("adopt", "verify", "roles-failed", "input-changed", "concurrent", "receipt-failed"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary)
                old_mask = os.umask(0o077)
                stack.callback(os.umask, old_mask)
                for name in ("PREFLIGHT_ROOT", "BOX_RUNTIME_ROOT", "NONCE_ROOT", "EXECUTION_ROOT", "AUTHORITY_ROOT"):
                    stack.enter_context(patch.object(m, name, root / name))
                stack.enter_context(patch.object(m, "AUTHORITY_POINTER", root / "active"))
                stack.enter_context(patch.object(m, "smith_gid", return_value=os.getgid()))
                stack.enter_context(patch.object(m.os, "chown"))
                self.base.real_fstat = os.fstat
                stack.enter_context(patch.object(m.os, "fstat", side_effect=self.base.root_metadata))
                stack.enter_context(patch.object(m, "append_audit"))
                forbidden = stack.enter_context(patch.object(m, "run_box_resource", side_effect=AssertionError("identity executor called a router command")))
                state = self.state(outcome == "verify")
                m.AUTHORITY_ROOT.mkdir()
                state_path = m.AUTHORITY_ROOT / (state["authority_state_sha256"] + ".json")
                state_path.write_text(m.canonical(state) + "\n")
                m.AUTHORITY_POINTER.write_text(state["authority_state_sha256"] + "\n")
                config = root / "controller-ha.yml"
                config.write_text(m.yaml.safe_dump(dict(schema_version=1, remote_user="smith", repo_dir="~/src/klokast/klokast-box", controllers=[self.pair["active"], self.pair["standby"]])))
                stack.enter_context(patch.object(m, "CONTROLLER_HA", config))
                plan = self.plan(outcome == "verify")
                plan["plan_sha256"] = "3" * 64
                plan["compatibility_inputs"][2]["sha256"] = m.sha256_file(config)
                binding = {k: str(root / k) for k in ("plan_path", "toolchain_path", "source_path", "observation", "build_dir")}
                binding.update(authority_path=str(state_path), recovery_path=str(root / ("4" * 64 + ".json")), binary_sha256="5" * 64, builder_receipt_sha256="6" * 64)
                validations = []
                def validate(args, work):
                    validations.append(work)
                    if outcome == "input-changed" and len(validations) == 3:
                        raise m.ApplyError("private input changed")
                    comparison = m.prepare_controller_comparison(work, plan)
                    for name in ("old", "effective"):
                        self.assertEqual(Path(comparison[name + "_controller_path"]).stat().st_mode & 0o777, 0o640)
                    return {**binding, **comparison, "state": state, "plan": plan, "group": next(g for g in plan["action_groups"] if g["id"] == m.IDENTITY_GROUP)}
                stack.enter_context(patch.object(m, "validate_inputs_v3", side_effect=validate))
                roles = stack.enter_context(patch.object(m, "verify_controller_pair", return_value=self.roles()))
                with redirect_stdout(io.StringIO()) as stdout: m.identity_preflight(Mock())
                intent = json.loads(stdout.getvalue())
                m.validate_identity_intent(intent)
                for update in ({"action": "rollback_to_legacy"}, {"command": "id"}, {"action_set": m.IDENTITY_SCOPES[:-1]}, {"expires_at": "2000-01-01T00:00:00Z"}):
                    with self.assertRaises(m.ApplyError): m.validate_identity_intent({**intent, **update})
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
                if outcome == "roles-failed": roles.side_effect = m.ApplyError("role check failed")
                if outcome == "concurrent": stack.enter_context(patch.object(m, "publish_authority", side_effect=m.ApplyError("concurrent source publication")))
                if outcome == "receipt-failed": stack.enter_context(patch.object(m, "store_identity_receipt", side_effect=OSError("disk full")))
                with redirect_stdout(io.StringIO()) as stdout:
                    if outcome in ("adopt", "verify"):
                        m.identity_execute(args, intent)
                        result = json.loads(stdout.getvalue())
                        receipt = m.load_json(result["receipt_path"])
                        self.assertEqual(receipt["result"], "verified" if outcome == "verify" else "success")
                        self.assertEqual(Path(result["receipt_path"]).stat().st_mode & 0o777, 0o440)
                    else:
                        with self.assertRaisesRegex(m.ApplyError, "incomplete" if outcome == "receipt-failed" else "before publication"):
                            m.identity_execute(args, intent)
                active = m.AUTHORITY_POINTER.read_text().strip()
                if outcome in ("adopt", "receipt-failed"):
                    after = m.load_json(m.AUTHORITY_ROOT / (active + ".json"))
                    m.validate_authority_current_document(after)
                    self.assertEqual([g for g in after["setting_groups"] if g["id"] != m.IDENTITY_GROUP], state["setting_groups"])
                    self.assertEqual(after["prior_state_sha256"], state["authority_state_sha256"])
                else:
                    self.assertEqual(active, state["authority_state_sha256"])
                self.assertEqual(list(m.BOX_RUNTIME_ROOT.iterdir()), [])
                self.assertEqual(len(list(m.NONCE_ROOT.iterdir())), 1)
                calls = roles.call_count
                with self.assertRaisesRegex(m.ApplyError, "nonce was already used"): m.identity_execute(args, intent)
                self.assertEqual(roles.call_count, calls)
                forbidden.assert_not_called()
                self.assertEqual(m.sha256_file(config), plan["compatibility_inputs"][2]["sha256"])

    def test_dispatch_uses_verified_pair_and_explicit_recovery_is_bounded(self):
        path = Path(__file__).resolve().parents[1] / "bin" / "ops-controller-ha"
        loader = SourceFileLoader("identity_ha_test", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        ha = importlib.util.module_from_spec(spec)
        loader.exec_module(ha)
        status = {"schema_version": 1, "kind": "klokast.controller-identity-status.v1", "source": "instance_specification_v1", "authority_state_sha256": "a" * 64, "engine_commit": "b" * 40, "controllers": self.pair}
        config = {"remote_user": "smith", "repo_dir": "~/src/klokast/klokast-box", "controllers": {p["box"]: p for p in self.pair.values()}}
        with patch.object(ha, "is_local_controller", return_value=False), patch.object(ha, "run", side_effect=[Mock(returncode=0, stdout=json.dumps(status)), Mock(returncode=1)]):
            resolved = ha.source_config(config)
        self.assertEqual(resolved["identity_status"], status)
        with patch.object(ha, "remote_sh") as dispatch, self.assertRaises(SystemExit):
            ha.run_on_controller(resolved, Mock(controller="boxb-ops", dry_run_plan=False))
        dispatch.assert_not_called()
        for malformed in ({**status, "command": "id"}, {**status, "source": "unknown"}):
            with self.assertRaises(ValueError): ha.validate_source_status(malformed, "boxa-ops")
        with self.assertRaises(ValueError): ha.validate_source_status(status, "boxb-ops")
        with patch.object(ha, "is_local_controller", return_value=False), patch.object(ha, "run", return_value=Mock(returncode=1)), self.assertRaises(SystemExit):
            ha.source_config(config)
        with patch.object(ha, "resolve_config_path", return_value=Path("/unused")), patch.object(ha, "load_config", return_value=config), self.assertRaises(SystemExit):
            ha.main(["--legacy-recovery", "resolve-active"])
        with patch.dict(os.environ, {ha.CONFIG_ENV: ""}), patch.object(ha, "resolve_config_path", side_effect=AssertionError("explicit airunner contact read private config")), patch.object(ha, "source_config", return_value=resolved), redirect_stdout(io.StringIO()) as stdout:
            ha.main(["resolve-active", "--controller", "boxa-ops"])
        self.assertEqual(stdout.getvalue().strip(), "boxa-ops")

    def test_adopted_status_requires_current_inputs_and_protected_adoption(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            for name in ("INSTANCE", "AUTHORITY_ROOT", "PREFLIGHT_ROOT", "PLAN_ROOT"):
                directory = root / name
                directory.mkdir()
                stack.enter_context(patch.object(m, name, directory))
            stack.enter_context(patch.object(m, "AUTHORITY_POINTER", root / "active"))
            stack.enter_context(patch.object(m, "require_root_active", return_value={"hostname": "boxa-ops"}))
            stack.enter_context(patch.object(m, "require_self_match"))
            stack.enter_context(patch.object(m, "verify_public_checkout"))
            stack.enter_context(patch.object(m, "run_plan_as_controller", side_effect=lambda argv: Mock(returncode=0, stdout="main" if "branch" in argv else "")))
            roles = stack.enter_context(patch.object(m, "verify_controller_pair", return_value=self.roles()))
            plan = self.plan()
            path = self.base.fixture.store_plan(m.PLAN_ROOT, plan)
            plan = m.load_json(path)
            binding = dict(plan=plan, state=self.state(), group=next(g for g in plan["action_groups"] if g["id"] == m.IDENTITY_GROUP),
                           recovery_path=str(root / ("1" * 64 + ".json")), binary_sha256="2" * 64, builder_receipt_sha256="3" * 64,
                           controller_pair=self.pair, old_controller_sha256="4" * 64, effective_controller_sha256="4" * 64, controller_roles_sha256="5" * 64)
            intent = m.identity_intent(binding, "adopted-controller-test", m.now_utc())
            archive = m.PREFLIGHT_ROOT / intent["nonce"]
            archive.mkdir(mode=0o700)
            intent_path = archive / "intent.json"
            intent_path.write_text(m.canonical(intent) + "\n")
            (archive / "binding.json").write_text(m.canonical({"plan_path": str(path)}) + "\n")
            state = self.state(True)
            state.update(transition_id=intent["nonce"], signed_intent_sha256=m.sha256_file(intent_path),
                         controller_identity_adoption={"nonce": intent["nonce"], "intent_sha256": m.sha256_file(intent_path), "plan_sha256": plan["plan_sha256"]})
            state["authority_state_sha256"] = m.authority_v2_hash(state)
            (m.AUTHORITY_ROOT / (state["authority_state_sha256"] + ".json")).write_text(m.canonical(state) + "\n")
            m.AUTHORITY_POINTER.write_text(state["authority_state_sha256"] + "\n")
            instance = m.INSTANCE / "klokast-instance.json"
            instance.write_text(json.dumps({"controllers": {"active": "boxa", "standby": "boxb"}}))
            lock = m.INSTANCE / "klokast.lock.json"
            lock.write_text(json.dumps({"engine": {"repository": m.ENGINE_REPOSITORY, "ref": "main", "commit": "c" * 40}}))
            self.assertEqual(m.controller_identity_status()["controllers"], self.pair)
            before = instance.read_bytes()
            instance.write_text(json.dumps({"controllers": {"active": "boxb", "standby": "boxa"}}))
            with self.assertRaisesRegex(m.ApplyError, "differ from adopted"):
                m.controller_identity_status()
            instance.write_bytes(before)
            def changed_lock(pair):
                lock.write_text("{}")
                return self.roles()
            roles.side_effect = changed_lock
            with self.assertRaisesRegex(m.ApplyError, "engine changed"):
                m.controller_identity_status()
            roles.side_effect = None
            intent_path.write_text(m.canonical({**intent, "controller_pair": {"active": self.pair["standby"], "standby": self.pair["active"]}}) + "\n")
            with self.assertRaisesRegex(m.ApplyError, "archive does not match"):
                m.controller_identity_status()


if __name__ == "__main__":
    unittest.main()
