"""Offline HA and native Git fixtures; never access a real controller."""
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_ha():
    loader = SourceFileLoader('reconstruction_ha', str(ROOT / 'ansible/bin/ops-controller-ha'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.ha = load_ha()
        self.config = {'remote_user': 'smith', 'repo_dir': '~/src/klokast/klokast-box',
                       'controllers': {box: {'box': box, 'hostname': box + '-ops'} for box in ('boxa', 'boxb')}}
        self.args = SimpleNamespace(old_active='boxa', new_active='boxb',
                                    old_active_fenced=True, old_credentials_revoked=True, dry_run_plan=False)
        self.standby = {'reachable': True, 'active': False, 'role': 'standby', 'active_box': 'boxa'}
        self.identity = {'schema_version': 1, 'kind': 'klokast.controller-identity-status.v1',
                         'source': 'instance', 'instance_sha256': 'a' * 64, 'engine_commit': 'b' * 40,
                         'controllers': {'active': self.config['controllers']['boxb'],
                                         'standby': self.config['controllers']['boxa']}}

    def repositories(self):
        base = {'available': True, 'clean': True, 'branch': 'main',
                'remote_reachable': True, 'up_to_date': True}
        return [dict(base, origin='https://github.com/klokast/klokast-box.git'),
                dict(base, origin='git@github.com:family/klokast-instance.git')]

    def test_readiness_uses_offline_instance_without_provider_credentials(self):
        with patch.object(self.ha, 'repository_status', side_effect=self.repositories()), \
                patch.object(self.ha, 'controller_sh', side_effect=[
                    SimpleNamespace(stdout='{"active":"boxb","standby":"boxa"}'), None]) as shell:
            self.ha.require_readiness(self.config, 'boxb', 'boxa')
        for call in shell.call_args_list:
            self.assertEqual(call.args[1], 'boxb')
        self.assertIn('klokast check --instance', shell.call_args_list[0].args[2])
        self.assertNotIn('tailscale-policy.env', shell.call_args_list[1].args[2])
        with patch.object(self.ha, 'controller_sh') as shell:
            self.ha.verify_credentials(self.config, 'boxb')
        credentials = shell.call_args.args[2]
        self.assertIn('stat.S_IMODE(info.st_mode) != 0o600', credentials)
        self.assertIn('--check-config', credentials)
        self.assertIn('--require-active', credentials)

    def test_invalid_or_unpushed_instance_cannot_change_a_marker(self):
        for placement, current in (({'active': 'boxa', 'standby': 'boxb'}, True),
                                   ({'active': 'boxb', 'standby': 'boxa'}, False)):
            repos = self.repositories()
            repos[1]['up_to_date'] = current
            with self.subTest(placement=placement), \
                    patch.object(self.ha, 'controller_status', return_value=self.standby), \
                    patch.object(self.ha, 'repository_status', side_effect=repos), \
                    patch.object(self.ha, 'controller_sh', return_value=SimpleNamespace(stdout=json.dumps(placement))), \
                    patch.object(self.ha, 'set_marker') as marker, self.assertRaises(SystemExit):
                self.ha.promote(self.config, self.args)
            marker.assert_not_called()

    def test_failed_development_prerequisite_stops_before_marker_write(self):
        with patch.object(self.ha, 'controller_status', return_value=self.standby), \
                patch.object(self.ha, 'repository_status', side_effect=self.repositories()), \
                patch.object(self.ha, 'controller_sh', side_effect=[
                    SimpleNamespace(stdout='{"active":"boxb","standby":"boxa"}'), SystemExit(1)]), \
                patch.object(self.ha, 'set_marker') as marker, self.assertRaises(SystemExit):
            self.ha.promote(self.config, self.args)
        marker.assert_not_called()

    def test_emergency_promotion_without_old_controller_or_history(self):
        with patch.object(self.ha, 'controller_status', side_effect=[self.standby, {'reachable': False}]), \
                patch.object(self.ha, 'require_readiness') as ready, \
                patch.object(self.ha, 'require_credentials_absent'), \
                patch.object(self.ha, 'verify_credentials'), \
                patch.object(self.ha, 'set_marker') as marker, \
                patch.object(self.ha, 'controller_sh', return_value=SimpleNamespace(stdout=json.dumps(self.identity))):
            self.ha.promote(self.config, self.args)
        ready.assert_called_once_with(self.config, 'boxb', 'boxa')
        marker.assert_called_once_with(self.config, 'boxb', 'active', 'boxb')

    def test_fencing_required_before_any_contact(self):
        self.args.old_active_fenced = False
        with patch.object(self.ha, 'controller_status') as status, self.assertRaises(SystemExit):
            self.ha.promote(self.config, self.args)
        status.assert_not_called()

    def test_contact_timeout_is_reported_as_unreachable(self):
        with patch.object(self.ha, 'controller_sh', side_effect=subprocess.TimeoutExpired('fixture', 120)):
            status = self.ha.controller_status(self.config, 'boxa')
        self.assertFalse(status['reachable'])
        self.assertIn('timed out', status['error'])

    def test_promotion_main_does_not_resolve_the_failed_active_source(self):
        with patch.object(self.ha, 'source_config') as source, patch.object(self.ha, 'promote') as promote:
            self.ha.main(['promote', '--old-active', 'boxa', '--new-active', 'boxb', '--old-active-fenced'])
        source.assert_not_called()
        self.assertEqual(set(promote.call_args.args[0]['controllers']), {'boxa', 'boxb'})

    def test_switchover_demotes_and_verifies_old_before_activation(self):
        events = []
        statuses = [self.standby, dict(reachable=True, active=True, role='active'), dict(self.standby, active_box='boxb')]
        with patch.object(self.ha, 'controller_status', side_effect=statuses), \
                patch.object(self.ha, 'require_readiness'), \
                patch.object(self.ha, 'require_credentials_absent'), \
                patch.object(self.ha, 'set_marker', side_effect=lambda cfg, box, role, active: events.append((box, role))), \
                patch.object(self.ha, 'verify_promotion'):
            self.ha.switchover(self.config, self.args)
        self.assertEqual(events, [('boxa', 'standby'), ('boxb', 'active')])

    def test_uncertain_demotion_never_activates_new_controller(self):
        statuses = [self.standby, dict(reachable=True, active=True, role='active'), {'reachable': False}]
        with patch.object(self.ha, 'controller_status', side_effect=statuses), \
                patch.object(self.ha, 'require_readiness'), patch.object(self.ha, 'require_credentials_absent'), patch.object(self.ha, 'set_marker') as marker, \
                self.assertRaises(SystemExit):
            self.ha.switchover(self.config, self.args)
        marker.assert_called_once_with(self.config, 'boxa', 'standby', 'boxb')

    def test_unrevoked_credentials_stop_before_any_contact(self):
        self.args.old_credentials_revoked = False
        with patch.object(self.ha, 'controller_status') as status, self.assertRaises(SystemExit):
            self.ha.switchover(self.config, self.args)
        status.assert_not_called()

    def test_old_credentials_present_prevent_demotion(self):
        with patch.object(self.ha, 'controller_status', side_effect=[self.standby,
                         dict(reachable=True, active=True, role='active')]), \
                patch.object(self.ha, 'require_readiness'), \
                patch.object(self.ha, 'require_credentials_absent', side_effect=SystemExit(2)), \
                patch.object(self.ha, 'set_marker') as marker, self.assertRaises(SystemExit):
            self.ha.switchover(self.config, self.args)
        marker.assert_not_called()

    def test_missing_new_credentials_leave_a_resumable_incomplete_handoff(self):
        statuses = [dict(reachable=True, active=True, role='active', active_box='boxb'),
                    dict(self.standby, active_box='boxb')]
        with patch.object(self.ha, 'controller_status', side_effect=statuses), \
                patch.object(self.ha, 'require_readiness'), \
                patch.object(self.ha, 'require_credentials_absent'), \
                patch.object(self.ha, 'set_marker') as marker, \
                patch.object(self.ha, 'controller_sh', return_value=SimpleNamespace(stdout=json.dumps(self.identity))), \
                patch.object(self.ha, 'verify_credentials', side_effect=SystemExit(1)), self.assertRaises(SystemExit):
            self.ha.switchover(self.config, self.args)
        marker.assert_not_called()

    def test_repeat_after_credentials_verifies_without_marker_changes(self):
        statuses = [dict(reachable=True, active=True, role='active', active_box='boxb'),
                    dict(self.standby, active_box='boxb')]
        with patch.object(self.ha, 'controller_status', side_effect=statuses), \
                patch.object(self.ha, 'require_readiness'), \
                patch.object(self.ha, 'require_credentials_absent'), \
                patch.object(self.ha, 'set_marker') as marker, \
                patch.object(self.ha, 'verify_promotion') as verify:
            self.ha.switchover(self.config, self.args)
        marker.assert_not_called()
        verify.assert_called_once_with(self.config, 'boxb')

    def test_discovery_accepts_either_contact_but_refuses_zero_or_conflicting_sources(self):
        import subprocess
        other = dict(self.identity, controllers={'active': self.config['controllers']['boxa'],
                                                 'standby': self.config['controllers']['boxb']})
        def ok(value): return subprocess.CompletedProcess([], 0, json.dumps(value), '')
        unavailable = subprocess.CompletedProcess([], 1, '', 'unavailable')
        for replies, expected in (([unavailable, ok(self.identity)], 'boxb'),
                                  ([ok(other), unavailable], 'boxa'),
                                  ([unavailable, unavailable], None),
                                  ([ok(other), ok(self.identity)], None)):
            with self.subTest(expected=expected), patch.object(self.ha, 'run', side_effect=replies):
                if expected:
                    result = self.ha.source_config(self.config)
                    self.assertEqual(result['identity_status']['controllers']['active']['box'], expected)
                else:
                    with self.assertRaises(SystemExit): self.ha.source_config(self.config)


class InstanceGitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='controller-git-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.origin = self.root / 'origin'
        self.checkout = self.root / 'controller/instance'
        self.checkout.parent.mkdir()
        self.git('init', '--initial-branch=main', str(self.origin))
        self.git('-C', str(self.origin), 'config', 'user.name', 'Fixture')
        self.git('-C', str(self.origin), 'config', 'user.email', 'fixture@example.invalid')
        (self.origin / 'klokast-instance.json').write_text('{}')
        (self.origin / 'controller-only-secret').write_text('must never transfer')
        self.commit('initial')
        tasks = yaml.safe_load((ROOT / 'ansible/roles/ops-controller/tasks/instance.yml').read_text())
        block = next(task['block'] for task in tasks if 'block' in task)
        self.script = next(task['ansible.builtin.shell'] for task in block if 'ansible.builtin.shell' in task)

    def git(self, *args):
        return subprocess.run(['git', *args], capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, message):
        self.git('-C', str(self.origin), 'add', 'klokast-instance.json')
        self.git('-C', str(self.origin), 'commit', '-m', message)

    def converge(self):
        return subprocess.run(['sh', '-s'], input=self.script, text=True, capture_output=True,
                              env=dict(os.environ, INSTANCE_DIR=str(self.checkout),
                                       INSTANCE_ORIGIN=str(self.origin), GIT_SSH_COMMAND='ssh'))

    def test_clone_fetches_git_content_without_private_tree_or_git_copy(self):
        self.assertEqual(self.converge().returncode, 0)
        self.assertTrue((self.checkout / '.git').is_dir())
        self.assertFalse((self.checkout / 'controller-only-secret').exists())
        (self.origin / 'klokast-instance.json').write_text('{"new":true}')
        self.commit('update')
        self.assertEqual(self.converge().returncode, 0)
        self.assertEqual((self.checkout / 'klokast-instance.json').read_text(), '{"new":true}')
        self.assertEqual(self.git('-C', str(self.checkout), 'config', 'core.sshCommand'), 'ssh')

    def test_local_edits_and_divergent_commits_are_preserved(self):
        self.assertEqual(self.converge().returncode, 0)
        document = self.checkout / 'klokast-instance.json'
        document.write_text('{"local":true}')
        self.assertNotEqual(self.converge().returncode, 0)
        self.assertEqual(document.read_text(), '{"local":true}')
        self.git('-C', str(self.checkout), 'config', 'user.name', 'Fixture')
        self.git('-C', str(self.checkout), 'config', 'user.email', 'fixture@example.invalid')
        self.git('-C', str(self.checkout), 'add', '.')
        self.git('-C', str(self.checkout), 'commit', '-m', 'local')
        (self.origin / 'klokast-instance.json').write_text('{"upstream":true}')
        self.commit('upstream')
        head = self.git('-C', str(self.checkout), 'rev-parse', 'HEAD')
        self.assertNotEqual(self.converge().returncode, 0)
        self.assertEqual(self.git('-C', str(self.checkout), 'rev-parse', 'HEAD'), head)

    def test_frozen_replacement_source_survives_upstream_advance_but_not_local_drift(self):
        self.assertEqual(self.converge().returncode, 0)
        frozen = self.git('-C', str(self.checkout), 'rev-parse', 'HEAD')
        (self.origin / 'klokast-instance.json').write_text('{"upstream":true}')
        self.commit('new public generation')
        tasks = yaml.safe_load((ROOT / 'ansible/roles/ops-controller-verification/tasks/main.yml').read_text())
        task = next(t for t in tasks if t['name'] == 'Read ops controller repository state')
        script = task['ansible.builtin.command']['argv'][4]
        def check(expected):
            return subprocess.run(['sh', '-c', script, 'source-check', str(self.checkout), str(self.origin), expected],
                                  text=True, capture_output=True).returncode
        self.assertNotEqual(check(''), 0)
        self.assertEqual(check(frozen), 0)
        self.assertNotEqual(check('0' * 40), 0)
        (self.checkout / 'klokast-instance.json').write_text('{"dirty":true}')
        self.assertNotEqual(check(frozen), 0)


if __name__ == '__main__':
    unittest.main()
