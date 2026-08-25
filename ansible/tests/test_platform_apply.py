#!/usr/bin/env python3
import datetime as dt
import importlib.util
import json
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
KSA_APPLY = REPO_ROOT / "klokast-ops/secret-authority/bin/ksa-apply"
MACBOOK_APPLY = REPO_ROOT / "klokast-dev/bin/apply-platform-intent"
MUTATION = REPO_ROOT / "klokast-ops/tailscale/bin/ts-policy-mutate-internal"
OPS_VARS = (REPO_ROOT / "ansible/inventory/group_vars/ops.yml").read_text(encoding="utf-8")
OPS_TASKS = (REPO_ROOT / "ansible/roles/ops-controller/tasks/main.yml").read_text(encoding="utf-8")
OPS_VERIFY = (REPO_ROOT / "ansible/roles/ops-controller-verification/tasks/main.yml").read_text(encoding="utf-8")


def load():
    loader = SourceFileLoader("ksa_apply_test", str(KSA_APPLY))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PlatformApplyTest(unittest.TestCase):
    def setUp(self):
        self.mod = load()

    def valid_intent(self, *, action="adopt_instance_specification"):
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        value = {
            "schema_version": 1,
            "kind": self.mod.KIND_INTENT,
            "action": action,
            "action_set": list(self.mod.SCOPES),
            "executor": self.mod.EXECUTOR,
            "rollback_type": self.mod.ROLLBACK_TYPE,
            "live_etag": '"etag-value"',
            "nonce": "nonce_123456789",
            "issued_at": self.mod.format_utc(now),
            "expires_at": self.mod.format_utc(now + dt.timedelta(minutes=10)),
            "private_commit": "a" * 40,
            "engine_commit": "b" * 40,
        }
        digest_fields = {
            "plan_sha256", "authority_state_sha256", "source_recovery_receipt_sha256",
            "source_receipt_sha256", "observation_sha256", "private_instance_sha256",
            "legacy_deployment_sha256", "legacy_registry_sha256",
            "legacy_controller_ha_sha256", "binary_sha256", "builder_receipt_sha256",
            "toolchain_receipt_sha256", "live_policy_sha256", "candidate_policy_sha256",
        }
        for index, field in enumerate(sorted(digest_fields)):
            value[field] = f"{index + 1:064x}"
        if action == "rollback_to_legacy":
            value["rollback_execution_receipt_sha256"] = "f" * 64
        return value

    def valid_plan(self, *, operation="adopt_instance_specification"):
        before = "legacy_deployment" if operation == "adopt_instance_specification" else "instance_specification_v1"
        legacy_digest = "9" * 64
        plan = {
            "schema_version": 2,
            "kind": self.mod.KIND_PLAN,
            "valid": True,
            "compatible": True,
            "substrate_healthy": True,
            "deployable": True,
            "authority_ready": True,
            "legacy_removal_ready": False,
            "health_scope": "standard_substrate_v1",
            "engine": {},
            "instance": {},
            "instance_source": {},
            "authority_state": {},
            "controller_toolchain": {},
            "inputs": [],
            "refusals": [],
            "diagnostics": [],
            "projection": {},
            "projection_sha256": "1" * 64,
            "observation": {},
            "compatibility": {},
            "authorities": [],
            "compatibility_inputs": [
                {"name": "legacy_deployment", "sha256": legacy_digest},
            ],
            "atomic_action_group": {
                "id": "tailnet-policy-inputs-v1",
                "operation": operation,
                "scopes": list(self.mod.SCOPES),
                "executor": self.mod.EXECUTOR,
                "rollback_type": self.mod.ROLLBACK_TYPE,
            },
            "actions": [],
        }
        for index, scope in enumerate(self.mod.SCOPES):
            plan["actions"].append({
                "id": f"action-{index}",
                "finding_id": f"finding-{index}",
                "operation": operation,
                "scope": scope,
                "authority_before": before,
                "authority_after": "instance_specification_v1",
                "executor": self.mod.EXECUTOR,
                "preconditions": [
                    "active_controller_fenced", "exact_plan_v2_revalidated",
                    "byte_equal_policy", "tailnet_policy_preimage_prepared",
                ],
                "rollback": {
                    "strategy": self.mod.ROLLBACK_TYPE,
                    "authority": "legacy_deployment",
                    "source_sha256": legacy_digest,
                },
            })
        return plan

    def store_plan(self, root, plan):
        digest = self.mod.sha256_bytes(self.mod.canonical(plan).encode("utf-8"))
        plan["plan_sha256"] = digest
        directory = root / ("a" * 40)
        directory.mkdir(exist_ok=True)
        path = directory / f"{digest}.json"
        path.write_text(self.mod.canonical(plan) + "\n", encoding="utf-8")
        return path

    def test_intent_rejects_partial_unknown_and_expired_actions(self):
        intent = self.valid_intent()
        self.mod.validate_intent(intent)
        partial = dict(intent)
        partial["action_set"] = partial["action_set"][:2]
        with self.assertRaisesRegex(self.mod.ApplyError, "partial"):
            self.mod.validate_intent(partial)
        unknown = dict(intent)
        unknown["executor"] = "shell"
        with self.assertRaisesRegex(self.mod.ApplyError, "partial"):
            self.mod.validate_intent(unknown)
        expired = dict(intent)
        past = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - dt.timedelta(minutes=20)
        expired["issued_at"] = self.mod.format_utc(past)
        expired["expires_at"] = self.mod.format_utc(past + dt.timedelta(minutes=10))
        with self.assertRaisesRegex(self.mod.ApplyError, "expired"):
            self.mod.validate_intent(expired)

    def test_nonce_is_single_use(self):
        intent = self.valid_intent()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            self.mod, "NONCE_ROOT", Path(temporary) / "nonces"
        ):
            self.mod.consume_nonce(intent)
            with self.assertRaisesRegex(self.mod.ApplyError, "already used"):
                self.mod.consume_nonce(intent)

    def test_macbook_replay_proof_is_closed(self):
        source = MACBOOK_APPLY.read_text(encoding="utf-8")
        self.assertIn("--prove-replay-refusal", source)
        self.assertIn("run_remote_execute", source)
        self.assertIn("ksa-apply: authority state is not active", source)
        self.assertIn("ksa-apply: Apply intent nonce was already used", source)
        self.assertIn("controller accepted a replayed Apply intent", source)

        combined = subprocess.run(
            [str(MACBOOK_APPLY), "--check", "--prove-replay-refusal"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(combined.returncode, 0)
        self.assertIn("mutually exclusive", combined.stderr)

        rollback = subprocess.run(
            [
                str(MACBOOK_APPLY),
                "--rollback-execution-receipt",
                "/tmp/execution.json",
                "--prove-replay-refusal",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(rollback.returncode, 0)
        self.assertIn("only adoption or verification", rollback.stderr)

    def test_apply_refuses_plan_v1(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commit = "a" * 40
            directory = root / commit
            directory.mkdir()
            digest = "b" * 64
            path = directory / f"{digest}.json"
            path.write_text(self.mod.canonical({
                "schema_version": 1,
                "kind": "klokast.plan.v1",
                "plan_sha256": digest,
            }) + "\n", encoding="utf-8")
            with patch.object(self.mod, "PLAN_ROOT", root):
                with self.assertRaisesRegex(self.mod.ApplyError, "Plan v1"):
                    self.mod.verify_plan(path)

    def test_apply_refuses_missing_rollback_and_wrong_executor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = self.valid_plan()
            del plan["actions"][0]["rollback"]
            path = self.store_plan(root, plan)
            with patch.object(self.mod, "PLAN_ROOT", root):
                with self.assertRaisesRegex(self.mod.ApplyError, "partial or unknown"):
                    self.mod.verify_plan(path)
            plan = self.valid_plan()
            plan["atomic_action_group"]["executor"] = "shell"
            path = self.store_plan(root, plan)
            with patch.object(self.mod, "PLAN_ROOT", root):
                with self.assertRaisesRegex(self.mod.ApplyError, "exact Tailnet"):
                    self.mod.verify_plan(path)

    def test_apply_refuses_inactive_controller_and_wrong_signer(self):
        inactive = Mock(returncode=1, stdout="", stderr="")
        with patch.object(self.mod.os, "geteuid", return_value=0), patch.object(
            self.mod, "run", return_value=inactive
        ):
            with self.assertRaisesRegex(self.mod.ApplyError, "inactive"):
                self.mod.require_root_active()
        args = Mock(signer_id="human-private-instance")
        with self.assertRaisesRegex(self.mod.ApplyError, "human-platform-apply"):
            self.mod.verify_signature(args, self.valid_intent())

    def test_apply_refuses_wrong_active_authority_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = {
                "schema_version": 1,
                "kind": self.mod.KIND_AUTHORITY,
                "prior_state_sha256": "",
                "transitioned_scopes": [],
                "resulting_authorities": [],
                "signed_intent_sha256": "",
                "transition_id": "initial",
                "authority_state_sha256": "",
            }
            state["authority_state_sha256"] = self.mod.authority_hash(state)
            path = root / f"{state['authority_state_sha256']}.json"
            path.write_text(self.mod.canonical(state) + "\n", encoding="utf-8")
            pointer = root / "active"
            pointer.write_text("f" * 64 + "\n", encoding="ascii")
            plan = self.valid_plan()
            plan["authority_state"] = {"authority_state_sha256": state["authority_state_sha256"]}
            with patch.object(self.mod, "AUTHORITY_ROOT", root), patch.object(
                self.mod, "AUTHORITY_POINTER", pointer
            ):
                with self.assertRaisesRegex(self.mod.ApplyError, "not active"):
                    self.mod.validate_authority(path, plan)

    def test_source_recovery_receipt_hash_is_revalidated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commit = "a" * 40
            directory = root / commit
            directory.mkdir()
            receipt = {
                "schema_version": 1,
                "kind": self.mod.KIND_RECOVERY,
                "repository_sha256": "1" * 64,
                "repository_id": 123,
                "private_commit": commit,
                "private_tree": "b" * 40,
                "source_receipt_sha256": "2" * 64,
                "engine_commit": "c" * 40,
                "build_operation": "d" * 12,
                "binary_sha256": "3" * 64,
                "builder_receipt_sha256": "4" * 64,
                "checked_at": self.mod.format_utc(self.mod.now_utc()),
            }
            digest = self.mod.sha256_bytes(self.mod.canonical(receipt).encode("utf-8"))
            receipt["receipt_sha256"] = digest
            path = directory / f"{digest}.json"
            path.write_text(self.mod.canonical(receipt) + "\n", encoding="utf-8")
            plan = {"instance": {"commit": commit}, "engine": {"commit": "c" * 40}}
            with patch.object(self.mod, "RECOVERY_ROOT", root):
                self.mod.validate_source_recovery(path, plan)
                receipt["private_tree"] = "e" * 40
                path.write_text(self.mod.canonical(receipt) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(self.mod.ApplyError, "does not match"):
                    self.mod.validate_source_recovery(path, plan)

    def test_mutation_boundary_requires_etag_and_handles_412(self):
        source = MUTATION.read_text(encoding="utf-8")
        self.assertIn('"If-Match: $etag"', source)
        self.assertIn('[ "$status" = 412 ]', source)
        self.assertIn('>"$work_dir/post-status"', source)
        self.assertIn("get-before|get-after|get-recovery|validate-candidate|post-candidate|post-preimage", source)
        self.assertNotIn("$3", source)

    def test_infra_has_no_direct_policy_mutation_privilege(self):
        self.assertNotIn("- ts-policy-apply", OPS_VARS)
        self.assertIn("Remove the retired direct Tailnet policy mutation wrapper", OPS_TASKS)
        self.assertNotIn("ts-policy-mutate-internal\n  -", OPS_VARS)
        self.assertIn("/usr/local/libexec/klokast/ts-policy-mutate-internal", OPS_TASKS)

    def test_exact_plan_revalidation_drops_to_controller_checkout_owner(self):
        command = [Path("/sealed/klokast"), "plan", "--json"]
        completed = Mock(returncode=0, stdout="{}", stderr="")
        with patch.object(self.mod, "run", return_value=completed) as runner:
            self.assertIs(self.mod.run_plan_as_controller(command), completed)
        runner.assert_called_once_with(
            [self.mod.DOAS, "-u", self.mod.CONTROLLER_USER, *command],
            capture=True,
            check=False,
        )
        source = KSA_APPLY.read_text(encoding="utf-8")
        self.assertIn("rerun = run_plan_as_controller(command)", source)

    def test_policy_renderer_output_is_captured_from_apply_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)

            def render(argv, **kwargs):
                output = Path(argv[argv.index("--output") + 1])
                output.write_bytes(b"fixed policy bytes\n")
                return Mock(returncode=0, stdout=f"{output}\n", stderr="")

            with patch.object(self.mod, "run", side_effect=render) as runner:
                self.assertEqual(
                    self.mod.render_policies(work),
                    b"fixed policy bytes\n",
                )

        self.assertEqual(runner.call_count, 2)
        for call in runner.call_args_list:
            self.assertTrue(call.kwargs.get("capture"))

    def test_ansible_requires_exact_apply_toolchain_bytes(self):
        self.assertIn("Require exact checked and installed Apply toolchain bytes", OPS_VERIFY)
        for name in (
            "controller_guard", "ksa_apply", "platform_resources",
            "policy_mutation_helper", "policy_renderer", "policy_template",
        ):
            self.assertIn(f"name: {name}_source", OPS_VERIFY)
            self.assertIn(f"name: {name}_installed", OPS_VERIFY)

    def test_root_apply_does_not_load_checkout_python(self):
        source = KSA_APPLY.read_text(encoding="utf-8")
        self.assertNotIn("SourceFileLoader", source)
        self.assertNotIn("importlib", source)
        self.assertNotIn("PLATFORM_PLAN", source)
        self.assertNotIn("load_plan_module", source)
        self.assertIn("def verify_build_directory", source)
        self.assertIn("rerun = run_plan_as_controller(command)", source)

        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "executed"
            malicious = Path(temporary) / "platform-plan"
            malicious.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('root code ran')\n",
                encoding="utf-8",
            )
            load()
            self.assertFalse(marker.exists())

    def test_strict_json_rejects_duplicate_and_noncanonical_stored_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            path.write_text('{"a":1,"a":2}\n', encoding="utf-8")
            with self.assertRaisesRegex(self.mod.ApplyError, "duplicate"):
                self.mod.load_json(path)
            path.write_text('{ "a": 1 }\n', encoding="utf-8")
            with self.assertRaisesRegex(self.mod.ApplyError, "not canonical"):
                self.mod.load_json(path, canonical_stored=True)
            path.write_text('{"a":1}\n{}\n', encoding="utf-8")
            with self.assertRaisesRegex(self.mod.ApplyError, "not valid JSON"):
                self.mod.load_json(path)

    def test_source_recovery_is_temporary_and_redacted(self):
        source = (REPO_ROOT / "klokast-ops/secret-authority/bin/ksa-instance").read_text(encoding="utf-8")
        function = source[source.index("def cmd_source_recovery_check"):source.index("def app_repository_ids")]
        self.assertIn("tempfile.mkdtemp", function)
        self.assertIn("shutil.rmtree(temporary)", function)
        self.assertIn("capture=True", function)
        self.assertIn("checkout_state(args.checkout)", function)
        self.assertNotIn('"repository":', function)
        self.assertIn("SOURCE_RECOVERY_KIND", function)

    def test_recovery_runs_only_after_confirmed_successful_post(self):
        source = KSA_APPLY.read_text(encoding="utf-8")
        self.assertIn('read_regular(post_status, 16) == b"200\\n"', source)
        self.assertIn("live policy body or ETag changed after authorization", source)
        self.assertIn("post-write policy verification failed", source)
        self.assertIn("recovery_required", source)
        self.assertIn("--tags ops-controller-secret-authority-wrappers", source)


if __name__ == "__main__":
    unittest.main()
