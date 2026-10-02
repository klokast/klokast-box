"""Run the real peer selectors and direct-path assertions with synthetic IO."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('ansible-playbook'), 'requires controller Ansible')
class ReplacementIpv6Tests(unittest.TestCase):
    def exercise(self, *, failure=None, overlay=True):
        plays = yaml.safe_load((ROOT / 'ansible/playbooks/74-router-replacement-final-verify.yml').read_text())
        management = yaml.safe_load((ROOT / 'ansible/roles/router-management-target/tasks/main.yml').read_text())
        # Expand the existing role; retain its actual selection/identity checks.
        for index, task in enumerate(plays[0]['tasks']):
            if 'ansible.builtin.include_role' in task:
                plays[0]['tasks'][index:index+1] = [
                    {'ansible.builtin.set_fact': task['vars']}, *management]
                break
        identity = {'ID': 'new-device', 'HostName': 'peer-router-'+'a'*24,
                    'TailscaleIPs': ['100.64.0.2'], 'Online': failure != 'offline'}
        peer_map = {'action': 'map-status', 'box': 'peer', 'result': {
            'kind': 'klokast.router-map.v2', 'box': 'peer', 'pending': None,
            'current': {'record_sha256': 'a'*64, 'machine_id': identity['ID'],
                        'tailnet_hostname': identity['HostName']}}}
        after = copy.deepcopy(peer_map)
        if failure == 'generation-changed':
            after['result']['current']['record_sha256'] = 'b'*64
        if failure == 'pending':
            peer_map['result']['pending'] = {'phase': 'copying'}
        guest = dict(identity, ID='old-device') if failure == 'wrong-device' else identity
        direct = 'pong from peer (100.64.0.2) via [2001:db8::2]:41641 in 1.2ms'
        replies = {
            'router_replacement_peer_map': json.dumps(peer_map),
            'router_management_status': json.dumps({'BackendState': 'Running', 'Peer': {
                'old': {'ID': 'old-device', 'HostName': 'peer-router', 'Online': False,
                        'TailscaleIPs': ['100.64.0.1']}, 'new': identity}}),
            'router_management_guest': json.dumps({'BackendState': 'Running', 'Self': guest}),
            'router_replacement_peer_ipv6': '2001:db8::2',
            'router_replacement_direct_ping': direct,
            'router_replacement_ops_direct_ping': 'pong via DERP in 4ms' if failure == 'ops-relay' else direct,
            'router_replacement_peer_map_after': json.dumps(after),
            'router_replacement_peer_boot': json.dumps({'box': 'peer', 'result': 'accepted-assignment-verified'})}
        # Only run the connected IPv6 section of B's service play, plus its
        # final peer consistency checks. Other service checks have separate tests.
        plays[1].pop('pre_tasks')
        names = {'Require direct IPv6 UDP replies on both the router and ops paths',
                 'Refuse a peer generation or identity change during verification',
                 'Require the exact accepted peer boot proof'}
        plays[1]['tasks'] = [task for task in plays[1]['tasks']
                            if task.get('register') in replies or task['name'] in names]
        for play in plays:
            play['connection'] = 'local'
            for task in play['tasks']:
                register = task.get('register')
                if register not in replies:
                    continue
                original = task.get('ansible.builtin.command', {}).get('argv', [])
                if register in ('router_replacement_direct_ping', 'router_replacement_ops_direct_ping'):
                    # Exercise the production target expression; the synthetic
                    # transport refuses a legacy name or retained old address.
                    script = 'import sys; assert sys.argv[1] == "100.64.0.2"; print('+repr(replies[register])+')'
                    args = [original[-1]]
                elif register == 'router_replacement_peer_ipv6':
                    script = 'import sys; assert sys.argv[1] == "wan_peer"; print('+repr(replies[register])+')'
                    # Extract the actual interface expression from production.
                    expression = task['ansible.builtin.shell'].split('show dev ', 1)[1].split(' scope global', 1)[0]
                    args = [expression]
                else:
                    script, args = 'print('+repr(replies[register])+')', []
                for key in ('ansible.builtin.shell', 'args', 'delegate_to', 'become', 'become_method', 'no_log'):
                    task.pop(key, None)
                task['ansible.builtin.command'] = {'argv': [shutil.which('python3'), '-c', script, *args]}
        with tempfile.TemporaryDirectory(prefix='router-ipv6-test-') as temporary:
            work = Path(temporary)
            (work/'test.json').write_text(json.dumps(plays))
            (work/'inventory.json').write_text(json.dumps({'all': {'hosts': {
                'boxa-router': {'node_name': 'boxa', 'router_wan_interface': 'wan_candidate'},
                'peer-router': {'node_name': 'peer', 'router_wan_interface': 'wan_peer'}},
                'vars': {'ansible_connection': 'local', 'ansible_python_interpreter': shutil.which('python3'),
                         'router_replacement_box': 'boxa',
                         'router_replacement_overlay': {'peer_box': 'peer'} if overlay else None,
                         'router_replacement_address': '100.64.0.3',
                         'router_replacement_expected': {'hostname': 'boxa-router-'+'b'*24}}}}))
            (work/'ansible.cfg').write_text('[defaults]\nretry_files_enabled = false\n')
            (work/'extra.json').write_text(json.dumps({
                'router_replacement_box': 'boxa',
                'router_replacement_overlay': {'peer_box': 'peer'} if overlay else None}))
            result = subprocess.run(['ansible-playbook', '-i', str(work/'inventory.json'),
                str(work/'test.json'), '-e', '@'+str(work/'extra.json'), '--limit', 'boxa-router,peer-router' if overlay else 'boxa-router'],
                cwd=work, env=dict(os.environ, ANSIBLE_CONFIG=str(work/'ansible.cfg'),
                    ANSIBLE_LOCAL_TEMP=str(work/'local'), ANSIBLE_REMOTE_TEMP=str(work/'remote')),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertEqual(result.returncode == 0, failure is None, result.stdout)
            if failure is not None:
                expected_task = {
                    'offline': 'Require the recorded router to be online at one exact address',
                    'wrong-device': 'Require the selected connection to reach the recorded machine',
                    'pending': 'Require one accepted peer with a recorded identity and no pending cutover',
                    'generation-changed': 'Refuse a peer generation or identity change during verification',
                    'ops-relay': 'Require direct IPv6 UDP replies on both the router and ops paths'}[failure]
                self.assertIn('TASK ['+expected_task+']', result.stdout)
                self.assertIn('Task failed: Action failed:', result.stdout)


    def test_new_generation_and_peer_interface_on_router_and_ops_paths(self):
        self.exercise()

    def test_offline_accepted_device_is_not_replaced_by_hostname_guess(self):
        self.exercise(failure='offline')

    def test_guest_must_confirm_recorded_identity(self):
        self.exercise(failure='wrong-device')

    def test_pending_peer_is_refused(self):
        self.exercise(failure='pending')

    def test_changed_peer_cannot_issue_service_proof(self):
        self.exercise(failure='generation-changed')

    def test_router_direct_reply_cannot_substitute_for_ops_path(self):
        self.exercise(failure='ops-relay')

    def test_disabled_overlay_needs_no_peer(self):
        self.exercise(overlay=False)
