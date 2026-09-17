#!/usr/bin/env python3
import copy
import datetime as dt
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ansible/lib"))
import platform_updates as u
import platform_update_metadata as m

NOW = dt.datetime(2026, 9, 17, 2, 0, tzinfo=dt.timezone.utc)


def load_cli():
    loader = SourceFileLoader("platform_update_cli", str(REPO / "ansible/bin/platform-update"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class EvidenceTests(unittest.TestCase):
    def metadata(self):
        return {"v3.23": {"observed_at": u.timestamp(NOW), "signature_verified": True,
            "releases": {"release_branches": [{"rel_branch": "v3.23", "eol_date": "2027-01-01", "repos": [{"name":"main"}, {"name":"community", "eol_date":"2027-01-01"}]}]},
            "indexes": {"main": {"linux-virt": {"version":"2", "origin":"linux-virt"}}, "community": {}},
            "security": {"main": {"packages": []}, "community": {"packages": []}}}}

    def fact(self):
        return {"observed_at": u.timestamp(NOW), "role":"bak", "os":{"id":"alpine"}, "architecture":"x86_64", "branch":"v3.23",
                "packages":{"linux-virt":{"version":"1", "origin":"linux-virt"}}, "configuration":{"status":"unknown"}}

    def assess(self, fact=None, metadata=None, compare=lambda a, b: "<"):
        return u.assess_host("boxa-bak", fact or self.fact(), metadata or self.metadata(), ["linux-virt"], compare, NOW)

    def test_updates_are_evidence_not_permission(self):
        result = self.assess()
        self.assertEqual(result["update_status"], "updates-available")
        self.assertFalse(result["replacement_ready"])
        self.assertEqual(len(result["updates"]), 1)

    def test_stale_unsigned_missing_and_future_metadata_are_unknown(self):
        for change in ({"signature_verified":False}, {"observed_at":"2026-09-15T02:00:00Z"}, {"observed_at":"2026-09-18T02:00:00Z"}, {"error":"signature failure"}, {"security":{}}, {"indexes":{}}):
            with self.subTest(change=change):
                metadata = self.metadata()
                metadata["v3.23"].update(change)
                self.assertEqual(self.assess(metadata=metadata)["update_status"], "unknown")

    def test_no_current_claim_when_comparison_fails(self):
        def fail(*args): raise u.UpdateError("APK failed")
        self.assertEqual(self.assess(compare=fail)["update_status"], "unknown")

    def test_missing_package_is_blocked(self):
        fact = self.fact()
        fact["packages"]["lost"] = {"version":"1", "origin":"lost"}
        self.assertEqual(self.assess(fact=fact)["update_status"], "blocked")

    def test_security_origin_applies_to_subpackages(self):
        metadata = self.metadata()
        metadata["v3.23"]["security"]["main"]["packages"] = [{"pkg":{"name":"linux-virt", "secfixes":{"2":["CVE-EXAMPLE"]}}}]
        result = self.assess(metadata=metadata)
        self.assertEqual(result["security_fixes"][0]["issues"], ["CVE-EXAMPLE"])
        self.assertIn("security.blocked", [v["code"] for v in result["findings"]])

    def test_main_support_never_extends_community(self):
        metadata = self.metadata()
        branch = metadata["v3.23"]["releases"]["release_branches"][0]
        branch["repos"][1]["eol_date"] = "2026-06-01"
        result = self.assess(metadata=metadata)
        self.assertEqual(result["support"], {"main":"supported", "community":"unsupported"})
        self.assertEqual(result["update_status"], "blocked")
        del branch["repos"][1]["eol_date"]
        self.assertEqual(self.assess(metadata=metadata)["support"]["community"], "unknown")

    def test_inventory_all_profiles_without_replacement(self):
        for change in ({"role":"ops"}, {"role":"router"}, {"role":"dedicated"}, {"os":{"id":"debian"}}, {"os":{"id":"ubuntu"}}):
            fact = self.fact(); fact.update(change)
            result = self.assess(fact=fact)
            self.assertEqual(result["profile"], "unsupported")
            self.assertFalse(result["replacement_ready"])

    def test_apk_records_reject_ambiguous_versions_and_bad_formats(self):
        good = "P:linux-virt\nV:6.12.1-r0\no:linux-lts\nA:x86_64\n"
        self.assertEqual(u.parse_apk_database(good)["linux-virt"]["origin"], "linux-lts")
        for bad in ("", "ADB.bad", "P:a\n", "P:a\nP:b\nV:1", good + "\n" + good, "P:$(id)\nV:1"):
            with self.subTest(value=bad), self.assertRaises(u.UpdateError): u.parse_apk_database(bad)

    def test_health_thresholds_are_exact_and_do_not_mask_failure(self):
        report = {"kind":u.REPORT_KIND, "generated_at":u.timestamp(NOW - dt.timedelta(hours=30)), "findings":[], "hosts":[]}
        verification = {"generated_at":u.timestamp(NOW - dt.timedelta(hours=2)), "findings":[]}
        self.assertEqual(u.health(report, verification, NOW), [])
        self.assertEqual(len(u.health(report, verification, NOW + dt.timedelta(seconds=1))), 2)
        verification["findings"] = [u.findings("replacement.failed", "failed", "critical")]
        self.assertIn("replacement.failed", [v["code"] for v in u.health(report, verification, NOW)])


class SafetyRulesTests(unittest.TestCase):
    def test_one_branch_at_a_time(self):
        self.assertEqual(u.next_branch("v3.21", ["v3.23", "v3.24"]), "v3.21")
        self.assertEqual(u.next_branch("v3.21", ["v3.22", "v3.23"]), "v3.22")
        for value in ("edge", "latest-stable", "3.23", "v3.23/../../edge"):
            with self.assertRaises(u.UpdateError): u.next_branch(value, [])

    def test_replacement_and_recovery_fit_window(self):
        policy = {"maintenance-window":{"start":"02:00", "end":"04:00", "last-start":"03:00"}, "replacement-minutes":30, "recovery-minutes":30}
        for hour, minute, second, allowed in ((1,59,59,False), (2,0,0,True), (3,0,0,True), (3,0,1,False), (4,0,0,False)):
            self.assertEqual(u.replacement_window(NOW.replace(hour=hour, minute=minute, second=second), policy), allowed)

    def test_dependencies_are_closed_acyclic_and_independent(self):
        graph = {v:[] for v in ("controller", "dom0", "dns", "artifacts", "recovery")}
        u.check_dependencies("boxa-bak", graph)
        for edge in ("boxa-bak", "missing", "artifacts"):
            bad = copy.deepcopy(graph); bad["artifacts"] = [edge]
            with self.assertRaises(u.UpdateError): u.check_dependencies("boxa-bak", bad)
        bad = copy.deepcopy(graph); bad["dns"]=["controller"]; bad["controller"]=["dns"]
        with self.assertRaises(u.UpdateError): u.check_dependencies("boxa-bak", bad)

    def test_failure_at_every_stage_preserves_post_acceptance_writes(self):
        for stage in u.STAGES:
            action = u.recovery_action({"stage":stage})
            if u.STAGES.index(stage) >= u.STAGES.index("accepted"):
                self.assertEqual(action, "preserve-production-data")
            elif stage == "stopped":
                self.assertEqual(action, "restart-recorded-old-release")
            elif u.STAGES.index(stage) >= u.STAGES.index("checkpointed"):
                self.assertEqual(action, "restore-recorded-checkpoint-and-old-release")
        with self.assertRaises(u.UpdateError): u.recovery_action({"stage":"unknown"})

    def test_deadline_and_acceptance_cannot_be_bypassed(self):
        journal = {"stage":"staged", "deadline":u.timestamp(NOW + dt.timedelta(minutes=30))}
        with self.assertRaises(u.UpdateError): u.transition(journal, "booted", NOW)
        with self.assertRaises(u.UpdateError): u.transition(journal, "armed", NOW + dt.timedelta(minutes=30))
        journal["stage"]="tested"
        with self.assertRaises(u.UpdateError): u.transition(journal, "accepted", NOW)
        journal.update(checks_passed=True, checkpoint_healthy=True)
        accepted = u.transition(journal, "accepted", NOW)
        self.assertEqual(u.transition(accepted, "opened", NOW + dt.timedelta(hours=3))["stage"], "opened")

    def test_release_requires_matching_boot_artifacts(self):
        artifacts = {v:"a"*64 for v in ("root", "kernel", "initramfs")}
        release = {"kind":"klokast.vm-release.v1", "engine_commit":"a"*40, "profile":"shared-alpine-v1", "branch":"v3.23", "architecture":"x86_64",
                   "packages":{v:"1" for v in ("linux-virt", "tailscale", "podman")}, "artifacts":artifacts, "kernel_release":"6.12-virt", "modules_release":"6.12-virt", "inputs_sha256":"b"*64,
                   "tests":{v:True for v in ("boot", "kernel_modules", "tailscale", "rootless_podman", "firewall", "application_compatibility", "no_machine_identity")}}
        release["release_sha256"] = u.digest(release)
        u.validate_release(release, "a"*40, "shared-alpine-v1", artifacts)
        for change in ({"modules_release":"old"}, {"branch":"latest-stable"}, {"engine_commit":"b"*40}, {"packages":{}}, {"tests":{}}, {"release_sha256":"0"*64}):
            bad = {**release, **change}
            with self.subTest(change=change), self.assertRaises(u.UpdateError): u.validate_release(bad, "a"*40, "shared-alpine-v1", artifacts)
        with self.assertRaises(u.UpdateError): u.validate_release(release, "a"*40, "shared-alpine-v1", {**artifacts, "kernel":"c"*64})


class ControllerTests(unittest.TestCase):
    def test_collection_uses_tailnet_names_local_controller_and_preserves_stopped(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as temporary, patch.object(cli, "command") as command, patch.object(cli.socket, "gethostname", return_value="boxa-ops"):
            root=Path(temporary)
            targets={"boxa-bak":{"role":"bak", "runtime":"running"}, "boxa-ops":{"role":"ops", "runtime":"running"}, "boxa-iot":{"role":"iot", "runtime":"stopped"}}
            cli.collect_facts(targets, root, None, "example.ts.net")
            hosts=json.loads((root / "inventory.json").read_text())["all"]["hosts"]
            self.assertNotIn("boxa-iot", hosts)
            self.assertEqual(hosts["boxa-bak"]["ansible_host"], "boxa-bak.example.ts.net")
            self.assertEqual(hosts["boxa-ops"]["ansible_connection"], "local")
            self.assertEqual(command.call_args[0][0][0], "ansible-playbook")

    def test_failed_source_reader_invalidates_the_previous_scan(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = {"kind":u.REPORT_KIND, "generated_at":u.timestamp(NOW), "hosts":[{"host":"old"}]}
            (root / "current.json").write_text(json.dumps(old))
            def command(argv, **kwargs):
                if str(argv[0]) == "git": return "a"*40
                raise u.UpdateError("source check failed")
            with patch.object(cli, "STATE", root), patch.object(cli, "CACHE", root), patch.object(cli, "require_controller"), patch.object(cli, "command", side_effect=command):
                report = cli.scan()
            self.assertFalse(report["complete"])
            self.assertEqual(report["hosts"], [])
            self.assertEqual(report["findings"][0]["code"], "discovery.failed")
            self.assertEqual(json.loads((root / "current.json").read_text()), report)

    def test_target_inventory_includes_stopped_and_unclassified_guests(self):
        cli = load_cli()
        mapping = {"boxes":{"boxa":{"machines":{"ops":{}}, "dom0":{"xen":{"available":True, "domains":[{"name":"Domain-0"}, {"name":"bak"}, {"name":"torrent"}], "config_files":["/etc/xen/iot.cfg"]}}, "app_vms":{"boxa-usr-test":{"app":"test"}}}}}
        targets = cli.targets_from_map(mapping)
        self.assertEqual(targets["boxa-bak"]["runtime"], "running")
        self.assertEqual(targets["boxa-iot"]["runtime"], "stopped")
        self.assertEqual(targets["boxa-torrent"]["role"], "unknown")
        self.assertEqual(targets["boxa-usr-test"]["role"], "dedicated")
        self.assertNotIn("boxa-Domain-0", targets)

    def test_guard_refuses_legacy_unconfigured_authority(self):
        cli = load_cli()
        with patch.object(cli, "command", return_value=json.dumps({"active":True, "configured":False})):
            with self.assertRaises(u.UpdateError): cli.require_controller()

    def test_native_signature_failure_does_not_accept_evidence(self):
        profile = json.loads((REPO / "ansible/update-profiles/shared-alpine-v1.json").read_text())
        with tempfile.TemporaryDirectory() as temporary, patch.object(m, "fetch_json", return_value=({"release_branches":[{"rel_branch":"v3.23"}]}, "a"*64)), patch.object(m.Path, "glob", return_value=[Path("example.pub")]), patch.object(m.shutil, "copyfile"), patch.object(m.Path, "read_bytes", return_value=b"public-key"), patch.object(m, "invoke", side_effect=u.UpdateError("signature failure")):
            result = m.collect_branch("v3.23", temporary, profile, NOW)
            self.assertFalse(result["signature_verified"])
            self.assertIn("signature", result["error"])

    def test_duplicate_json_fields_are_rejected(self):
        with self.assertRaises(u.UpdateError): json.loads('{"enabled":false,"enabled":true}', object_pairs_hook=u.unique_object)


if __name__ == "__main__": unittest.main()
