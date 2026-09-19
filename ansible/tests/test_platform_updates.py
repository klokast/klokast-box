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
    def test_automatic_prepare_transfers_one_exact_candidate_to_other_box(self):
        cli = load_cli()
        commit = 'c' * 40
        selection = {'kind': 'klokast.vm-update-auto-selection.v1', 'branch': 'v3.24',
                     'build_box': 'k001', 'targets': ['k001-dmz', 'k002-dmz', 'k002-iot'],
                     'policy_sha256': 'a' * 64, 'activation_sha256': 'b' * 64,
                     'engine_commit': commit}
        source = {'policy': 'checked'}
        discovery, metadata = {'complete': True}, {'signed': True}
        candidate = {'artifacts': {'root': {'sha256': 'd' * 64, 'bytes': 1}}}
        release = {'kind': 'klokast.vm-release.v2', 'release_sha256': 'e' * 64,
                   'artifacts': candidate['artifacts'], 'engine_commit': commit,
                   'branch': 'v3.24',
                   'application_tests': {'status': 'not-run', 'executed': False}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, value in (('candidate.json', candidate),
                                ('release-evidence.json', release)):
                (root / name).write_text(json.dumps(value))
            built = {'state': 'candidate-built', 'accepted': False,
                     'result_directory': str(root), 'operation_id': 'f' * 24,
                     'inputs_sha256': '9' * 64,
                     'release_evidence_sha256': release['release_sha256']}
            def command(argv, **kwargs):
                if argv[:3] == ['git', '-C', cli.REPO] and argv[3] == 'rev-parse':
                    return commit + '\n'
                return ''
            with patch.object(cli, 'require_controller'), \
                    patch.object(cli, 'STATE', root), \
                    patch.object(cli, 'command', side_effect=command), \
                    patch.object(cli, 'read_policy_source', return_value=source), \
                    patch.object(cli, 'optional', side_effect=[discovery, metadata,
                                                               discovery, metadata,
                                                               discovery, metadata]), \
                    patch.object(cli, 'automatic_selection', return_value=selection), \
                    patch.object(cli, 'reuse_auto_candidate', return_value=None), \
                    patch.object(cli, 'prepare', return_value=built), \
                    patch.object(cli.vm_artifact_transfer, 'transfer',
                                 return_value={'target_box': 'k002', 'accepted': False}) as transfer:
                result = cli.prepare_auto()
            self.assertEqual(result['transfers'], [{'target_box': 'k002', 'accepted': False}])
            self.assertEqual(transfer.call_count, 1)
            self.assertEqual(transfer.call_args.args[3], 'k002')
            self.assertTrue((root / 'transfer-k002.json').is_file())
            self.assertEqual(json.loads((root / 'automatic.json').read_text())['operation_id'], 'f' * 24)

    def test_automatic_prepare_reuses_only_a_complete_build_with_current_signed_indexes(self):
        cli = load_cli()
        operation = 'f' * 24
        selection = {'branch': 'v3.24', 'build_box': 'k001',
                     'targets': ['k001-dmz', 'k002-dmz', 'k002-iot'],
                     'engine_commit': 'c' * 40}
        profile = {'packages': ['linux-virt'], 'repositories': ['main', 'community']}
        indexes = {'one': '1' * 64, 'two': '2' * 64}
        inputs = {'engine_commit': selection['engine_commit'], 'branch': 'v3.24',
                  'profile_sha256': u.digest(profile), 'world': ['linux-virt'],
                  'keys': {'alpine.pub': '3' * 64}, 'indexes': indexes}
        inputs['inputs_sha256'] = u.digest(inputs)
        candidate = {'kind': 'klokast.vm-template-candidate.v1',
                     'operation_id': operation, 'box': 'k001', 'success': True,
                     'accepted': False, 'inputs_sha256': inputs['inputs_sha256'],
                     'artifacts': {'root': {'sha256': '4' * 64, 'bytes': 1},
                                   'kernel': {'sha256': '5' * 64, 'bytes': 1},
                                   'initramfs': {'sha256': '6' * 64, 'bytes': 1}}}
        release = {'release_sha256': '7' * 64}
        transfer = {'kind': 'klokast.vm-template-transfer.v1',
                    'operation_id': operation, 'source_box': 'k001',
                    'target_box': 'k002', 'artifacts': candidate['artifacts'],
                    'accepted': False}
        pointer = {'kind': 'klokast.vm-update-auto-build.v1',
                   'selection': selection, 'operation_id': operation,
                   'inputs_sha256': inputs['inputs_sha256'],
                   'release_sha256': release['release_sha256']}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'builds' / operation
            directory.mkdir(parents=True)
            for path, value in ((root / 'automatic.json', pointer),
                                (root / 'profile.json', profile),
                                (directory / 'inputs.json', inputs),
                                (directory / 'candidate.json', candidate),
                                (directory / 'release-evidence.json', release),
                                (directory / 'transfer-k002.json', transfer)):
                path.write_text(json.dumps(value))
            metadata = {'signature_verified': True, 'observed_at': u.timestamp(dt.datetime.now(dt.timezone.utc)),
                        'inputs_sha256': {'main:index': '1' * 64, 'community:index': '2' * 64}}
            with patch.object(cli, 'STATE', root), patch.object(cli, 'PROFILE', root / 'profile.json'), \
                    patch.object(cli, 'installed_apk_keys', return_value=inputs['keys']), \
                    patch.object(cli, 'collect_branch', return_value=metadata), \
                    patch.object(cli, 'no_application_release', return_value=release), \
                    patch.object(cli.vm_artifact_transfer, 'verify_published') as verify:
                result = cli.reuse_auto_candidate(selection)
                self.assertEqual(result['state'], 'unchanged')
                self.assertEqual(verify.call_count, 2)
                changed = copy.deepcopy(metadata)
                changed['inputs_sha256']['main:index'] = '8' * 64
                with patch.object(cli, 'collect_branch', return_value=changed):
                    self.assertIsNone(cli.reuse_auto_candidate(selection))
                self.assertEqual(verify.call_count, 2)

    def test_adjacent_branch_selection_ignores_expired_source_and_skips_no_branch(self):
        releases = {'release_branches': [
            {'rel_branch': 'v3.23', 'git_branch': '3.23-stable',
             'branch_date': '2025-12-03', 'eol_date': '2027-11-01',
             'arches': ['x86_64'], 'repos': [{'name': 'main'},
                                              {'name': 'community', 'eol_date': '2026-05-01'}],
             'releases': [{'version': '3.23.6', 'date': '2026-09-17'}]},
            {'rel_branch': 'v3.24', 'git_branch': '3.24-stable',
             'branch_date': '2026-06-09', 'eol_date': '2028-06-01',
             'arches': ['x86_64'], 'repos': [{'name': 'main'},
                                              {'name': 'community', 'eol_date': '2026-11-01'}],
             'releases': [{'version': '3.24.2', 'date': '2026-09-17'}]}]}
        self.assertEqual(m.adjacent_stable_branch('v3.23', releases, NOW), 'v3.24')
        self.assertIsNone(m.adjacent_stable_branch('v3.21', releases, NOW))
        changed = copy.deepcopy(releases)
        changed['release_branches'][1]['repos'][1]['eol_date'] = '2026-05-01'
        self.assertIsNone(m.adjacent_stable_branch('v3.23', changed, NOW))
        changed = copy.deepcopy(releases)
        changed['release_branches'][1]['arches'] = ['aarch64']
        with self.assertRaises(u.UpdateError):
            m.adjacent_stable_branch('v3.23', changed, NOW)
        changed = copy.deepcopy(releases)
        changed['release_branches'].append(changed['release_branches'][1])
        with self.assertRaises(u.UpdateError):
            m.adjacent_stable_branch('v3.23', changed, NOW)

    def test_automatic_selection_uses_only_the_signed_selected_shared_targets(self):
        cli = load_cli()
        releases = {'release_branches': [
            {'rel_branch': 'v3.24', 'git_branch': '3.24-stable', 'branch_date': '2026-06-09',
             'eol_date': '2028-06-01', 'arches': ['x86_64'],
             'repos': [{'name': 'main'}, {'name': 'community', 'eol_date': '2026-11-01'}],
             'releases': [{'version': '3.24.2', 'date': '2026-09-17'}]}]}
        targets = [('k001', 'dmz'), ('k002', 'dmz'), ('k002', 'iot')]
        report = {'complete': True, 'generated_at': u.timestamp(NOW), 'hosts': [
            {'host': box + '-' + role, 'profile': 'shared-alpine-v1', 'branch': 'v3.23',
             'target': {'box': box, 'role': role, 'runtime': 'running'}}
            for box, role in targets]}
        metadata = {'v3.23': {'signature_verified': True, 'observed_at': u.timestamp(NOW), 'releases': releases}}
        policy = {'enabled': True, 'targets': {'k001': ['dmz'], 'k002': ['dmz', 'iot']},
                  'exclusions': [], 'branch-policy': 'tested-stable',
                  'maintenance-window': {'start': '02:00', 'end': '04:00', 'last-start': '03:00'},
                  'canary-hours': 24, 'replacement-minutes': 30, 'recovery-minutes': 30}
        source = {'kind': 'klokast.vm-update-policy-source.v1', 'policy': policy,
                  'policy_sha256': 'a' * 64, 'activation_sha256': 'b' * 64,
                  'engine_commit': 'c' * 40, 'private_commit': 'd' * 40,
                  'authority_state_sha256': 'e' * 64, 'paused': False,
                  'replacement_executor_available': False}
        selected = cli.automatic_selection(report, metadata, source, 'c' * 40, NOW)
        self.assertEqual(selected['branch'], 'v3.24')
        self.assertEqual(selected['build_box'], 'k001')
        self.assertEqual(selected['targets'], ['k001-dmz', 'k002-dmz', 'k002-iot'])
        paused = copy.deepcopy(source); paused['paused'] = True
        with self.assertRaises(u.UpdateError):
            cli.automatic_selection(report, metadata, paused, 'c' * 40, NOW)
        incomplete = copy.deepcopy(report); incomplete['hosts'].pop()
        with self.assertRaises(u.UpdateError):
            cli.automatic_selection(incomplete, metadata, source, 'c' * 40, NOW)
        divergent = copy.deepcopy(report); divergent['hosts'][2]['branch'] = 'v3.22'
        with self.assertRaises(u.UpdateError):
            cli.automatic_selection(divergent, metadata, source, 'c' * 40, NOW)

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

    def test_multiple_index_versions_use_native_order(self):
        records="P:example\nV:9-r0\n\nP:example\nV:10-r0\n"
        calls=[]
        def compare(a,b):
            calls.append((a,b)); return "<"
        self.assertEqual(u.parse_apk_database(records, compare)["example"]["version"], "10-r0")
        self.assertEqual(calls, [("9-r0", "10-r0")])

    def test_health_thresholds_are_exact_and_do_not_mask_failure(self):
        report = {"kind":u.REPORT_KIND, "generated_at":u.timestamp(NOW - dt.timedelta(hours=30)), "findings":[], "hosts":[], "complete":True}
        verification = {"generated_at":u.timestamp(NOW - dt.timedelta(hours=2)), "findings":[]}
        self.assertEqual(u.health(report, verification, NOW), [])
        self.assertEqual(len(u.health(report, verification, NOW + dt.timedelta(seconds=1))), 2)
        verification["findings"] = [u.findings("replacement.failed", "failed", "critical")]
        self.assertIn("replacement.failed", [v["code"] for v in u.health(report, verification, NOW)])
        report["complete"] = False
        self.assertIn("discovery.incomplete", [v["code"] for v in u.health(report, verification, NOW)])


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

    def test_no_application_release_binds_full_manifest_and_records_omitted_app_tests(self):
        manifest = [{'name': name, 'version': '1-r0', 'origin': name,
                     'architecture': 'x86_64', 'file': 'packages/' + name + '-1-r0.apk',
                     'bytes': 100, 'sha256': 'd' * 64}
                    for name in ('linux-virt', 'podman', 'tailscale')]
        inputs = {'kind': 'klokast.vm-template-inputs.v1', 'engine_commit': 'a' * 40,
                  'profile': 'shared-alpine-v1', 'branch': 'v3.24',
                  'architecture': 'x86_64', 'packages': manifest}
        inputs['inputs_sha256'] = u.digest(inputs)
        candidate = {'success': True, 'accepted': False,
                     'inputs_sha256': inputs['inputs_sha256'],
                     'tests': {'package_closure': True, 'no_machine_identity': True},
                     'artifacts': {name: {'sha256': 'c' * 64, 'bytes': 100}
                                   for name in ('root', 'kernel', 'initramfs')},
                     'kernel_release': '6.18-virt', 'modules_release': '6.18-virt',
                     'boot_test': {'success': True, 'tests': dict.fromkeys((
                         'boot', 'kernel_modules', 'tailscale_offline', 'rootless_podman',
                         'nftables_kernel', 'retained_data_copy', 'retained_data_stage',
                         'retained_identity', 'retained_partition', 'personalization',
                         'backup_restore'), True)}}
        normal = {'success': True, 'tests': dict.fromkeys((
            'openrc_boot', 'cgroup_v2', 'kernel_modules', 'tailscale_offline',
            'default_rootless_podman'), True)}
        personalized = {'success': True, 'tests': dict.fromkeys((
            'personalized_boot', 'configuration', 'retained_mount', 'runtime_identity',
            'tailscale_retained_state', 'firewall', 'default_rootless_podman',
            'packages_unchanged'), True)}
        maintenance = {'success': True}
        release = u.no_application_release(inputs, candidate, normal, personalized, maintenance)
        self.assertEqual(release['application_tests'], {'status': 'not-run', 'executed': False})
        self.assertEqual(release['package_manifest'], manifest)
        for change in ({'application_tests': {'status': 'passed', 'executed': False}},
                       {'tests': {**release['tests'], 'application_compatibility': True}},
                       {'package_manifest': manifest[:-1]},
                       {'packages': {'podman': '1-r0'}},
                       {'artifacts': {'root': release['artifacts']['root']}},
                       {'component_sha256': {**release['component_sha256'], 'openrc': '0' * 64}}):
            with self.subTest(change=change), self.assertRaises(u.UpdateError):
                u.validate_no_application_release({**release, **change}, inputs, candidate,
                                                   normal, personalized, maintenance)
        with self.assertRaises(u.UpdateError):
            u.validate_no_application_release(release, {**inputs, 'branch': 'v3.23'},
                                               candidate, normal, personalized, maintenance)
        failed_boot = copy.deepcopy(candidate)
        failed_boot['boot_test']['tests']['kernel_modules'] = False
        with self.assertRaises(u.UpdateError):
            u.no_application_release(inputs, failed_boot, normal, personalized, maintenance)


class ControllerTests(unittest.TestCase):
    def test_prepare_accepts_complete_stage_evidence_and_refuses_missing_or_failed_stage(self):
        cli = load_cli()
        operation = 'a' * 24
        packages = [{'name': name, 'version': '1', 'origin': name, 'architecture': 'x86_64',
                     'file': 'packages/' + name + '-1.apk', 'bytes': 100, 'sha256': 'd' * 64}
                    for name in ('linux-virt', 'podman', 'tailscale')]
        inputs = {'kind': 'klokast.vm-template-inputs.v1', 'engine_commit': 'a' * 40,
                  'profile': 'shared-alpine-v1', 'branch': 'v3.23', 'architecture': 'x86_64',
                  'packages': packages}
        inputs['inputs_sha256'] = u.digest(inputs)
        for mode in ('valid', 'missing-stage', 'failed-stage', 'missing-identity', 'failed-identity',
                     'missing-partition', 'failed-partition', 'missing-openrc', 'failed-openrc',
                     'changed-openrc-input', 'missing-openrc-cleanup', 'missing-personalization', 'failed-personalization',
                     'missing-profile', 'failed-profile', 'changed-profile-receipt', 'missing-profile-cleanup',
                     'missing-backup', 'failed-backup', 'missing-maintenance', 'failed-maintenance',
                     'changed-maintenance-receipt', 'missing-maintenance-cleanup'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                def command(argv, **kwargs):
                    if argv[0] == 'git':
                        return '' if 'status' in argv else 'a' * 40
                    if argv[0] == 'ansible-playbook':
                        tests = dict.fromkeys(('boot', 'kernel_modules', 'tailscale_offline', 'rootless_podman',
                                               'nftables_kernel', 'retained_data_copy', 'retained_data_stage', 'retained_identity', 'retained_partition', 'personalization', 'backup_restore'), True)
                        if mode == 'missing-stage':
                            del tests['retained_data_stage']
                        if mode == 'failed-stage':
                            tests['retained_data_stage'] = False
                        if mode == 'missing-identity':
                            del tests['retained_identity']
                        if mode == 'failed-identity':
                            tests['retained_identity'] = False
                        if mode == 'missing-partition':
                            del tests['retained_partition']
                        if mode == 'failed-partition':
                            tests['retained_partition'] = False
                        if mode == 'missing-personalization':
                            del tests['personalization']
                        if mode == 'failed-personalization':
                            tests['personalization'] = False
                        if mode == 'missing-backup':
                            del tests['backup_restore']
                        if mode == 'failed-backup':
                            tests['backup_restore'] = False
                        candidate = {'kind': 'klokast.vm-template-candidate.v1', 'accepted': False,
                                     'success': True, 'box': 'boxa', 'validation': 'base-boot-tested',
                                     'operation_id': operation, 'inputs_sha256': inputs['inputs_sha256'],
                                     'packages': {row['name']: row['version'] for row in packages},
                                     'kernel_release': 'test-kernel', 'modules_release': 'test-kernel',
                                     'tests': {'package_closure': True, 'no_machine_identity': True},
                                     'artifacts': {name: {'sha256': 'c' * 64, 'bytes': 100}
                                                   for name in ('root', 'kernel', 'initramfs')},
                                     'boot_test': {'kind': 'klokast.vm-template-test-result.v1', 'success': True,
                                         'operation_id': operation, 'inputs_sha256': inputs['inputs_sha256'],
                                         'kernel_release': 'test-kernel', 'tested_root_sha256': 'c' * 64,
                                         'tests': tests}}
                        normal = {'kind': 'klokast.vm-template-openrc-test.v1', 'success': mode != 'failed-openrc',
                                  'operation_id': operation, 'inputs_sha256': inputs['inputs_sha256'],
                                  'kernel_release': 'test-kernel', 'tests': dict.fromkeys(('openrc_boot', 'cgroup_v2',
                                      'kernel_modules', 'tailscale_offline', 'default_rootless_podman'), True)}
                        if mode == 'changed-openrc-input': normal['inputs_sha256'] = '0' * 64
                        if mode != 'missing-openrc': candidate['boot_test']['openrc_test'] = normal
                        prepared = {'kind': 'klokast.vm-template-personalize-stage.v1', 'success': True,
                                    'operation_id': operation, 'inputs_sha256': inputs['inputs_sha256'],
                                    'production_data_used': False, 'source_root_sha256': 'c' * 64,
                                    'request_sha256': '1' * 64, 'receipt_sha256': '2' * 64}
                        profile = {**prepared, 'kind': 'klokast.vm-template-personalized-test.v1',
                                   'kernel_release': 'test-kernel', 'success': mode != 'failed-profile',
                                   'tests': dict.fromkeys(('personalized_boot', 'configuration', 'retained_mount',
                                       'runtime_identity', 'tailscale_retained_state', 'firewall', 'default_rootless_podman', 'packages_unchanged'), True)}
                        if mode == 'changed-profile-receipt': profile['receipt_sha256'] = '0' * 64
                        candidate['boot_test']['personalization_stage'] = prepared
                        if mode != 'missing-profile': candidate['boot_test']['personalized_test'] = profile
                        restored = {'kind': 'klokast.vm-backup-restore-result.v1', 'request_sha256': '3' * 64,
                                    'complete_disk_restored': True, 'root_filesystem_checked': True, 'backup_unchanged': True,
                                    'source_freshness_verified': False, 'application_consistency_verified': False, 'adoption_accepted': False}
                        restored['receipt_sha256'] = u.digest(restored)
                        if mode == 'changed-maintenance-receipt': restored['receipt_sha256'] = '0' * 64
                        if mode != 'missing-maintenance':
                            candidate['boot_test']['maintenance_restore'] = {
                                'kind': 'klokast.vm-backup-restore-guest.v1', 'operation_id': operation,
                                'inputs_sha256': inputs['inputs_sha256'], 'request_sha256': '3' * 64,
                                'success': mode != 'failed-maintenance', 'restore': restored}
                        directory = root / 'builds' / operation
                        (directory / 'candidate.json').write_text(json.dumps(candidate))
                        for filename, domain in (('lifecycle.json', 'vm-build-'), ('test-lifecycle.json', 'vm-test-')):
                            record = {'stage': 'cleaned', 'domain': domain + operation}
                            if filename == 'test-lifecycle.json' and mode != 'missing-openrc-cleanup':
                                record['openrc_domain'] = 'vm-openrc-' + operation
                            if filename == 'test-lifecycle.json' and mode != 'missing-profile-cleanup':
                                record['personalize_domain'] = 'vm-personalize-' + operation
                                record['profile_domain'] = 'vm-profile-' + operation
                            if filename == 'test-lifecycle.json' and mode != 'missing-maintenance-cleanup':
                                record['restore_domain'] = 'vm-restore-' + operation
                            (directory / filename).write_text(json.dumps(record))
                    return '{}'
                with patch.object(cli, 'STATE', root), patch.object(cli, 'CACHE', root), \
                        patch.object(cli, 'require_controller'), patch.object(cli, 'command', side_effect=command), \
                        patch.object(cli.secrets, 'token_hex', return_value=operation), \
                        patch.object(cli.vm_template_inputs, 'freeze', return_value=inputs), \
                        patch.object(cli.vm_template_inputs, 'capsule', return_value={}), \
                        patch.object(cli.vm_template_inputs, 'personalization_fixture'), \
                        patch.object(cli.vm_template_inputs, 'bootstrap', return_value={}):
                    if mode == 'valid':
                        result = cli.prepare('boxa', 'v3.23')
                        self.assertEqual(result['state'], 'candidate-built')
                        self.assertFalse(result['accepted'])
                        self.assertTrue(result['base_tests']['retained_data_stage'])
                        self.assertTrue(result['base_tests']['retained_identity'])
                        self.assertTrue(result['openrc_tests']['default_rootless_podman'])
                        release = json.loads((root / 'builds' / operation / 'release-evidence.json').read_text())
                        self.assertEqual(result['release_evidence_sha256'], release['release_sha256'])
                        self.assertEqual(release['application_tests'], {'status': 'not-run', 'executed': False})
                    else:
                        with self.assertRaisesRegex(u.UpdateError, 'candidate or cleanup evidence'):
                            cli.prepare('boxa', 'v3.23')

    def test_scan_integrates_storage_refusals_and_catalog_without_adoption(self):
        from test_vm_storage_inventory import fact as storage_fact
        cli = load_cli()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.run/platform-map').mkdir(parents=True)
            mapping = {'generated_at': u.timestamp(NOW), 'tailnet': {'magicdns_suffix': 'example.ts.net'},
                       'boxes': {'boxa': {'dom0': {'xen': {'available': True, 'domains': [{'name': 'bak'}]}}}}}
            (root / '.run/platform-map/current.json').write_text(json.dumps(mapping))
            def collect(targets, directory, log, suffix):
                value = {**storage_fact(), 'observed_at': u.timestamp(NOW),
                         'os': {'id': 'alpine', 'version_id': '3.23.0'}, 'architecture': 'x86_64',
                         'apk_database': 'P:linux-virt\nV:1\nA:x86_64\n'}
                (directory / 'boxa-bak.json').write_text(json.dumps(value))
            with patch.object(cli, 'STATE', root), patch.object(cli, 'CACHE', root), \
                    patch.object(cli, 'APPROVED_REPO', root), patch.object(cli, 'require_controller'), \
                    patch.object(cli, 'command', return_value='a' * 40), patch.object(cli, 'now', return_value=NOW), \
                    patch.object(cli, 'collect_facts', side_effect=collect), \
                    patch.object(cli, 'collect_branch', return_value={}):
                report = cli.scan(refresh=False)
            self.assertTrue(report['complete'])
            bak = next(host for host in report['hosts'] if host['host'] == 'boxa-bak')
            self.assertFalse(bak['replacement_ready'])
            self.assertFalse(bak['storage_assessment']['adoption_ready'])
            self.assertEqual(bak['storage_assessment']['volumes'][0]['catalog_match']['dataset'], 'library')
            self.assertIn('storage.adoption-unverified', {f['code'] for f in bak['findings']})
            self.assertEqual(json.loads((root / 'current.json').read_text()), report)
            stopped = next(host for host in report['hosts'] if host['host'] == 'boxa-iot')
            self.assertNotIn('storage_assessment', stopped)

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
            self.assertFalse(hosts['boxa-ops']['vm_update_shared_storage'])
            self.assertTrue(hosts['boxa-bak']['vm_update_shared_storage'])
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
