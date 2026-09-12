"""Closed Plan v9 and legacy-input retirement executor tests."""

import copy
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_instance_verification


class LegacyInputRetirementTest(unittest.TestCase):
    def setUp(self):
        self.base = test_instance_verification.InstanceVerificationTest()
        self.base.setUp()
        self.m = self.base.m

    def test_matrix_handoff_has_readable_projections_and_new_output(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / 'matrix'
            work.mkdir()
            matrix = {'schema_version': 1, 'kind': 'klokast.instance-input-absence.v1',
                      'equal': True, 'temporary_views_removed': True,
                      'effective_settings_sha256': 'a' * 64,
                      'consumers': sorted(m.RETIREMENT_CONSUMERS)}
            content = m.canonical(matrix) + '\n'
            plan = self.plan()
            plan['plan_sha256'] = 'b' * 64
            plan['legacy_retirement']['consumer_matrix_sha256'] = m.sha256_bytes(content.encode())
            owners = {}
            def invoke(argv):
                for option, value in (('--inventory-projection', plan['inventory']),
                        ('--registry-projection', {'valid': True, 'engine': plan['engine'],
                                                  'projection': plan['projection']['registry']})):
                    path = Path(argv[argv.index(option) + 1])
                    self.assertEqual(json.loads(path.read_text()), value)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o440)
                    self.assertEqual(owners[path], (0, os.getgid()))
                output = Path(argv[argv.index('--output-directory') + 1])
                self.assertFalse(output.exists())
                self.assertEqual(output.parent.stat().st_mode & 0o777, 0o700)
                output.mkdir()
                (output/'result.json').write_text(content)
                return SimpleNamespace(returncode=0, stdout=content)
            with patch.object(m, 'new_box_work', return_value=work), patch.object(
                    m.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())), patch.object(
                    m.os, 'chown', side_effect=lambda path, uid, gid: owners.update({path: (uid, gid)})), patch.object(
                    m, 'run_plan_as_controller', side_effect=invoke):
                self.assertEqual(m.run_bound_retirement_matrix(plan, 'test-matrix'), matrix)
            self.assertFalse(work.exists())

    def reference(self, phase="exercise"):
        m = self.m
        metadata = lambda path, digit: {
            "path": str(path), "sha256": digit * 64, "size": 10,
            "uid": 1000, "gid": 1000, "mode": "0600", "nlink": 1,
        }
        return {
            "phase": phase, "evidence_sha256": "1" * 64,
            "consumer_matrix_sha256": "2" * 64, "settings_sha256": "3" * 64,
            "recovery_archive_path": str(m.RETIREMENT_ARCHIVE_ROOT / ("4" * 64)),
            "recovery_archive_sha256": "4" * 64, "recovery_manifest_sha256": "5" * 64,
            "detached_reconstruction_sha256": "6" * 64,
            "consumer_absence_complete": True, "recovery_reconstruction_verified": True,
            "settings_unchanged": True, "live_inputs_state": "absent" if phase == "verify" else "present",
            "approved_backups_state": "absent" if phase == "verify" else "present",
            "live_inputs": [metadata(path, str(index + 1)) for index, path in enumerate(m.LEGACY_INPUTS)],
            "approved_backups": [metadata(path, str(index + 4)) for index, path in enumerate(m.OBSOLETE_BACKUPS)],
            "exercise_receipt_sha256": "7" * 64 if phase in {"retire", "verify"} else "",
            "retirement_receipt_sha256": "8" * 64 if phase == "verify" else "",
        }

    def plan(self, phase="exercise"):
        m = self.m
        plan = self.base.plan()
        plan.update(schema_version=9, kind=m.KIND_PLAN_V9, legacy_retirement=self.reference(phase),
                    legacy_removal_ready=phase != "exercise")
        groups = plan["action_groups"]
        for action in plan["actions"]:
            action["preconditions"][0] = "exact_plan_v9_revalidated"
        operation = {"exercise": "exercise_legacy_input_retirement", "retire": "retire_legacy_inputs", "verify": "verify_legacy_retirement"}[phase]
        rollback = {"exercise": "restore_exact_legacy_inputs", "retire": "recovery_archive_only", "verify": "no_mutation"}[phase]
        groups.append({"id": m.RETIREMENT_GROUP, "operation": operation, "scopes": ["legacy_private_inputs"],
                       "executor": m.RETIREMENT_EXECUTOR, "rollback_type": rollback})
        plan["actions"].append({
            "id": operation + "-" + m.sha256_bytes((operation + "\0legacy_private_inputs").encode())[:16],
            "operation": operation, "scope": "legacy_private_inputs", "authority_before": "retained_legacy_inputs",
            "authority_after": "retained_legacy_inputs" if phase == "exercise" else "immutable_recovery_archive",
            "executor": m.RETIREMENT_EXECUTOR,
            "preconditions": ["exact_plan_v9_revalidated", "complete_instance_ownership", "consumer_absence_verified", "recovery_reconstruction_verified", "single_use_approval"],
            "rollback": {"strategy": rollback, "authority": "immutable_recovery_archive", "source_sha256": "4" * 64},
        })
        plan["actions"].sort(key=lambda item: item["id"])
        return plan

    def test_plan_v9_has_six_verification_groups_and_one_lifecycle_group(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary, patch.object(m, "PLAN_ROOT", Path(temporary)), patch.object(
            m, "RETIREMENT_ARCHIVE_ROOT", Path("/var/lib/klokast/legacy-input-recovery")
        ):
            for phase in ("exercise", "retire", "verify"):
                plan = self.plan(phase)
                path = self.base.store_plan(Path(temporary), plan)
                self.assertEqual(m.verify_plan_v9(path)[2]["operation"], plan["action_groups"][-1]["operation"])
            bad = self.plan("retire")
            bad["legacy_removal_ready"] = False
            with self.assertRaises(m.ApplyError):
                m.verify_plan_v9(self.base.store_plan(Path(temporary), bad))
            bad = self.plan("exercise")
            bad["action_groups"][0]["operation"] = "retain_legacy"
            with self.assertRaises(m.ApplyError):
                m.verify_plan_v9(self.base.store_plan(Path(temporary), bad))

    def test_metadata_refuses_symlinks_hardlinks_and_drift(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.write_bytes(b"exact")
            first = m.retirement_metadata(source)
            source.write_bytes(b"changed")
            self.assertNotEqual(first, m.retirement_metadata(source))
            link = root / "link"
            link.symlink_to(source)
            with self.assertRaises(m.ApplyError):
                m.retirement_metadata(link)
            hard = root / "hard"
            hard.hardlink_to(source)
            with self.assertRaises(m.ApplyError):
                m.retirement_metadata(source)

    def test_intent_is_ten_minute_single_phase_and_rejects_expiry(self):
        m = self.m
        plan = self.plan("exercise")
        collected = self.base.collected()
        collected.update(phase="exercise", retirement=plan["legacy_retirement"], action_groups=plan["action_groups"])
        intent = m.retirement_intent(collected, "retirement-test-nonce", m.now_utc())
        m.validate_retirement_intent(intent)
        expired = copy.deepcopy(intent)
        issued = m.now_utc() - dt.timedelta(minutes=11)
        expired["issued_at"] = m.format_utc(issued)
        expired["expires_at"] = m.format_utc(issued + dt.timedelta(minutes=10))
        with self.assertRaisesRegex(m.ApplyError, "expired"):
            m.validate_retirement_intent(expired)
        changed = copy.deepcopy(intent)
        changed["retirement"]["approved_backups"].append(changed["retirement"]["approved_backups"][0])
        with self.assertRaises(m.ApplyError):
            m.validate_retirement_intent(changed)
        changed = copy.deepcopy(intent)
        changed["retirement"]["unknown"] = True
        with self.assertRaises(m.ApplyError):
            m.validate_retirement_intent(changed)

    def test_removal_checks_exact_prior_state_before_unlink(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary:
            paths = tuple(Path(temporary) / name for name in ("one", "two", "three"))
            for index, path in enumerate(paths):
                path.write_bytes(f"value-{index}".encode())
            expected = [m.retirement_metadata(path) for path in paths]
            changed = copy.deepcopy(expected)
            changed[0]["sha256"] = "f" * 64
            with self.assertRaisesRegex(m.ApplyError, "prior state"):
                m.remove_retirement_paths(paths, changed)
            self.assertTrue(all(path.exists() for path in paths))
            m.remove_retirement_paths(paths, expected)
            self.assertTrue(all(not path.exists() for path in paths))

    def execution_fixture(self, root, phase):
        m = self.m
        plan = self.plan(phase)
        collected = self.base.collected()
        collected.update(phase=phase, retirement=plan["legacy_retirement"], action_groups=plan["action_groups"])
        collected["binding"] = {**collected["binding"], "retirement_evidence_path": "/protected/retirement"}
        plan_path = root / "plan.json"
        plan_path.write_text(self.m.canonical(plan) + "\n")
        collected["binding"]["plan_path"] = str(plan_path)
        intent = m.retirement_intent(collected, "retirement-test-nonce", m.now_utc())
        preflight = root / intent["nonce"]
        preflight.mkdir(parents=True)
        (preflight / "intent.json").write_text(m.canonical(intent) + "\n")
        (preflight / "binding.json").write_text(m.canonical(collected["binding"]) + "\n")
        return collected, intent

    def test_exercise_consumes_nonce_before_removal_and_restores(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collected, intent = self.execution_fixture(root, "exercise")
            events = []
            def store(receipt):
                receipt["receipt_sha256"] = "d" * 64
                return root / "receipt.json"
            with patch.object(m, "PREFLIGHT_ROOT", root), patch.object(m, "verify_signature"), patch.object(
                m, "consume_nonce", side_effect=lambda _intent: events.append("nonce")
            ), patch.object(m, "authority_publication_lock", return_value=nullcontext()), patch.object(
                m, "retirement_collect", return_value=collected
            ), patch.object(m, "validate_retirement_archive", return_value=(root, {"files": []}, "4" * 64)), patch.object(
                m, "remove_retirement_paths", side_effect=lambda *_args: events.append("remove")
            ), patch.object(m, "verification_collect", return_value={"evidence": intent["evidence"]}), patch.object(
                m, "run_bound_retirement_matrix"
            ), patch.object(m, "restore_legacy_inputs", side_effect=lambda *_args: events.append("restore")), patch.object(
                m, "store_retirement_receipt", side_effect=store
            ), patch.object(m, "append_audit"), redirect_stdout(io.StringIO()):
                m.retirement_execute(SimpleNamespace(), intent)
            self.assertEqual(events, ["nonce", "remove", "restore"])

    def test_exercise_restoration_failure_reports_recovery_required(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collected, intent = self.execution_fixture(root, "exercise")
            with patch.object(m, "PREFLIGHT_ROOT", root), patch.object(m, "verify_signature"), patch.object(
                m, "consume_nonce"
            ), patch.object(m, "authority_publication_lock", return_value=nullcontext()), patch.object(
                m, "retirement_collect", return_value=collected
            ), patch.object(m, "validate_retirement_archive", return_value=(root, {"files": []}, "4" * 64)), patch.object(
                m, "remove_retirement_paths"
            ), patch.object(m, "verification_collect", side_effect=m.ApplyError("absence failed")), patch.object(
                m, "restore_legacy_inputs", side_effect=m.ApplyError("restore failed")
            ):
                with self.assertRaisesRegex(m.ApplyError, "recovery is required"):
                    m.retirement_execute(SimpleNamespace(), intent)

    def test_partial_exercise_removal_still_restores(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collected, intent = self.execution_fixture(root, "exercise")
            with patch.object(m, "PREFLIGHT_ROOT", root), patch.object(m, "verify_signature"), patch.object(
                m, "consume_nonce"
            ), patch.object(m, "authority_publication_lock", return_value=nullcontext()), patch.object(
                m, "retirement_collect", return_value=collected
            ), patch.object(m, "validate_retirement_archive", return_value=(root, {"files": []}, "4" * 64)), patch.object(
                m, "remove_retirement_paths", side_effect=m.ApplyError("partial removal")
            ), patch.object(m, "restore_legacy_inputs") as restore, patch.object(
                m, "store_retirement_receipt"
            ), patch.object(m, "append_audit"):
                with self.assertRaisesRegex(m.ApplyError, "exact legacy inputs were restored"):
                    m.retirement_execute(SimpleNamespace(), intent)
            restore.assert_called_once()

    def test_final_receipt_storage_failure_never_restores_retired_inputs(self):
        m = self.m
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collected, intent = self.execution_fixture(root, "retire")
            with patch.object(m, "PREFLIGHT_ROOT", root), patch.object(m, "verify_signature"), patch.object(
                m, "consume_nonce"
            ), patch.object(m, "authority_publication_lock", return_value=nullcontext()), patch.object(
                m, "retirement_collect", return_value=collected
            ), patch.object(m, "validate_retirement_archive", return_value=(root, {"files": []}, "4" * 64)), patch.object(
                m, "remove_retirement_paths"
            ) as remove, patch.object(m, "verification_collect", return_value={"evidence": intent["evidence"]}), patch.object(
                m, "run_bound_retirement_matrix"
            ), patch.object(m, "retirement_absent"), patch.object(
                m, "store_retirement_receipt", side_effect=OSError("disk full")
            ), patch.object(m, "restore_legacy_inputs") as restore:
                with self.assertRaisesRegex(m.ApplyError, "files may have been removed"):
                    m.retirement_execute(SimpleNamespace(), intent)
            self.assertEqual(remove.call_count, 2)
            restore.assert_not_called()


if __name__ == "__main__":
    unittest.main()
