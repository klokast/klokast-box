"""Evidence expiry is not a content change and must not invite a signature."""
import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_platform_apply import load


class ApplyFreshnessTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.now = dt.datetime(2026, 9, 16, 12, tzinfo=dt.timezone.utc)

    def evidence(self, source_age=0, observation_age=0):
        return ({"fetched_at": self.m.format_utc(self.now - dt.timedelta(seconds=source_age))},
                {"observed_at": self.m.format_utc(self.now - dt.timedelta(seconds=observation_age))})

    def test_one_hour_boundary_and_unchanged_bytes_becoming_stale(self):
        m = self.m
        source, observation = self.evidence(3599, 3598)
        before = json.dumps([source, observation])
        with patch.object(m, "now_utc", return_value=self.now):
            deadline, label = m.check_evidence_deadline(source, observation)
            self.assertEqual(label, "instance source")
            self.assertEqual(deadline, self.now + dt.timedelta(seconds=1))
        with patch.object(m, "now_utc", return_value=self.now + dt.timedelta(seconds=1)):
            with self.assertRaisesRegex(m.ApplyError, "instance source evidence expired"):
                m.check_evidence_deadline(source, observation)
        self.assertEqual(before, json.dumps([source, observation]))

    def test_historical_ten_minute_intents_are_not_new_execution_authority(self):
        m = self.m
        for seconds in (599, 600, 601, 3599, 3600, 3601):
            expires = self.now + dt.timedelta(seconds=seconds)
            self.assertEqual(m.valid_intent_lifetime(self.now, expires), seconds == 3600)
            self.assertEqual(m.valid_intent_lifetime(self.now, expires, check_time=False), seconds in (600, 3600))

    def test_observation_deadline_reserve_and_future_clock(self):
        m = self.m
        with patch.object(m, "now_utc", return_value=self.now):
            m.check_evidence_deadline(*self.evidence(0, 2700), reserve=m.PREFLIGHT_EXECUTION_RESERVE)
            with self.assertRaisesRegex(m.ApplyError, "Observation.*less than 15 minutes"):
                m.check_evidence_deadline(*self.evidence(0, 2701), reserve=m.PREFLIGHT_EXECUTION_RESERVE)
            with self.assertRaisesRegex(m.ApplyError, "Observation evidence expired"):
                m.check_evidence_deadline(*self.evidence(0, 3600))
            with self.assertRaisesRegex(m.ApplyError, "instance source timestamp.*future"):
                m.check_evidence_deadline(*self.evidence(-301, 0))

    def test_retirement_expiry_during_matrix_never_issues_intent(self):
        m = self.m
        source, observation = self.evidence(0, 3599)
        plan = {"legacy_retirement": {}}
        collected = {"binding": {key: "/protected/" + key for key in m.RETIREMENT_BINDING_FIELDS}}
        clock = [self.now]

        def matrix(*args):
            clock[0] += dt.timedelta(seconds=2)

        def revalidate(*args):
            m.check_evidence_deadline(source, observation)

        with patch.object(m, "now_utc", side_effect=lambda: clock[0]), patch.object(
            m, "authority_publication_lock", return_value=nullcontext()
        ), patch.object(m, "verification_collect", return_value=collected), patch.object(
            m, "verify_plan_v9", return_value=(None, plan, None)
        ), patch.object(m, "retirement_assert_reference", return_value=({"phase": "verify"}, None, None, None)), patch.object(
            m, "run_bound_retirement_matrix", side_effect=matrix
        ) as matrix_call, patch.object(m, "validate_inputs_v3", side_effect=revalidate) as checked, patch.object(
            m, "retirement_intent"
        ) as issue, patch.object(m, "ensure_dir") as store, redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(m.ApplyError, "Observation evidence expired"):
                m.retirement_preflight(SimpleNamespace())
            matrix_call.assert_called_once()
            checked.assert_called_once()
            issue.assert_not_called()
            store.assert_not_called()

    def test_retirement_detects_content_change_after_matrix(self):
        m = self.m
        plan = {"legacy_retirement": {}}
        binding = {key: "/protected/" + key for key in m.RETIREMENT_BINDING_FIELDS}
        with patch.object(m, "verification_collect", return_value={"binding": binding}), patch.object(
            m, "verify_plan_v9", return_value=(None, plan, None)
        ), patch.object(m, "retirement_assert_reference", return_value=({"phase": "verify"}, None, None, None)), patch.object(
            m, "run_bound_retirement_matrix"
        ), patch.object(m, "validate_inputs_v3", return_value={**binding, "plan": {"changed": True}}), redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(m.ApplyError, "inputs changed during the complete consumer matrix"):
                m.retirement_collect(SimpleNamespace(), "freshness-test-nonce")

    def test_failed_planner_output_is_retained_but_redacted(self):
        m = self.m
        cases = [
            (1, json.dumps({"diagnostics": [{"code": "time.stale", "message": "PRIVATE"},
                                           {"code": "PRIVATE", "path": "PRIVATE"}]}), "Observation evidence expired"),
            (1, json.dumps({"diagnostics": [{"code": "fetched-at.stale"}]}), "instance source evidence expired"),
            (1, json.dumps({"diagnostics": [{"code": "PRIVATE"}]}), "sealed engine rejected"),
            (0, '{"PRIVATE":true}', "Plan content changed"),
            (1, 'PRIVATE', "invalid Plan JSON"),
            (1, '{"a":1,"a":2}', "invalid Plan JSON"),
        ]
        for rc, stdout, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary, patch.object(
                m, "PREFLIGHT_ROOT", Path(temporary)
            ):
                result = SimpleNamespace(returncode=rc, stdout=stdout, stderr="PRIVATE stderr")
                with self.assertRaisesRegex(m.ApplyError, expected) as error:
                    m.verify_revalidation_result(result, {"valid": True})
                self.assertNotIn("PRIVATE", str(error.exception))
                directory, = Path(temporary).iterdir()
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                self.assertEqual((directory / "stdout").read_text(), stdout)
                self.assertEqual((directory / "stderr").read_text(), "PRIVATE stderr")
                for path in directory.iterdir():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_successful_exact_revalidation_creates_no_failure_archive(self):
        with patch.object(self.m, "ensure_dir") as store:
            self.m.verify_revalidation_result(SimpleNamespace(returncode=0, stdout='{"valid":true}'), {"valid": True})
            store.assert_not_called()
