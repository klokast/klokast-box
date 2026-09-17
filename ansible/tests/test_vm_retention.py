"""Retention intent never grants storage adoption or suppresses refusals."""
import copy
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_instance_verification as authority_fixture
from test_platform_updates import NOW, load_cli
from test_vm_storage_inventory import CATALOG, fact
import vm_retention as retention
import vm_storage_inventory as storage
from platform_updates import REPORT_KIND, UpdateError, digest, timestamp


def source():
    projection = {'boxes': ['boxa', 'boxb'], 'datasets': [
        {'app': 'music', 'dataset': 'library', 'box': 'boxa', 'retention': 'preserve', 'desired_state': 'absent'}]}
    return {'schema_version': 1, 'kind': 'klokast.vm-retention-source.v1', 'source': 'instance_specification_v1',
            'authority_state_sha256': 'a' * 64, 'engine_commit': 'b' * 40, 'private_commit': 'c' * 40,
            'inputs': [{'path': name, 'sha256': 'd' * 64} for name in ('klokast-instance.json', 'klokast.lock.json')],
            'projection': projection, 'projection_sha256': digest(projection), 'adoption_authorized': False}


def discovery():
    return {'kind': REPORT_KIND, 'complete': True, 'generated_at': timestamp(NOW), 'implementation_commit': 'b' * 40,
            'hosts': [{'host': 'boxa-bak', 'target': {'box': 'boxa', 'role': 'bak', 'runtime': 'running'},
                       'storage_assessment': storage.assess(fact(), [CATALOG], 'boxa-bak')}], 'findings': []}


class RetentionSourceTests(unittest.TestCase):
    def setUp(self):
        fixture = authority_fixture.InstanceVerificationTest()
        fixture.setUp()
        self.m = fixture.m
        self.private = {'boxes': {'boxb': {}, 'boxa': {}}, 'apps': {
            'music': {'desired-state': 'absent', 'data': {'library': {'box': 'boxa', 'retention': 'preserve'}}}},
            'inactive-apps': {'music': {'placement': {'boxes': ['boxb']}}},
            'tailscale': {'members': {'do-not-emit@example.invalid': {}}}}
        self.raw = json.dumps(self.private).encode()
        self.lock = b'{"engine":"test-only"}'
        self.evidence = {'source': 'instance_specification_v1', 'authority_state_sha256': 'a' * 64,
                         'engine_commit': 'b' * 40, 'rendered': {'repository': {'head_commit': 'c' * 40},
                         'inputs': [{'path': name, 'sha256': self.m.sha256_bytes(value)} for name, value in
                                    [('klokast-instance.json', self.raw), ('klokast.lock.json', self.lock)]]}}

    def read(self, path):
        self.assertIn(path, (self.m.INSTANCE / 'klokast-instance.json', self.m.INSTANCE / 'klokast.lock.json'))
        return self.raw if path.name == 'klokast-instance.json' else self.lock

    def test_exact_checked_files_project_absent_app_data_without_private_details_or_writes(self):
        m = self.m
        with patch.object(m, 'inventory_source_status', return_value=self.evidence) as check, \
                patch.object(m, 'read_regular', side_effect=self.read), patch.object(m, 'vm_update_store') as store:
            result = m.vm_retention_status()
        retention.validate_source(result)
        self.assertEqual(result['projection'], source()['projection'])
        self.assertFalse(result['adoption_authorized'])
        self.assertNotIn('do-not-emit', json.dumps(result))
        self.assertNotIn('placement', json.dumps(result))
        self.assertEqual(check.call_count, 2)
        store.assert_not_called()

    def test_legacy_missing_mismatched_or_duplicate_input_evidence_is_refused(self):
        for change in (
            lambda v: v.update(source='legacy_engine_inventory'),
            lambda v: v['rendered'].update(inputs=[]),
            lambda v: v['rendered']['inputs'][0].update(sha256='0' * 64),
            lambda v: v['rendered']['inputs'][1].update(sha256='0' * 64),
            lambda v: v['rendered']['inputs'].__setitem__(1, v['rendered']['inputs'][0]),
        ):
            evidence = copy.deepcopy(self.evidence)
            change(evidence)
            with self.subTest(evidence=evidence), patch.object(self.m, 'inventory_source_status', return_value=evidence), \
                    patch.object(self.m, 'read_regular', side_effect=self.read), self.assertRaises(self.m.ApplyError):
                self.m.vm_retention_status()

    def test_source_engine_controller_and_raw_file_changes_are_refused(self):
        for field in ('source', 'engine_commit', 'authority_state_sha256', 'private_commit', 'raw', 'lock'):
            second = copy.deepcopy(self.evidence)
            if field == 'private_commit':
                second['rendered']['repository']['head_commit'] = 'e' * 40
            elif field not in ('raw', 'lock'):
                second[field] = 'changed'
            values = [self.raw, self.lock, self.raw + b' ' if field == 'raw' else self.raw,
                      self.lock + b' ' if field == 'lock' else self.lock]
            with self.subTest(field=field), patch.object(self.m, 'inventory_source_status', side_effect=[self.evidence, second]), \
                    patch.object(self.m, 'read_regular', side_effect=values), self.assertRaisesRegex(self.m.ApplyError, 'changed'):
                self.m.vm_retention_status()

    def test_sealed_reader_refusals_propagate_without_fallback(self):
        for reason in ('inactive controller', 'dirty checkout', 'changed engine', 'sealed binary missing'):
            with patch.object(self.m, 'inventory_source_status', side_effect=self.m.ApplyError(reason)), \
                    patch.object(self.m, 'read_regular') as read, self.assertRaisesRegex(self.m.ApplyError, reason):
                self.m.vm_retention_status()
            read.assert_not_called()

    def test_root_cli_accepts_no_caller_selected_input(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.m.parse_args(['vm-retention-status', '--instance', '/untrusted'])
        with patch.object(self.m, 'vm_retention_status', return_value=source()), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.m.main(['vm-retention-status']), 0)
        self.assertEqual(json.loads(output.getvalue()), source())


class RetentionReportTests(unittest.TestCase):
    def report(self, intent=None, observed=None, catalogs=None, commit='b' * 40):
        return retention.report(source() if intent is None else intent, discovery() if observed is None else observed,
                                [CATALOG] if catalogs is None else catalogs, commit, NOW)

    def codes(self, report):
        return {v['code'] for v in report['findings']}

    def test_absent_app_retention_is_reported_but_never_adopted(self):
        intent, observed = source(), discovery()
        before = copy.deepcopy((intent, observed))
        result = self.report(intent, observed)
        self.assertEqual(result['datasets'][0]['status'], 'observed')
        self.assertEqual(result['datasets'][0]['desired_state'], 'absent')
        self.assertEqual(len(result['datasets'][0]['observed_volumes']), 2)
        self.assertFalse(result['adoption_ready'])
        self.assertIn('retention.adoption-unverified', self.codes(result))
        self.assertEqual((intent, observed), before)

    def test_wrong_box_or_omitted_intent_never_retains_observed_volumes_implicitly(self):
        for absent in (False, True):
            intent = source()
            if absent:
                intent['projection']['datasets'] = []
            else:
                intent['projection']['datasets'][0]['box'] = 'boxb'
            intent['projection_sha256'] = digest(intent['projection'])
            result = self.report(intent)
            self.assertIn('retention.volume-undeclared', self.codes(result))
            self.assertFalse(result['adoption_ready'])
            if not absent:
                self.assertEqual(result['datasets'][0]['status'], 'unknown')

    def test_missing_whole_or_partial_dataset_is_visible(self):
        for count in (0, 1):
            observed = discovery()
            observed['hosts'][0]['storage_assessment']['volumes'] = observed['hosts'][0]['storage_assessment']['volumes'][:count]
            result = self.report(observed=observed)
            self.assertEqual(result['datasets'][0]['status'], 'missing')
            self.assertIn('retention.dataset-missing', self.codes(result))

    def test_unsafe_paths_and_storage_refusals_survive_declared_retention(self):
        observed = discovery()
        assessment = observed['hosts'][0]['storage_assessment']
        assessment['volumes'][0]['path_supported'] = False
        assessment['findings'].append({'code': 'storage.writable-layer', 'severity': 'critical', 'message': 'unaccounted'})
        result = self.report(observed=observed)
        self.assertIn('storage.writable-layer', self.codes(result))
        self.assertIn('retention.dataset-unsafe', self.codes(result))
        self.assertEqual(result['datasets'][0]['status'], 'unknown')

    def test_unknown_catalog_dataset_is_explicit(self):
        result = self.report(catalogs=[])
        self.assertEqual(result['datasets'][0]['status'], 'unsupported')
        self.assertIn('retention.dataset-unsupported', self.codes(result))

    def test_old_future_partial_or_different_engine_scan_cannot_match_data(self):
        for change in ({'generated_at': '2026-09-16T00:00:00Z'}, {'generated_at': '2026-09-18T00:00:00Z'},
                       {'complete': False}, {'implementation_commit': 'e' * 40}, {'hosts': None}, {'kind': 'unknown'}):
            observed = discovery()
            observed.update(change)
            result = self.report(observed=observed)
            self.assertIn('retention.discovery-unknown', self.codes(result))
            self.assertEqual(result['datasets'][0]['status'], 'unknown')
        result = self.report(commit='e' * 40)
        self.assertIn('retention.engine-mismatch', self.codes(result))
        self.assertEqual(result['datasets'][0]['status'], 'unknown')

    def test_missing_scan_and_stopped_or_different_catalog_storage_stay_unknown(self):
        missing = retention.report(source(), None, [CATALOG], 'b' * 40, NOW)
        self.assertIn('retention.discovery-unknown', self.codes(missing))
        for mode in ('stopped', 'missing', 'catalog'):
            observed = discovery()
            if mode == 'stopped':
                observed['hosts'][0]['target']['runtime'] = 'stopped'
            elif mode == 'missing':
                del observed['hosts'][0]['storage_assessment']
            else:
                observed['hosts'][0]['storage_assessment']['catalog_sha256'] = 'e' * 64
            result = self.report(observed=observed)
            self.assertEqual(result['datasets'][0]['status'], 'unknown')
            self.assertIn('retention.storage-unknown', self.codes(result))

    def test_duplicate_targets_or_volumes_are_refused(self):
        for mode in ('targets', 'volumes'):
            observed = discovery()
            if mode == 'targets':
                observed['hosts'].append(observed['hosts'][0])
            else:
                volumes = observed['hosts'][0]['storage_assessment']['volumes']
                volumes.append(volumes[0])
            with self.assertRaises(UpdateError):
                self.report(observed=observed)

    def test_closed_source_rejects_tampering_and_expanded_authority(self):
        for change in (
            lambda v: v.update(adoption_authorized=True),
            lambda v: v.update(schema_version=True),
            lambda v: v.update(command='copy'),
            lambda v: v.update(inputs=[]),
            lambda v: v.update(projection_sha256='0' * 64),
            lambda v: v['projection']['datasets'].append(v['projection']['datasets'][0]),
            lambda v: v['projection']['datasets'][0].update(retention='delete'),
        ):
            intent = source()
            change(intent)
            with self.assertRaises(UpdateError):
                self.report(intent=intent)

    def test_malformed_observations_have_bounded_errors(self):
        for change in (
            lambda v: v['hosts'].__setitem__(0, None),
            lambda v: v['hosts'][0].update(target=None),
            lambda v: v['hosts'][0]['target'].update(role=[]),
            lambda v: v['hosts'][0]['storage_assessment']['volumes'].append(None),
            lambda v: v['hosts'][0]['storage_assessment']['findings'].append(None),
        ):
            observed = discovery()
            change(observed)
            with self.assertRaises(UpdateError):
                self.report(observed=observed)


class RetentionCLITests(unittest.TestCase):
    def test_cli_binds_fresh_source_and_never_writes_scan_or_runs_mutations(self):
        cli = load_cli()
        for changed in (False, True):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                scan = root / 'current.json'
                scan.write_text(json.dumps(discovery()))
                before = scan.read_bytes()
                calls = []
                def command(argv, **kwargs):
                    calls.append(argv)
                    if argv[0] == 'git':
                        return '' if 'status' in argv else 'b' * 40
                    self.assertEqual(argv, ['/usr/bin/doas', '/usr/local/sbin/ksa-apply', 'vm-retention-status'])
                    result = source()
                    if changed and len([v for v in calls if v[0] != 'git']) == 2:
                        result['private_commit'] = 'e' * 40
                    return json.dumps(result)
                with patch.object(cli, 'STATE', root), patch.object(cli, 'require_controller'), \
                        patch.object(cli, 'command', side_effect=command), patch.object(cli, 'now', return_value=NOW), \
                        redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main(['retention', '--json']), 2 if changed else 1)
                self.assertEqual(scan.read_bytes(), before)
                self.assertEqual(list(root.iterdir()), [scan])
                if changed:
                    self.assertEqual(output.getvalue(), '')
                else:
                    self.assertFalse(json.loads(output.getvalue())['adoption_ready'])

    def test_dirty_checkout_or_unavailable_reader_produces_no_report(self):
        cli = load_cli()
        for mode in ('dirty', 'unavailable'):
            def command(argv, **kwargs):
                if argv[0] == 'git':
                    return (' M file' if mode == 'dirty' else '') if 'status' in argv else 'b' * 40
                raise UpdateError('installed reader unavailable')
            with patch.object(cli, 'require_controller'), patch.object(cli, 'command', side_effect=command), \
                    redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['retention', '--json']), 2)
            self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
